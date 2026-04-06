"""
ContentIQ — Ingestion CLI: ingest_all.py
Runs the full ingestion pipeline over all files in Azure Blob Storage:
  Blob Storage → Content Understanding → Chunk → Embed → AI Search Index

Usage:
  # First create the index schema (safe to run multiple times)
  python ingest_all.py --create-index

  # Ingest all documents
  python ingest_all.py

  # Dry-run: test without uploading to AI Search
  python ingest_all.py --dry-run

  # Single file (by Blob path)
  python ingest_all.py --file customers/Shell/shell_proposal.pdf
"""

import argparse
import datetime
import json
import logging
import os
import sys
import uuid

# ── Apply SDK timeout patches before any Azure/OpenAI imports ────────────────
# Fixes indefinite hang on networks with slow proxy auto-detection (Windows).
# See backend/sdk_patch.py for details.
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _backend_dir)
import sdk_patch  # noqa: F401, E402
# ─────────────────────────────────────────────────────────────────────────────

import tiktoken
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn

load_dotenv()

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, markup=True, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)

from ingestion.indexer import create_or_update_index
from ingestion.analyzer import analyze_document, extract_text_pypdf
from ingestion.chunker import chunk_document
from ingestion.uploader import upload_chunks

STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER", "documents")
STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")


def _get_blob_service_client() -> BlobServiceClient:
    if STORAGE_CONNECTION_STRING:
        return BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
    if not STORAGE_ACCOUNT_NAME:
        raise EnvironmentError(
            "Either AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME must be set in .env"
        )
    account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())


def list_blob_files() -> list[dict]:
    """List all files in the Blob container."""
    client = _get_blob_service_client()
    container = client.get_container_client(CONTAINER_NAME)

    files = []
    for blob in container.list_blobs():
        blob_client = container.get_blob_client(blob.name)
        files.append({
            "file_path": blob.name,
            "blob_url": blob_client.url,
            "last_modified": blob.last_modified,
        })
    return files


def get_blob_metadata(blob_info: dict) -> dict:
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "last_modified_date": (
            blob_info["last_modified"].isoformat()
            if blob_info.get("last_modified") else now_utc
        ),
        "created_date": (
            blob_info["last_modified"].isoformat()
            if blob_info.get("last_modified") else now_utc
        ),
        "author": "",
    }


