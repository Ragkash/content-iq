"""
ContentIQ — Ingestion: Document Chunker
Splits analyzed document content into overlapping chunks and attaches
all required ContentIQ metadata fields to every chunk.
"""

import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

# ── Chunking config ──────────────────────────────────────────────────────────
CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50
ENCODING_MODEL = "cl100k_base"  # used by text-embedding-ada-002 & GPT-4o


def _get_encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding(ENCODING_MODEL)


def _extract_customer_tag(file_path: str) -> str:
    """
    Derive the customer tag from the Blob folder path.

    Convention:
        customers/Shell/shell_proposal_q4.pdf  →  "Shell"
        customers/BP/bp_brief.pdf              →  "BP"
        internal/general/trends.pdf            →  "internal"
        anything else                          →  "unknown"

    Examples
    --------
    >>> _extract_customer_tag("customers/Shell/file.pdf")
    'Shell'
    >>> _extract_customer_tag("internal/general/file.pdf")
    'internal'
    """
    parts = file_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "customers":
        return parts[1]  # e.g. "Shell" or "BP"
    if len(parts) >= 1 and parts[0].lower() == "internal":
        return "internal"
    return "unknown"


def _extract_document_title(file_path: str) -> str:
    """Return just the filename without the folder prefix."""
    return file_path.replace("\\", "/").split("/")[-1]


def _split_into_chunks(text: str, size: int, overlap: int) -> list[str]:
    """
    Split text into overlapping token-based chunks.
    Returns list of text strings — each ≤ `size` tokens.
    """
    enc = _get_encoder()
    tokens = enc.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += size - overlap
    return chunks


def _get_page_number_for_chunk(chunk_text: str, pages: list[dict]) -> int | None:
    """
    Heuristically match a chunk's text to the page it came from.
    Returns 1-based page number or None if not determinable.
    CU pages list contains {"pageNumber": int, "content": str, ...}
    """
    if not pages:
        return None
    for page in pages:
        page_content = page.get("content", "")
        # Use first 100 chars of chunk as fingerprint
        sample = chunk_text[:100].strip()
        if sample and sample in page_content:
            return page.get("pageNumber", 1)
    return 1  # default to first page if not found


def _process_figures(figures: list[dict], source_url: str, file_path: str,
                     blob_metadata: dict | None) -> list[dict]:
    """
    Convert each figure/chart from CU output into a standalone ContentIQ chunk
    with content_type="chart" or "image".
    """
    customer_tag = _extract_customer_tag(file_path)
    document_title = _extract_document_title(file_path)
    now = datetime.now(timezone.utc).isoformat()

    figure_chunks = []
    for idx, fig in enumerate(figures):
        # CU figure format: {"caption": {...}, "boundingRegions": [...], ...}
        caption_obj = fig.get("caption", {})
        caption_text = caption_obj.get("content", "") if isinstance(caption_obj, dict) else str(caption_obj)

        # Determine content type
        caption_lower = caption_text.lower()
        content_type = "chart" if any(w in caption_lower for w in ["chart", "graph", "bar", "pie", "trend"]) else "image"

        # Get page from bounding regions
        bounding_regions = fig.get("boundingRegions", [])
        page_num = bounding_regions[0].get("pageNumber", 1) if bounding_regions else 1

        if not caption_text:
            continue  # skip figures with no caption

        figure_chunks.append({
            "id": str(uuid.uuid4()),
            "content": caption_text,
            "content_vector": [],           # filled by embedder.py
            "document_title": document_title,
            "source_url": source_url,
            "page_number": page_num,
            "slide_number": None,
            "content_type": content_type,
            "customer_tag": customer_tag,
            "author": (blob_metadata or {}).get("author", ""),
            "created_date": (blob_metadata or {}).get("created_date", now),
            "last_modified_date": (blob_metadata or {}).get("last_modified_date", now),
            "chunk_index": idx,
            "extracted_caption": caption_text,
            "allowed_groups": ["all"],      # v1: everyone, v2: per-user groups
        })
    return figure_chunks


