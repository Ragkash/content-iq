"""
ContentIQ — Ingestion: Text Embedder
Generates 1536-dim embeddings using Azure OpenAI text-embedding-ada-002.

DEPLOYED TO AZURE: This is a copy of backend/ingestion/embedder.py placed here because
when Azure deploys the scraper/ folder, it has no access to the parent ingestion/ directory.
Copying it here ensures the scraper can embed chunks in Azure exactly as it does locally.
"""

import os
import logging
from typing import Sequence

from openai import AzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import RateLimitError, APIError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
EMB_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT", "text-embedding-ada-002")

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_KEY:
            raise EnvironmentError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY must be set in .env"
            )
        _client = AzureOpenAI(
            api_key=AZURE_OPENAI_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version="2024-02-01",
        )
    return _client


@retry(
    retry=retry_if_exception_type((RateLimitError, APIError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def embed_text(text: str) -> list[float]:
    """
    Embed a single string using text-embedding-ada-002.

    Args:
        text: The string to embed (max ~8191 tokens for ada-002).

    Returns:
        A list of 1536 floats representing the embedding vector.
    """
    client = _get_client()
    response = client.embeddings.create(
        model=EMB_DEPLOYMENT,
        input=text,
    )
    return response.data[0].embedding


def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """
    Embed multiple strings in a single API call (more efficient than looping).
    Ada-002 supports up to 2048 items per batch but we cap at 100 for safety.

    Args:
        texts: Sequence of strings to embed.

    Returns:
        List of embedding vectors in the same order as inputs.
    """
    if not texts:
        return []

    BATCH_SIZE = 100
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = list(texts[i : i + BATCH_SIZE])
        logger.debug("Embedding batch %d–%d of %d", i, i + len(batch), len(texts))

        client = _get_client()
        response = client.embeddings.create(
            model=EMB_DEPLOYMENT,
            input=batch,
        )
        # API returns items sorted by index
        sorted_data = sorted(response.data, key=lambda x: x.index)
        all_embeddings.extend([item.embedding for item in sorted_data])

    return all_embeddings


# ─── CLI test helper ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_text = "Shell digital transformation proposal Q4 2024"
    print(f"Embedding: '{test_text}'")
    embedding = embed_text(test_text)
    print(f"Dimensions: {len(embedding)}")
    print(f"First 5 values: {embedding[:5]}")
