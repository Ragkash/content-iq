"""
ContentIQ — Substack Scraper
Scrapes all posts from sandeepalur.substack.com, saves each as a JSON file in
Azure Blob Storage (for audit/visibility), then chunks + embeds + uploads to
Azure AI Search.

Usage:
  python substack_scraper.py              # ingest all new posts
  python substack_scraper.py --dry-run    # scrape + save JSON to Blob, skip AI Search
  python substack_scraper.py --reset      # clear state and reingest every post from scratch
"""

import os
import sys
import json
import uuid
import logging
import argparse
import re
from datetime import datetime, timezone

import httpx
import tiktoken
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient, ContentSettings
from rich.console import Console
from rich.logging import RichHandler

# ── Path setup ────────────────────────────────────────────────────────────────
# embedder.py and uploader.py are copied into this folder for Azure deployment
_SCRAPER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRAPER_DIR)

from embedder import embed_batch       # scraper/embedder.py
from uploader import upload_chunks     # scraper/uploader.py

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()  # loads .env locally; in Azure env vars come from App Settings

SUBSTACK_BASE          = "https://sandeepalur.substack.com"
SUBSTACK_RSS_URL       = f"{SUBSTACK_BASE}/feed"
SUBSTACK_API_SLUG_URL  = f"{SUBSTACK_BASE}/api/v1/posts/by-slug"   # fallback per-post
RSS2JSON_API           = "https://api.rss2json.com/v1/api.json"     # proxy — avoids Azure IP block

BLOB_CONNECTION_STR  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
BLOB_CONTAINER       = os.getenv("RAW_DOCUMENT_CONTAINER", "container")
BLOB_PREFIX          = "sandeep-alur"
STATE_BLOB_PATH      = f"{BLOB_PREFIX}/state.json"
LOG_BLOB_PATH        = f"{BLOB_PREFIX}/run_log.json"

CHUNK_SIZE_TOKENS    = 500
CHUNK_OVERLAP_TOKENS = 50
ENCODING_MODEL       = "cl100k_base"

# ── Logging ───────────────────────────────────────────────────────────────────
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, markup=True, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)


# ── Blob helpers ──────────────────────────────────────────────────────────────

def _get_blob_service() -> BlobServiceClient:
    if not BLOB_CONNECTION_STR:
        raise EnvironmentError("AZURE_STORAGE_CONNECTION_STRING must be set in .env")
    return BlobServiceClient.from_connection_string(BLOB_CONNECTION_STR)


def _read_blob_json(blob_service: BlobServiceClient, blob_path: str):
    """Read a JSON blob; return None if it doesn't exist."""
    try:
        blob = blob_service.get_blob_client(container=BLOB_CONTAINER, blob=blob_path)
        data = blob.download_blob().readall()
        return json.loads(data)
    except Exception:
        return None