def chunk_document(
    result: dict[str, Any],
    blob_url: str,
    file_path: str,
    blob_metadata: dict | None = None,
) -> list[dict[str, Any]]:
    """
    Split a Content Understanding analysis result into ContentIQ index chunks.

    Args:
        result:        Output of analyzer.analyze_document()
        blob_url:      Direct SAS URL to the file in Azure Blob Storage (becomes source_url)
        file_path:     Blob path like "customers/Shell/file.pdf" — used for customer_tag
        blob_metadata: Optional dict with keys: author, created_date, last_modified_date

    Returns:
        List of chunk dicts, each ready to be embedded and uploaded to AI Search.
        Every chunk has all fields from the ContentIQ metadata schema.
    """
    markdown_text = result.get("markdown", "")
    pages = result.get("pages", [])
    figures = result.get("figures", [])

    customer_tag = _extract_customer_tag(file_path)
    document_title = _extract_document_title(file_path)
    now = datetime.now(timezone.utc).isoformat()

    author = (blob_metadata or {}).get("author", "")
    created_date = (blob_metadata or {}).get("created_date", now)
    last_modified_date = (blob_metadata or {}).get("last_modified_date", now)

    logger.info(
        "Chunking '%s' | customer_tag='%s' | markdown_len=%d chars",
        document_title, customer_tag, len(markdown_text)
    )

    # ── Split main text into chunks ──────────────────────────────────────────
    text_strings = _split_into_chunks(markdown_text, CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS)
    chunks: list[dict[str, Any]] = []

    for idx, chunk_text in enumerate(text_strings):
        if not chunk_text.strip():
            continue

        page_num = _get_page_number_for_chunk(chunk_text, pages)

        chunks.append({
            "id": str(uuid.uuid4()),
            "content": chunk_text,
            "content_vector": [],           # filled by embedder.py
            "document_title": document_title,
            "source_url": blob_url,
            "page_number": page_num,
            "slide_number": None,           # populated if PPTX (see slide detection below)
            "content_type": "text",
            "customer_tag": customer_tag,
            "author": author,
            "created_date": created_date,
            "last_modified_date": last_modified_date,
            "chunk_index": idx,
            "extracted_caption": "",
            "allowed_groups": ["all"],
        })

    # ── PPTX: attempt slide number detection ────────────────────────────────
    # CU extracts slides as pages; slide_number mirrors page_number for PPTX
    if file_path.lower().endswith(".pptx"):
        for chunk in chunks:
            chunk["slide_number"] = chunk["page_number"]

    # ── Figure / chart chunks ────────────────────────────────────────────────
    figure_chunks = _process_figures(figures, blob_url, file_path, blob_metadata)
    chunks.extend(figure_chunks)

    logger.info(
        "Produced %d text chunks + %d figure chunks for '%s'",
        len(chunks) - len(figure_chunks), len(figure_chunks), document_title
    )
    return chunks


# ─── CLI test helper ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json
    from analyzer import analyze_document

    if len(sys.argv) < 3:
        print("Usage: python chunker.py <blob_url> <file_path>")
        print("  e.g: python chunker.py https://... customers/Shell/proposal.pdf")
        sys.exit(1)

    result = analyze_document(sys.argv[1])
    chunks = chunk_document(result, sys.argv[1], sys.argv[2])

    print(f"\nTotal chunks: {len(chunks)}")
    for i, c in enumerate(chunks[:3]):
        print(f"\n── Chunk {i} ──────────────────────────────────")
        print(f"  id:            {c['id']}")
        print(f"  customer_tag:  {c['customer_tag']}")
        print(f"  content_type:  {c['content_type']}")
        print(f"  page_number:   {c['page_number']}")
        print(f"  content[:200]: {c['content'][:200]}")
