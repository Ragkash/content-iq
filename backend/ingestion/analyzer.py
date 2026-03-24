"""
ContentIQ — Ingestion: Document Analyzer
Sends a document (via Blob URL) to Azure Content Understanding and returns
structured content including markdown, tables, and figure descriptions.

Content Understanding REST API reference:
https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/
"""

import os
import json
import time
import logging
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

CU_ENDPOINT = os.getenv("AZURE_CU_ENDPOINT", "")
CU_KEY = os.getenv("AZURE_CU_KEY", "")

# API version for Content Understanding (GA as of Nov 2025)
CU_API_VERSION = "2024-12-01-preview"


def _get_headers() -> dict[str, str]:
    return {
        "Ocp-Apim-Subscription-Key": CU_KEY,
        "Content-Type": "application/json",
    }


def analyze_document(
    blob_url: str,
    timeout: int = 300,
    cache_dir: str | None = None,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """
    Submit a document URL to Azure Content Understanding and poll until the result
    is ready. Returns the full analysis result dict.

    Args:
        blob_url:  Public SAS URL to the document in Azure Blob Storage.
        timeout:   Max seconds to wait for the long-running operation (default 5 min).
        cache_dir: Directory to read/write cached CU results. Pass None to disable.
        cache_key: Filename stem for the cache entry (e.g. blob path with / → _).
                   Required when cache_dir is set.

    Returns:
        dict with keys: "markdown", "tables", "figures", "pages", "rawContent"
        - markdown: Full extracted text as structured Markdown
        - tables:   List of table objects with row/cell data
        - figures:  List of figure objects with captions/descriptions
        - pages:    Per-page breakdown with page numbers
    """
    # ── Cache read ────────────────────────────────────────────────────────────
    cache_path: Path | None = None
    if cache_dir and cache_key:
        safe_key = cache_key.replace("/", "_").replace("\\", "_")
        cache_path = Path(cache_dir) / f"{safe_key}.json"
        if cache_path.exists():
            logger.info("CU cache hit — loading from %s", cache_path)
            return json.loads(cache_path.read_text(encoding="utf-8"))
        logger.info("CU cache miss — will call API for %s", cache_key)

    if not CU_ENDPOINT or not CU_KEY:
        raise EnvironmentError(
            "AZURE_CU_ENDPOINT and AZURE_CU_KEY must be set in .env before calling analyze_document()"
        )

    submit_url = (
        f"{CU_ENDPOINT.rstrip('/')}/contentunderstanding/analyzers/prebuilt-layout"
        f":analyze?api-version={CU_API_VERSION}"
    )

    logger.info("Submitting document to Content Understanding: %s", blob_url)

    with httpx.Client(timeout=30) as client:
        # Submit the long-running analysis job
        response = client.post(
            submit_url,
            headers=_get_headers(),
            json={"url": blob_url},
        )
        response.raise_for_status()

        # CU returns 202 with an Operation-Location header for polling
        operation_url = response.headers.get("Operation-Location")
        if not operation_url:
            raise RuntimeError(
                f"Content Understanding did not return Operation-Location header. "
                f"Status: {response.status_code}, Body: {response.text[:500]}"
            )

    logger.info("Polling result at: %s", operation_url)

    # Poll until job completes
    poll_headers = {"Ocp-Apim-Subscription-Key": CU_KEY}
    start = time.time()
    poll_interval = 3  # seconds

    with httpx.Client(timeout=30) as client:
        while True:
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"Content Understanding analysis timed out after {timeout}s for: {blob_url}"
                )

            poll_resp = client.get(operation_url, headers=poll_headers)
            poll_resp.raise_for_status()
            result_json = poll_resp.json()

            status = result_json.get("status", "").lower()
            logger.debug("Poll status: %s", status)

            if status == "succeeded":
                logger.info("Analysis complete for: %s", blob_url)
                result = _extract_result(result_json)
                # ── Cache write ───────────────────────────────────────────────
                if cache_path is not None:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(result), encoding="utf-8")
                    logger.info("CU result cached at %s", cache_path)
                return result
            elif status == "failed":
                error = result_json.get("error", {})
                raise RuntimeError(
                    f"Content Understanding analysis failed: {error.get('message', result_json)}"
                )

            time.sleep(poll_interval)


