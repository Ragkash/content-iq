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
import logging
import os
import sys

from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn

load_dotenv()

# Use rich for pretty CLI output
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, markup=True, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)

# Add backend dir to path so we can import ingestion modules
sys.path.insert(0, os.path.dirname(__file__))

from ingestion.indexer import create_or_update_index
from ingestion.analyzer import analyze_document
from ingestion.chunker import chunk_document
from ingestion.uploader import upload_chunks

STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER", "documents")


def list_blob_files() -> list[dict]:
    """List all files in the Blob container with their name and SAS URL."""
    if not STORAGE_CONNECTION_STRING:
        raise EnvironmentError("AZURE_STORAGE_CONNECTION_STRING must be set in .env")

    client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
    container = client.get_container_client(CONTAINER_NAME)

    files = []
    for blob in container.list_blobs():
        blob_client = container.get_blob_client(blob.name)

        # blob.name  = path within container, e.g. "customers/Shell/file.pdf"
        #              This is what gets passed as file_path → used for customer_tag extraction
        # blob_client.url = full URL e.g. "https://<account>.blob.core.windows.net/<container>/customers/Shell/file.pdf"
        #                   This is sent to CU as the document URL and stored as source_url in the index.
        #
        # TODO: For private containers (recommended for enterprise data), blob_client.url
        # does NOT include auth. Content Understanding will receive a 403 when it tries to
        # fetch the document. Fix before running P3:
        #
        # Option A — generate a short-lived SAS token:
        #   from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        #   from datetime import timedelta
        #   account_key = client.credential.account_key  # extracted from connection string
        #   sas = generate_blob_sas(
        #       account_name=blob_client.account_name,
        #       container_name=CONTAINER_NAME,
        #       blob_name=blob.name,
        #       account_key=account_key,
        #       permission=BlobSasPermissions(read=True),
        #       expiry=datetime.utcnow() + timedelta(hours=2),
        #   )
        #   blob_url = f"{blob_client.url}?{sas}"
        #
        # Option B — set the container to allow CU's managed identity
        #            via Azure portal → Storage Account → Access Control (IAM)
        #
        # For now, blob_client.url works if the container is set to public read (dev only).
        files.append({
            "file_path": blob.name,
            "blob_url": blob_client.url,
            "last_modified": blob.last_modified,
        })
    return files


def get_blob_metadata(blob_info: dict) -> dict:
    """Extract metadata from blob properties for the chunk schema."""
    import datetime
    return {
        "last_modified_date": (
            blob_info["last_modified"].isoformat()
            if blob_info.get("last_modified")
            else datetime.datetime.now().isoformat()
        ),
        "created_date": (
            blob_info["last_modified"].isoformat()
            if blob_info.get("last_modified")
            else datetime.datetime.now().isoformat()
        ),
        "author": "",  # CU can extract this from document metadata
    }


def ingest_file(blob_info: dict, dry_run: bool = False) -> int:
    """
    Run the full pipeline for a single blob file.
    Returns number of chunks produced (or would produce in dry-run).
    """
    file_path = blob_info["file_path"]
    blob_url = blob_info["blob_url"]

    logger.info("[bold]Processing[/bold]: %s", file_path)

    # Skip unsupported file types
    ext = file_path.lower().split(".")[-1]
    if ext not in {"pdf", "pptx", "docx", "xlsx", "txt", "md"}:
        logger.warning("Skipping unsupported file type: %s", file_path)
        return 0

    try:
        # Step 1: Analyse with Content Understanding
        result = analyze_document(blob_url)
        logger.info("  CU analysis done — %d pages, %d figures", len(result.get("pages", [])), len(result.get("figures", [])))

        # Step 2: Chunk into ContentIQ metadata-rich chunks
        blob_metadata = get_blob_metadata(blob_info)
        chunks = chunk_document(result, blob_url, file_path, blob_metadata)
        logger.info("  %d chunks produced", len(chunks))

        # Step 3: Embed + upload
        uploaded = upload_chunks(chunks, dry_run=dry_run)
        logger.info("  %d chunks [green]uploaded[/green] (%s)", uploaded, "DRY RUN" if dry_run else "LIVE")
        return uploaded

    except Exception as e:
        logger.error("[red]FAILED[/red] for %s: %s", file_path, e)
        return 0


def main():
    parser = argparse.ArgumentParser(description="ContentIQ Ingestion Pipeline")
    parser.add_argument("--create-index", action="store_true", help="Create/update the AI Search index schema")
    parser.add_argument("--dry-run", action="store_true", help="Embed chunks but do NOT upload to AI Search")
    parser.add_argument("--file", type=str, default=None, help="Ingest only this blob path (e.g. customers/Shell/file.pdf)")
    args = parser.parse_args()

    console.rule("[bold blue]ContentIQ Ingestion Pipeline")

    # Create/update index schema
    if args.create_index:
        console.print("[yellow]Creating/verifying AI Search index schema...[/yellow]")
        create_or_update_index()
        console.print("[green]Index ready.[/green]")

    # List blobs
    if args.file:
        # Single-file mode: construct blob info manually
        client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
        blob_client = client.get_container_client(CONTAINER_NAME).get_blob_client(args.file)
        blobs = [{"file_path": args.file, "blob_url": blob_client.url, "last_modified": None}]
    else:
        console.print("[yellow]Listing blobs in container '%s'...[/yellow]" % CONTAINER_NAME)
        blobs = list_blob_files()
        console.print(f"[green]Found {len(blobs)} files.[/green]")

    if not blobs:
        console.print("[yellow]No files to ingest. Upload documents to Blob Storage first.[/yellow]")
        return

    total_chunks = 0
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Ingesting...", total=len(blobs))
        for blob_info in blobs:
            progress.update(task, description=f"[cyan]{blob_info['file_path']}[/cyan]")
            n = ingest_file(blob_info, dry_run=args.dry_run)
            total_chunks += n
            progress.advance(task)

    console.rule()
    console.print(f"[bold green]Done! {total_chunks} chunks {'would be' if args.dry_run else 'were'} uploaded.[/bold green]")


if __name__ == "__main__":
    main()
