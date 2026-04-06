"""
ContentIQ — Ingestion: Chunk Uploader
Takes chunked documents (output of chunker.py), embeds each chunk's content,
and batch-uploads everything to the Azure AI Search index.
"""

import os
import logging
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv

from embedder import embed_batch

load_dotenv()
logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "content-iq-index")

BATCH_SIZE = 100


def get_search_client() -> SearchClient:
    if not SEARCH_ENDPOINT or not SEARCH_KEY:
        raise EnvironmentError(
            "AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY must be set in .env"
        )
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(SEARCH_KEY),
    )


def upload_chunks(chunks: list[dict[str, Any]], dry_run: bool = False) -> int:
    """
    Embed and upload a list of ContentIQ chunks to Azure AI Search.

    Args:
        chunks:  List of chunk dicts from chunker.chunk_document()
        dry_run: If True, embed and validate but do NOT upload to the index.

    Returns:
        Number of chunks successfully uploaded.
    """
    if not chunks:
        logger.info("No chunks to upload.")
        return 0

    logger.info("Embedding %d chunks...", len(chunks))

    texts = [c["content"] for c in chunks]
    embeddings = embed_batch(texts)

    for chunk, vector in zip(chunks, embeddings):
        chunk["content_vector"] = vector

    if dry_run:
        logger.info("[DRY RUN] Would upload %d chunks. Skipping actual upload.", len(chunks))
        _validate_chunks(chunks)
        return len(chunks)

    client = get_search_client()
    total_uploaded = 0

    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        logger.info(
            "Uploading batch %d/%d (%d chunks)...",
            i // BATCH_SIZE + 1,
            -(-len(chunks) // BATCH_SIZE),
            len(batch),
        )
        result = client.upload_documents(documents=batch)

        succeeded = sum(1 for r in result if r.succeeded)
        failed = len(batch) - succeeded
        total_uploaded += succeeded

        if failed > 0:
            logger.warning("%d chunks FAILED to upload in this batch.", failed)
        else:
            logger.info("Batch uploaded successfully (%d chunks).", succeeded)

    logger.info("Total uploaded: %d / %d chunks.", total_uploaded, len(chunks))
    return total_uploaded


def _validate_chunks(chunks: list[dict[str, Any]]) -> None:
    REQUIRED_FIELDS = {
        "id", "content", "content_vector", "document_title", "source_url",
        "customer_tag", "content_type", "chunk_index", "allowed_groups",
    }
    errors = []
    for i, chunk in enumerate(chunks):
        missing = REQUIRED_FIELDS - set(chunk.keys())
        if missing:
            errors.append(f"Chunk {i} (id={chunk.get('id', '?')}) missing: {missing}")
    if errors:
        raise ValueError("Chunk validation failed:\n" + "\n".join(errors))
    logger.info("All %d chunks passed validation.", len(chunks))