def _extract_result(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise the CU API response into the shape ContentIQ expects.

    Verified response shape for prebuilt-layout (2024-12-01-preview):
    {
      "status": "Succeeded",
      "result": {
        "contents": [
          {
            "markdown": "<full document as markdown string>",
            "pages":    [{"pageNumber": 1, "words": [...], "lines": [...], ...}],
            "tables":   [{"rowCount": N, "columnCount": N, "cells": [...], ...}],
            "figures":  [{"id": "...", "source": {...}, "span": {...}, "elements": [...]}]
          }
        ]
      }
    }
    Note: figures do NOT have a 'caption' field in this API version —
    the chunker's _process_figures will skip captionless figures gracefully.
    """
    result = raw.get("result", {})
    contents = result.get("contents", [])
    doc = contents[0] if contents else {}

    return {
        "markdown": doc.get("markdown", ""),
        "tables": doc.get("tables", []),
        "figures": doc.get("figures", []),
        "pages": doc.get("pages", []),
        "rawContent": result,
    }


def extract_text_pypdf(
    blob_path: str,
    connection_string: str,
    container_name: str,
) -> dict[str, Any]:
    """
    Extract text from a PDF using pypdf — no Azure Content Understanding required.

    Downloads the blob via the Azure Storage SDK (works for private containers),
    reads each page with pypdf, and returns a CU-compatible result dict.

    The returned markdown embeds  <!-- PageNumber="N" -->  markers where N is the
    1-based physical page position.  Because the ``pages`` array is left empty,
    chunker._build_physical_page_map() returns [] and _split_by_pages() falls back
    to  phys = int(match.group(1))  — which is exactly the physical page we set.

    Args:
        blob_path:         Path within the container, e.g. "indigo/IndiGo_AR.pdf"
        connection_string: Azure Storage connection string (from .env)
        container_name:    Blob container name (from .env)

    Returns:
        dict with keys: "markdown", "tables", "figures", "pages", "rawContent"
        Compatible with chunk_document() in chunker.py.

    Limitations:
        - Only works for PDFs with embedded text (i.e. not scanned image-only PDFs).
        - Tables, figures, and captions are not extracted — CU is still better for those.
    """
    try:
        import pypdf  # noqa: PLC0415
    except ImportError as e:
        raise ImportError(
            "pypdf is required for --no-cu mode. Run: pip install pypdf>=4.0.0"
        ) from e

    import io
    from azure.storage.blob import BlobServiceClient

    logger.info("Downloading blob for pypdf extraction: %s", blob_path)
    blob_client = (
        BlobServiceClient.from_connection_string(connection_string)
        .get_container_client(container_name)
        .get_blob_client(blob_path)
    )
    blob_data = blob_client.download_blob().readall()

    reader = pypdf.PdfReader(io.BytesIO(blob_data))
    total_pages = len(reader.pages)
    logger.info("  pypdf: %d pages found in %s", total_pages, blob_path)

    markdown_parts: list[str] = []
    empty_pages = 0
    for page_idx, page in enumerate(reader.pages):
        phys_page = page_idx + 1  # 1-based physical page number
        text = page.extract_text() or ""
        if not text.strip():
            empty_pages += 1
            continue
        markdown_parts.append(f'<!-- PageNumber="{phys_page}" -->\n{text}')

    if empty_pages:
        logger.warning(
            "  %d/%d pages had no extractable text (may be scanned images — "
            "use CU for full accuracy)",
            empty_pages, total_pages,
        )

    return {
        "markdown": "\n".join(markdown_parts),
        "tables": [],
        "figures": [],
        "pages": [],       # empty → chunker falls back to logical label = physical
        "rawContent": {},
    }


# ─── CLI test helper ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <blob_url>")
        sys.exit(1)

    result = analyze_document(sys.argv[1])
    print("=== Markdown (first 1000 chars) ===")
    print(result["markdown"][:1000])
    print(f"\n=== Tables: {len(result['tables'])} found ===")
    print(f"=== Figures: {len(result['figures'])} found ===")
    print(f"=== Pages: {len(result['pages'])} found ===")