def _ingest_substack_json(blob_info: dict, dry_run: bool = False) -> int:
    """
    Ingest a Substack post JSON blob into AI Search.
    JSON schema (written by substack_scraper.py):
        { title, url, content, published_date, subtitle, author, ... }
    """
    file_path = blob_info["file_path"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    client = _get_blob_service_client()
    blob_client = client.get_container_client(CONTAINER_NAME).get_blob_client(file_path)
    raw = blob_client.download_blob().readall().decode("utf-8")
    post = json.loads(raw)

    content = post.get("content", "").strip()
    if not content:
        logger.warning("  JSON has no content field — skipping %s", file_path)
        return 0

    title    = post.get("title", file_path.split("/")[-1].replace(".json", ""))
    url      = post.get("url", "")
    subtitle = post.get("subtitle", "")
    author   = post.get("author", "Sandeep Alur")
    pub_date = post.get("published_date") or post.get("scraped_at") or now

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(content)
    CHUNK_SIZE, OVERLAP = 500, 50

    raw_chunks: list[str] = []
    if len(tokens) <= CHUNK_SIZE:
        raw_chunks = [content]
    else:
        start = 0
        while start < len(tokens):
            end = min(start + CHUNK_SIZE, len(tokens))
            raw_chunks.append(enc.decode(tokens[start:end]))
            if end == len(tokens):
                break
            start += CHUNK_SIZE - OVERLAP

    chunks = []
    for idx, text in enumerate(raw_chunks):
        if not text.strip():
            continue
        chunks.append({
            "id":                 str(uuid.uuid4()),
            "content":            text,
            "content_vector":     [],
            "document_title":     title,
            "source_url":         url,
            "page_number":        None,
            "slide_number":       None,
            "content_type":       "text",
            "customer_tag":       "internal",
            "author":             author,
            "created_date":       pub_date,
            "last_modified_date": pub_date,
            "chunk_index":        idx,
            "extracted_caption":  subtitle,
            "allowed_groups":     ["all"],
        })

    logger.info("  %d chunks produced from Substack JSON", len(chunks))
    uploaded = upload_chunks(chunks, dry_run=dry_run)
    logger.info("  %d chunks [green]uploaded[/green] (%s)", uploaded, "DRY RUN" if dry_run else "LIVE")
    return uploaded


def ingest_file(
    blob_info: dict,
    dry_run: bool = False,
    cache_dir: str | None = None,
    skip_cu: bool = False,
    no_cu: bool = False,
) -> int:
    file_path = blob_info["file_path"]
    blob_url  = blob_info["blob_url"]

    logger.info("[bold]Processing[/bold]: %s", file_path)

    ext = file_path.lower().split(".")[-1]

    # JSON = Substack post (pre-scraped, no CU needed)
    if ext == "json":
        return _ingest_substack_json(blob_info, dry_run=dry_run)

    if ext not in {"pdf", "pptx", "docx", "xlsx", "txt", "md"}:
        logger.warning("Skipping unsupported file type: %s", file_path)
        return 0

    try:
        if no_cu:
            if ext != "pdf":
                logger.warning("  --no-cu only supports PDF files — skipping %s", file_path)
                return 0
            result = extract_text_pypdf(file_path, STORAGE_CONNECTION_STRING, CONTAINER_NAME)
        elif skip_cu and cache_dir:
            import pathlib
            safe_key = file_path.replace("/", "_").replace("\\", "_")
            cache_path = pathlib.Path(cache_dir) / f"{safe_key}.json"
            if not cache_path.exists():
                logger.warning("  --skip-cu set but no cache for %s — skipping", file_path)
                return 0
            result = analyze_document(blob_url, cache_dir=cache_dir, cache_key=file_path)
        else:
            result = analyze_document(blob_url, cache_dir=cache_dir, cache_key=file_path)

        logger.info("  CU analysis done — %d pages, %d figures",
                    len(result.get("pages", [])), len(result.get("figures", [])))

        blob_metadata = get_blob_metadata(blob_info)
        chunks = chunk_document(result, blob_url, file_path, blob_metadata)
        logger.info("  %d chunks produced", len(chunks))

        uploaded = upload_chunks(chunks, dry_run=dry_run)
        logger.info("  %d chunks [green]uploaded[/green] (%s)",
                    uploaded, "DRY RUN" if dry_run else "LIVE")
        return uploaded

    except Exception as e:
        logger.error("[red]FAILED[/red] for %s: %s", file_path, e)
        return 0


def main():
    parser = argparse.ArgumentParser(description="ContentIQ Ingestion Pipeline")
    parser.add_argument("--create-index", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--file", type=str, default=None)
    parser.add_argument("--folder", type=str, default=None,
                        help="Only ingest blobs whose path starts with this prefix (e.g. sandeep-alur/)")
    parser.add_argument("--cache-dir", type=str, default=".cu_cache")
    parser.add_argument("--skip-cu", action="store_true")
    parser.add_argument("--no-cu", action="store_true")
    args = parser.parse_args()

    console.rule("[bold blue]ContentIQ Ingestion Pipeline")

    if args.create_index:
        console.print("[yellow]Creating/verifying AI Search index schema...[/yellow]")
        create_or_update_index()
        console.print("[green]Index ready.[/green]")
        if not args.file and not args.dry_run and not args.skip_cu and not args.no_cu:
            return

    if args.file:
        client = _get_blob_service_client()
        blob_client = client.get_container_client(CONTAINER_NAME).get_blob_client(args.file)
        blobs = [{"file_path": args.file, "blob_url": blob_client.url, "last_modified": None}]
    else:
        console.print("[yellow]Listing blobs in container '%s'...[/yellow]" % CONTAINER_NAME)
        blobs = list_blob_files()
        if args.folder:
            prefix = args.folder.rstrip("/") + "/"
            blobs = [b for b in blobs if b["file_path"].startswith(prefix)]
            console.print(f"[green]Found {len(blobs)} files matching folder '{args.folder}'.[/green]")
        else:
            console.print(f"[green]Found {len(blobs)} files.[/green]")

    if not blobs:
        console.print("[yellow]No files to ingest.[/yellow]")
        return

    total_chunks = 0
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=console) as progress:
        task = progress.add_task("Ingesting...", total=len(blobs))
        for blob_info in blobs:
            progress.update(task, description=f"[cyan]{blob_info['file_path']}[/cyan]")
            n = ingest_file(blob_info, dry_run=args.dry_run, cache_dir=args.cache_dir,
                            skip_cu=args.skip_cu, no_cu=args.no_cu)
            total_chunks += n
            progress.advance(task)

    try:
        console.rule()
    except UnicodeEncodeError:
        print("-" * 80)
    try:
        console.print(f"[bold green]Done! {total_chunks} chunks "
                      f"{'would be' if args.dry_run else 'were'} uploaded.[/bold green]")
    except UnicodeEncodeError:
        print(f"Done! {total_chunks} chunks {'would be' if args.dry_run else 'were'} uploaded.")


if __name__ == "__main__":
    main()