def _write_blob_json(blob_service: BlobServiceClient, blob_path: str, data) -> None:
    """Write a JSON-serialisable object to Blob Storage (overwrites if exists)."""
    blob = blob_service.get_blob_client(container=BLOB_CONTAINER, blob=blob_path)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    blob.upload_blob(
        payload.encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    logger.debug("Blob written: %s", blob_path)


# ── Title normalisation ───────────────────────────────────────────────────────

def _clean_title(title: str) -> str:
    """
    Normalise a post title from the Substack API:
    - Strip leading/trailing whitespace
    - Remove stray unpaired quote marks at the very start or end
      (artifact from some Substack HTML encodings, e.g. 'Workforce"' → 'Workforce')
    """
    title = title.strip()
    # Remove a lone trailing quote that has no matching opening quote
    title = re.sub(r'(?<!\w)"$', "", title).strip()
    # Remove a lone leading quote that has no matching closing quote
    title = re.sub(r'^"(?!\w)', "", title).strip()
    return title


# ── Date normalisation ───────────────────────────────────────────────────────

def _parse_date(date_str: str, fallback: str) -> str:
    """
    Convert any date string to ISO 8601 with UTC timezone (Edm.DateTimeOffset).
    rss2json returns dates as "2026-04-13 01:01:17" (space, no tz) which Azure
    AI Search rejects. This converts them to "2026-04-13T01:01:17+00:00".
    """
    if not date_str:
        return fallback
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    return date_str  # already ISO format, return as-is


# ── Substack API + scraping ───────────────────────────────────────────────────

def fetch_all_posts() -> list[dict]:
    """
    Fetch all posts via rss2json.com, which proxies the Substack RSS feed.
    Direct requests to sandeepalur.substack.com are blocked from Azure datacenter IPs
    by Cloudflare — routing through rss2json bypasses this.
    Returns list of dicts: { title, url, slug, published_date, subtitle }
    """
    logger.info("Fetching all posts via rss2json proxy...")

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(RSS2JSON_API, params={"rss_url": SUBSTACK_RSS_URL})
        resp.raise_for_status()

    data = resp.json()
    if data.get("status") != "ok":
        raise ValueError(f"rss2json error: {data.get('message', 'unknown')}")

    all_posts: list[dict] = []
    for item in data.get("items", []):
        url  = (item.get("link") or "").rstrip("/")
        slug = url.split("/p/")[-1] if "/p/" in url else ""
        desc = BeautifulSoup(item.get("description") or "", "html.parser").get_text(strip=True)[:300]
        # rss2json returns full article HTML in 'content' — extract plain text here
        raw_content = BeautifulSoup(item.get("content") or "", "html.parser").get_text(separator="\n", strip=True)
        if url:
            all_posts.append({
                "title"          : _clean_title(item.get("title") or "Untitled"),
                "url"            : url,
                "slug"           : slug,
                "published_date" : item.get("pubDate") or "",
                "subtitle"       : desc,
                "content"        : raw_content,   # pre-fetched — no HTML scraping needed
            })

    logger.info("Total posts discovered: %d", len(all_posts))
    return all_posts


def _scrape_html(url: str) -> tuple[str, str]:
    """
    Attempt to extract post body from the HTML page.
    Returns (content, failure_reason). failure_reason is "" on success.
    """
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        for selector in [
            "div.body.markup",
            "div.post-content",
            "div[class*='body']",
            "article",
        ]:
            body = soup.select_one(selector)
            if body:
                text = body.get_text(separator="\n", strip=True)
                if text:
                    return text, ""

        # Last resort: all <p> tags
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        if text:
            return text, ""

        return "", "no matching selector found and no <p> tags with content"

    except httpx.HTTPStatusError as exc:
        return "", f"HTTP {exc.response.status_code} on page fetch"
    except Exception as exc:
        return "", f"exception during HTML scrape: {exc}"


def _fetch_via_api(slug: str) -> tuple[str, str]:
    """
    Fallback: fetch post content via the per-slug Substack API endpoint.
    Returns (content, failure_reason). failure_reason is "" on success.
    """
    try:
        url = f"{SUBSTACK_API_SLUG_URL}/{slug}"
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
            resp.raise_for_status()

        data      = resp.json()
        body_html = data.get("body_html", "")
        if not body_html:
            return "", "API returned no body_html field"

        text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n", strip=True)
        if text:
            return text, ""

        return "", "body_html parsed to empty string"

    except httpx.HTTPStatusError as exc:
        return "", f"HTTP {exc.response.status_code} on API fallback"
    except Exception as exc:
        return "", f"exception during API fallback: {exc}"


def get_post_content(url: str, slug: str) -> tuple[str, str]:
    """
    Try HTML scraping first; if that yields nothing, fall back to the
    per-slug Substack API endpoint.
    Returns (content, failure_reason). failure_reason is "" on success.
    """
    content, reason = _scrape_html(url)
    if content:
        return content, ""

    logger.info("  HTML scraping failed (%s) — trying API fallback...", reason)
    content, api_reason = _fetch_via_api(slug)
    if content:
        return content, ""

    return "", f"HTML: {reason} | API: {api_reason}"


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_post(post: dict) -> list[dict]:
    """
    Split a scraped post's content into ContentIQ-compatible chunks.
    Mirrors the metadata schema used by chunker.py so AI Search treats
    these chunks identically to PDF/PPTX-sourced content.
    """
    enc    = tiktoken.get_encoding(ENCODING_MODEL)
    tokens = enc.encode(post["content"])
    now    = datetime.now(timezone.utc).isoformat()

    raw_chunks: list[str] = []
    if len(tokens) <= CHUNK_SIZE_TOKENS:
        raw_chunks = [post["content"]]
    else:
        start = 0
        while start < len(tokens):
            end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
            raw_chunks.append(enc.decode(tokens[start:end]))
            if end == len(tokens):
                break
            start += CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS

    chunks = []
    for idx, text in enumerate(raw_chunks):
        if not text.strip():
            continue
        chunks.append({
            "id"                 : str(uuid.uuid4()),
            "content"            : text,
            "content_vector"     : [],           # filled by upload_chunks()
            "document_title"     : post["title"],
            "source_url"         : post["url"],  # live Substack URL → direct citation
            "page_number"        : None,
            "slide_number"       : None,
            "content_type"       : "text",
            "customer_tag"       : "internal",
            "author"             : "Sandeep Alur",
            "created_date"       : _parse_date(post.get("published_date") or "", now),
            "last_modified_date" : _parse_date(post.get("published_date") or "", now),
            "chunk_index"        : idx,
            "extracted_caption"  : post.get("subtitle", ""),
            "allowed_groups"     : ["all"],
        })
    return chunks


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(dry_run: bool = False, reset: bool = False) -> None:
    blob_service = _get_blob_service()

    # ── Load state ────────────────────────────────────────────────────────────
    state = _read_blob_json(blob_service, STATE_BLOB_PATH) or {"processed_urls": []}
    if reset:
        logger.info("[yellow]--reset: clearing state, all posts will be reingested[/yellow]")
        state = {"processed_urls": []}

    processed_urls = set(state.get("processed_urls", []))
    is_first_run   = len(processed_urls) == 0

    # ── Fetch + filter ────────────────────────────────────────────────────────
    all_posts = fetch_all_posts()
    new_posts  = [p for p in all_posts if p["url"] not in processed_urls]

    if not new_posts:
        console.print("[green]Nothing new — index is already up to date.[/green]")
        return

    console.print(
        f"[bold]{len(new_posts)} new post(s) to ingest[/bold] "
        f"({len(all_posts)} total · {'first run' if is_first_run else f'{len(processed_urls)} already indexed'})"
    )

    run_log = {
        "run_timestamp"        : datetime.now(timezone.utc).isoformat(),
        "is_first_run"         : is_first_run,
        "dry_run"              : dry_run,
        "posts_discovered"     : len(all_posts),
        "new_posts_found"      : len(new_posts),
        "successfully_ingested": 0,           # updated as we go
        "ingested"             : [],
        "skipped"              : [],          # {url, slug, title, reason}
        "total_chunks"         : 0,
    }

    total_chunks = 0

    for post_meta in new_posts:
        logger.info("[bold]→[/bold] %s", post_meta["title"])

        # ── Get content (from rss2json if available, else HTML scrape fallback) ──
        content = post_meta.get("content", "").strip()
        if content:
            logger.info("  Content sourced from rss2json (%d words)", len(content.split()))
        else:
            logger.info("  rss2json had no content — falling back to HTML scrape...")
            content, failure_reason = get_post_content(post_meta["url"], post_meta["slug"])

        if not content:
            logger.warning("  [red]Skipped — %s[/red]", failure_reason)
            run_log["skipped"].append({
                "title"  : post_meta["title"],
                "url"    : post_meta["url"],
                "slug"   : post_meta["slug"],
                "reason" : failure_reason,
            })
            continue

        post_data = {
            "title"          : post_meta["title"],
            "url"            : post_meta["url"],
            "slug"           : post_meta["slug"],
            "published_date" : post_meta["published_date"],
            "subtitle"       : post_meta["subtitle"],
            "author"         : "Sandeep Alur",
            "content"        : content,
            "word_count"     : len(content.split()),
            "scraped_at"     : datetime.now(timezone.utc).isoformat(),
        }

        # ── Save JSON to Blob (always — this is the visibility/audit layer) ──
        blob_path = f"{BLOB_PREFIX}/{post_meta['slug']}.json"
        _write_blob_json(blob_service, blob_path, post_data)
        logger.info("  Saved to Blob: [cyan]%s[/cyan]  (%d words)", blob_path, post_data["word_count"])

        # ── Chunk ─────────────────────────────────────────────────────────────
        chunks = chunk_post(post_data)
        logger.info("  %d chunk(s) produced", len(chunks))

        # ── Embed + Upload to AI Search ───────────────────────────────────────
        if dry_run:
            logger.info("  [yellow][DRY RUN] Skipping AI Search upload[/yellow]")
            uploaded = len(chunks)
        else:
            uploaded = upload_chunks(chunks, dry_run=False)
            logger.info("  [green]%d chunk(s) uploaded to AI Search[/green]", uploaded)

        total_chunks += uploaded
        processed_urls.add(post_meta["url"])
        run_log["successfully_ingested"] += 1

        run_log["ingested"].append({
            "title"      : post_meta["title"],
            "url"        : post_meta["url"],
            "slug"       : post_meta["slug"],
            "word_count" : post_data["word_count"],
            "chunks"     : uploaded,
            "blob_path"  : blob_path,
        })

    # ── Persist state + run log ───────────────────────────────────────────────
    state["processed_urls"] = list(processed_urls)
    state["last_run"]        = datetime.now(timezone.utc).isoformat()
    _write_blob_json(blob_service, STATE_BLOB_PATH, state)

    run_log["total_chunks"] = total_chunks
    _write_blob_json(blob_service, LOG_BLOB_PATH, run_log)

    # ── Summary ───────────────────────────────────────────────────────────────
    console.rule()
    console.print(
        f"[bold green]Done!  "
        f"{run_log['successfully_ingested']} ingested · "
        f"{len(run_log['skipped'])} skipped · "
        f"{total_chunks} chunk(s) {'staged (dry-run)' if dry_run else 'uploaded to AI Search'}.[/bold green]"
    )
    if run_log["skipped"]:
        for s in run_log["skipped"]:
            console.print(f"  [yellow]⚠ Skipped:[/yellow] {s['title']} — {s['reason']}")


def main():
    parser = argparse.ArgumentParser(description="ContentIQ — Substack Ingestion")
    parser.add_argument("--dry-run", action="store_true",
                        help="Save JSONs to Blob but skip AI Search upload")
    parser.add_argument("--reset",   action="store_true",
                        help="Clear state and reingest all posts from scratch")
    args = parser.parse_args()

    console.rule("[bold blue]ContentIQ — Substack Ingestion")
    run(dry_run=args.dry_run, reset=args.reset)


if __name__ == "__main__":
    main()
