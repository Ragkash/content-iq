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
from bs4 import BeautifulSoup

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
        indigo/file.pdf                        →  "indigo"
        air_india/file.pdf                     →  "air_india"
        customers/Shell/shell_proposal_q4.pdf  →  "Shell"
        internal/general/trends.pdf            →  "internal"
        file.pdf (no folder)                   →  "unknown"

    The first folder segment is always treated as the client name,
    except for the legacy "customers/<name>/" prefix which returns <name>,
    and "internal/" which returns "internal".
    """
    parts = file_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "customers":
        return parts[1]  # legacy: customers/Shell/file.pdf → "Shell"
    if len(parts) >= 1 and parts[0].lower() == "internal":
        return "internal"
    if len(parts) >= 2:
        return parts[0]  # direct: indigo/file.pdf → "indigo"
    return "unknown"


def _extract_document_title(file_path: str) -> str:
    """Return just the filename without the folder prefix."""
    return file_path.replace("\\", "/").split("/")[-1]


_PAGE_SPLIT_RE = re.compile(r'<!--\s*PageNumber="(\d+)"\s*-->')


def _build_physical_page_map(cu_pages: list[dict]) -> list[tuple[int, int]]:
    """
    Build a sorted lookup table: [(physical_page_number, markdown_start_offset), ...]

    CU's pages array has sequential physical page numbers (1, 2, 3...) and each
    page's lines carry markdown character-offset spans. The minimum span offset
    among all lines on a page is where that page's content begins in the markdown.

    This map is used to resolve the correct physical page number for each chunk,
    bypassing the PDF's logical page labels that CU inserts into <!-- PageNumber -->
    markers (which can be non-sequential, e.g. "7", "02", "03"... for PDFs with
    custom page label dictionaries like annual reports).
    """
    entries: list[tuple[int, int]] = []
    for page in cu_pages:
        phys = page.get("pageNumber")
        lines = page.get("lines", [])
        if not phys or not lines:
            continue
        offsets = [
            line["span"]["offset"]
            for line in lines
            if isinstance(line.get("span"), dict) and "offset" in line["span"]
        ]
        if offsets:
            entries.append((phys, min(offsets)))
    entries.sort(key=lambda x: x[1])
    return entries


def _lookup_physical_page(markdown_offset: int, page_map: list[tuple[int, int]]) -> int:
    """
    Binary search: return the physical page number whose content starts at or
    before `markdown_offset`. Falls back to page 1 if map is empty.
    """
    if not page_map:
        return 1
    lo, hi = 0, len(page_map) - 1
    result = page_map[0][0]
    while lo <= hi:
        mid = (lo + hi) // 2
        if page_map[mid][1] <= markdown_offset:
            result = page_map[mid][0]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def _split_by_pages(
    markdown: str,
    page_map: list[tuple[int, int]] | None = None,
) -> list[tuple[int, str]]:
    """
    Split CU markdown into (physical_page_number, raw_page_text) pairs.

    Uses re.finditer on PageNumber markers to track each segment's character
    offset in the original markdown, then resolves the physical page via
    page_map (built from CU's pages array).  Falls back to marker label
    integers when no map is available (standard PDFs with sequential labels).
    """
    markers = list(_PAGE_SPLIT_RE.finditer(markdown))
    result: list[tuple[int, str]] = []

    # Content before the first marker = physical page 1 (or whatever page_map says)
    first_start = markers[0].start() if markers else len(markdown)
    pre_content = markdown[:first_start]
    if pre_content.strip():
        phys = _lookup_physical_page(0, page_map) if page_map else 1
        result.append((phys, pre_content))

    # Regex to skip leading CU structural markers (PageBreak, PageHeader, etc.)
    # and whitespace so the lookup offset lands on actual content, not on the
    # gap between the PageNumber marker and the first real text on that page.
    _LEAD_HEADER_RE = re.compile(r'^[\s\n]*(?:<!--[^>]*-->[\s\n]*)*')

    for i, match in enumerate(markers):
        seg_start = match.end()
        seg_end = markers[i + 1].start() if i + 1 < len(markers) else len(markdown)
        content = markdown[seg_start:seg_end]
        if not content.strip():
            continue
        if page_map:
            # Skip structural headers at the segment start so the lookup hits
            # actual content, which is reliably within the correct physical page.
            lead = _LEAD_HEADER_RE.match(content)
            content_offset = seg_start + (lead.end() if lead else 0)
            phys = _lookup_physical_page(content_offset, page_map)
        else:
            phys = int(match.group(1))  # fallback: use logical label
        result.append((phys, content))

    return result


def _split_page_into_chunks(text: str, size: int, overlap: int) -> list[str]:
    """
    Split a single page's text into token-bounded sub-chunks.
    Overlap within a page is harmless — all sub-chunks share the same page number.
    Returns the text as-is when it fits within `size` tokens.
    """
    enc = _get_encoder()
    tokens = enc.encode(text)

    if len(tokens) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        chunks.append(enc.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += size - overlap
    return chunks


_CU_ARTIFACT_RE = re.compile(
    r'<!--\s*Page(?:Header|Footer|Break|Number)[^>]*-->'   # structural HTML comments
    r'|<figure[^>]*>.*?</figure>'                           # figure blocks (handled separately)
    r'|<figcaption[^>]*>.*?</figcaption>',                  # figure captions inside figure blocks
    re.DOTALL | re.IGNORECASE,
)


def _strip_html_to_text(content: str) -> str:
    """
    Convert HTML table markup to pipe-delimited plain text before indexing.

    Table rows become:
        Header1 | Header2 | Header3
        val1    | val2    | val3

    Cells with rowspan/colspan are included as-is (BeautifulSoup reads their
    text regardless of span attributes). Remaining non-table HTML tags are
    stripped so only clean text is stored in the index.

    Returns content unchanged when no HTML tags are present (fast path).
    """
    if "<" not in content:
        return content

    soup = BeautifulSoup(content, "html.parser")

    for table in soup.find_all("table"):
        rows_text = []
        for row in table.find_all("tr"):
            cells = [
                cell.get_text(separator=" ", strip=True)
                for cell in row.find_all(["th", "td"])
            ]
            if cells:
                rows_text.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows_text) + "\n")

    text = soup.get_text(separator="\n")
    text = re.sub(r'\|\s*\|', '|', text)           # collapse empty pipe cells
    text = re.sub(r'^\s*\|\s*', '', text, flags=re.MULTILINE)   # strip leading pipes
    text = re.sub(r'\s*\|\s*$', '', text, flags=re.MULTILINE)   # strip trailing pipes
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_markdown(markdown: str) -> str:
    """
    Strip CU structural annotations from the markdown before chunking.

    Removes:
    - <!-- PageHeader="..." -->  (repeated page headers pollute every chunk)
    - <!-- PageFooter="..." -->  (repeated footers)
    - <!-- PageBreak -->         (layout-only marker)
    - <!-- PageNumber="N" -->    (we use this for attribution but don't want it in embeddings)
    - <figure>...</figure>       (figure content is handled via _process_figures)
    - <figcaption>...</figcaption>

    NOTE: PageNumber comments are stripped from the TEXT that goes into content/embeddings,
    but _get_page_number_for_chunk reads them BEFORE cleaning, so attribution is preserved.
    """
    return _CU_ARTIFACT_RE.sub(" ", markdown).strip()


def _follow_element_path(path: str, raw_doc: dict) -> str:
    """
    Traverse a CU element path like '/sections/0/paragraphs/3' into raw_doc
    (the first entry of result["contents"]) and return its 'content' string.
    Returns "" on any traversal failure.
    """
    try:
        obj: Any = raw_doc
        for part in path.strip("/").split("/"):
            obj = obj[int(part)] if part.isdigit() else obj[part]
        if isinstance(obj, dict):
            return obj.get("content", "")
        return str(obj) if obj else ""
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_figure_content(fig: dict, markdown: str, raw_doc: dict) -> str:
    """
    Extract text content for a CU figure using three fallback strategies:

    1. caption.content  — present in future API versions / some analyzers
    2. span offset      — slice the markdown string using the figure's character span
    3. elements paths   — follow each "/sections/N/paragraphs/M" path into raw_doc

    Returns "" only when all three strategies yield nothing.
    """
    # Strategy 1: caption field
    caption_obj = fig.get("caption", {})
    if caption_obj:
        caption_text = caption_obj.get("content", "") if isinstance(caption_obj, dict) else str(caption_obj)
        if caption_text.strip():
            return caption_text.strip()

    # Strategy 2: span offset in the markdown string
    span = fig.get("span", {})
    if span and markdown:
        offset = span.get("offset", 0)
        length = span.get("length", 0)
        if length > 0:
            extracted = markdown[offset: offset + length].strip()
            if extracted:
                return extracted

    # Strategy 3: follow element paths into the raw document object
    elements = fig.get("elements", [])
    if elements and raw_doc:
        texts = [_follow_element_path(p, raw_doc) for p in elements]
        combined = " ".join(t for t in texts if t).strip()
        if combined:
            return combined

    return ""


def _process_figures(
    figures: list[dict],
    markdown: str,
    raw_doc: dict,
    source_url: str,
    file_path: str,
    blob_metadata: dict | None,
) -> list[dict]:
    """
    Convert each figure/chart from CU output into a standalone ContentIQ chunk
    with content_type="chart" or "image".

    Previously this skipped all captionless figures — but the 2024-12-01-preview
    API does not return caption fields at all, so every figure was silently
    dropped.  We now use span offsets and element paths as fallbacks so that
    charts, infographics, and image-embedded text are actually indexed.
    """
    customer_tag = _extract_customer_tag(file_path)
    document_title = _extract_document_title(file_path)
    now = datetime.now(timezone.utc).isoformat()

    figure_chunks = []
    for idx, fig in enumerate(figures):
        content_text = _extract_figure_content(fig, markdown, raw_doc)

        if not content_text:
            logger.debug("Figure %d has no extractable text — skipping", idx)
            continue

        # Determine content type from the extracted text
        content_lower = content_text.lower()
        content_type = (
            "chart"
            if any(w in content_lower for w in ["chart", "graph", "bar", "pie", "trend", "figure"])
            else "image"
        )

        # Get page from bounding regions
        bounding_regions = fig.get("boundingRegions", [])
        page_num = bounding_regions[0].get("pageNumber", 1) if bounding_regions else 1

        # caption text for the extracted_caption field (may be empty)
        caption_obj = fig.get("caption", {})
        caption_text = (
            caption_obj.get("content", "") if isinstance(caption_obj, dict) else str(caption_obj)
        )

        figure_chunks.append({
            "id": str(uuid.uuid4()),
            "content": content_text,
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
            "allowed_groups": ["all"],
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
    # raw_doc is the first entry in result["contents"] — used for element path traversal
    raw_content = result.get("rawContent", {})
    raw_doc = raw_content.get("contents", [{}])[0] if raw_content else {}

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

    # ── Page-aware chunking ───────────────────────────────────────────────────
    # Build a physical-page map from CU's pages array so that each chunk gets
    # the true physical page number (1-based position in the PDF file) rather
    # than the PDF's logical page label that CU puts in <!-- PageNumber --> markers.
    # Physical page numbers are what browser #page=N fragments require.
    page_map = _build_physical_page_map(pages)

    # Split the raw markdown on PageNumber markers (hard page boundaries), then
    # split within each page if it exceeds CHUNK_SIZE_TOKENS.
    # Every sub-chunk inherits its page's number exactly — no guessing needed.
    page_segments = _split_by_pages(markdown_text, page_map)
    chunks: list[dict[str, Any]] = []
    chunk_idx = 0

    for page_num, raw_page_text in page_segments:
        page_text = _clean_markdown(raw_page_text)
        page_text = _strip_html_to_text(page_text)
        if not page_text.strip():
            continue

        sub_chunks = _split_page_into_chunks(page_text, CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS)
        for sub_chunk in sub_chunks:
            if not sub_chunk.strip():
                continue
            chunks.append({
                "id": str(uuid.uuid4()),
                "content": sub_chunk,
                "content_vector": [],           # filled by embedder.py
                "document_title": document_title,
                "source_url": blob_url,
                "page_number": page_num,        # guaranteed correct — no cross-page bleed
                "slide_number": None,           # populated if PPTX (see slide detection below)
                "content_type": "text",
                "customer_tag": customer_tag,
                "author": author,
                "created_date": created_date,
                "last_modified_date": last_modified_date,
                "chunk_index": chunk_idx,
                "extracted_caption": "",
                "allowed_groups": ["all"],
            })
            chunk_idx += 1

    # ── PPTX: attempt slide number detection ────────────────────────────────
    # CU extracts slides as pages; slide_number mirrors page_number for PPTX
    if file_path.lower().endswith(".pptx"):
        for chunk in chunks:
            chunk["slide_number"] = chunk["page_number"]

    # ── Figure / chart chunks ────────────────────────────────────────────────
    figure_chunks = _process_figures(figures, markdown_text, raw_doc, blob_url, file_path, blob_metadata)
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
