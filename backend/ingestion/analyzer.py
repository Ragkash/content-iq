"""
ContentIQ — Ingestion: Document Analyzer
Sends a document (via Blob URL) to Azure Content Understanding and returns
structured content including markdown, tables, and figure descriptions.

Content Understanding REST API reference:
https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/
"""

import os
import time
import logging
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


def analyze_document(blob_url: str, timeout: int = 300) -> dict[str, Any]:
    """
    Submit a document URL to Azure Content Understanding and poll until the result
    is ready. Returns the full analysis result dict.

    Args:
        blob_url: Public SAS URL to the document in Azure Blob Storage.
        timeout:  Max seconds to wait for the long-running operation (default 5 min).

    Returns:
        dict with keys: "markdown", "tables", "figures", "pages", "rawContent"
        - markdown: Full extracted text as structured Markdown
        - tables:   List of table objects with row/cell data
        - figures:  List of figure objects with captions/descriptions
        - pages:    Per-page breakdown with page numbers
    """
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
                return _extract_result(result_json)
            elif status == "failed":
                error = result_json.get("error", {})
                raise RuntimeError(
                    f"Content Understanding analysis failed: {error.get('message', result_json)}"
                )

            time.sleep(poll_interval)


def _extract_result(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise the CU API response into the shape ContentIQ expects.
    The actual field names depend on the API version — this handles the
    prebuilt-layout analyser output format.

    # TODO: verify shape — expected raw response from prebuilt-layout analyser:
    # {
    #   "status": "succeeded",
    #   "analyzeResult": {
    #     "content": "<full markdown string>",     → mapped to 'markdown'
    #     "pages": [
    #       {"pageNumber": 1, "content": "...", "lines": [...], "words": [...]}
    #     ],
    #     "tables": [
    #       {"rowCount": 3, "columnCount": 2, "cells": [...]}
    #     ],
    #     "figures": [
    #       {
    #         "caption": {"content": "Revenue Chart Q4"},
    #         "boundingRegions": [{"pageNumber": 2, "polygon": [...]}]
    #       }
    #     ]
    #   }
    # }
    #
    # IMPORTANT: This is the 2024-12-01-preview shape.
    # If you upgrade the API version, re-check:
    #   1. Is the result under 'analyzeResult' or at the top level?
    #   2. Is full text under 'content' (string) or 'markdown' (string)?
    #   3. Do figures have a 'caption' dict or a 'description' string?
    # When credentials are available, add: print(json.dumps(raw, indent=2)[:2000])
    # in the 'succeeded' branch of analyze_document() to inspect the real shape.
    """
    # The result is nested under analyzeResult
    analyze_result = raw.get("analyzeResult", raw)

    return {
        "markdown": analyze_result.get("content", ""),
        "tables": analyze_result.get("tables", []),
        "figures": analyze_result.get("figures", []),
        "pages": analyze_result.get("pages", []),
        "rawContent": analyze_result,
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
