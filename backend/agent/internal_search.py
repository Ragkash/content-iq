"""
ContentIQ — Agent: Internal Search Tool
Hybrid search (vector + BM25 + RRF) against the ContentIQ AI Search index
with optional metadata filters (customer_tag, content_type, date sort).
"""

import os
import logging
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv

from ingestion.embedder import embed_text

load_dotenv()
logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "content-iq-index")
TOP_K = int(os.getenv("TOP_K_RESULTS", "5"))


def _get_search_client() -> SearchClient:
    if not SEARCH_ENDPOINT or not SEARCH_KEY:
        raise EnvironmentError(
            "AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY must be set in .env"
        )
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(SEARCH_KEY),
    )


def _build_filter(metadata_filters: dict[str, Any]) -> str | None:
    """
    Build an OData filter expression from ContentIQ metadata filters.

    Supported filters:
        customer_tag  → "customer_tag eq 'Shell'"
        content_type  → "content_type eq 'chart'"

    Returns OData filter string or None if no filters.
    """
    clauses = []
    customer = metadata_filters.get("customer_tag")
    if customer:
        # Normalize to match how customer_tag is stored (folder name: lowercase, spaces→underscores)
        # e.g. "Air India" → "air_india", "IndiGo" → "indigo"
        safe_customer = customer.lower().replace(" ", "_").replace("'", "''")
        clauses.append(f"customer_tag eq '{safe_customer}'")

    content_type = metadata_filters.get("content_type")
    if content_type:
        safe_ct = content_type.replace("'", "''")
        clauses.append(f"content_type eq '{safe_ct}'")

    return " and ".join(clauses) if clauses else None


def search(
    query: str,
    metadata_filters: dict[str, Any] | None = None,
    top: int = TOP_K,
) -> list[dict[str, Any]]:
    """
    Run hybrid (vector + keyword) search against the ContentIQ AI Search index.

    Args:
        query:            Natural language search query (or optimised keyword query).
        metadata_filters: Dict from QueryParser containing customer_tag, content_type, sort.
        top:              Max number of results to return.

    Returns:
        List of result dicts, each containing all ContentIQ metadata fields
        plus "@search.score" and "@search.reranker_score".
    """
    if not query.strip():
        return []

    filters = metadata_filters or {}
    odata_filter = _build_filter(filters)
    sort_by = filters.get("sort")  # e.g. "last_modified_date desc"

    # Build vector query from the text embedding of the query
    query_vector = embed_text(query)
    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=top * 2,     # retrieve more candidates, let RRF re-rank
        fields="content_vector",
    )

    logger.info(
        "InternalSearchTool.search | query='%s' | filter='%s' | sort='%s'",
        query[:80], odata_filter, sort_by
    )

    client = _get_search_client()

    search_kwargs: dict[str, Any] = {
        "search_text": query,           # BM25 keyword component
        "vector_queries": [vector_query],  # Vector component
        "select": [                     # Only retrieve needed fields
            "id", "content", "document_title", "source_url",
            "page_number", "slide_number", "content_type",
            "customer_tag", "author", "last_modified_date",
            "chunk_index", "extracted_caption",
        ],
        "top": top,
        "query_type": "semantic",       # Enable semantic ranking (RRF + reranker)
        "semantic_configuration_name": "default",
    }
    if odata_filter:
        search_kwargs["filter"] = odata_filter
    # order_by is not supported when query_type="semantic"; ignore sort requests

    seen_ids: set[str] = set()
    results = []
    for item in client.search(**search_kwargs):
        doc_id = item.get("id", "")
        if doc_id and doc_id in seen_ids:
            continue
        if doc_id:
            seen_ids.add(doc_id)
        result = dict(item)
        # Ensure score fields are present for ConfidenceEvaluator
        result["@search.score"] = item.get("@search.score", 0.0)
        result["@search.reranker_score"] = item.get("@search.reranker_score", 0.0)
        results.append(result)

    logger.info("InternalSearchTool returned %d results (deduplicated).", len(results))
    return results


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Shell digital transformation"
    results = search(q)
    print(f"Results: {len(results)}")
    for r in results:
        print(f"  [{r.get('@search.score', 0):.3f}] {r.get('document_title')} p{r.get('page_number')}")
        print(f"    {r.get('content', '')[:120]}")
