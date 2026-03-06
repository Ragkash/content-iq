"""
ContentIQ — Ingestion: AI Search Index Manager
Creates (or updates) the Azure AI Search index with the full ContentIQ
metadata schema. Extends the pattern from azure-search-openai-demo's
SearchManager but adds ContentIQ-specific fields.
"""

import os
import logging
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchAlgorithmConfiguration,
    VectorSearchProfile,
    BinaryQuantizationCompression,
    RescoringOptions,
    VectorSearchCompressionRescoreStorageMethod,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
)
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "content-iq-index")
OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
EMB_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT", "text-embedding-ada-002")

EMBEDDING_DIMENSIONS = 1536  # text-embedding-ada-002


def get_index_client() -> SearchIndexClient:
    if not SEARCH_ENDPOINT or not SEARCH_KEY:
        raise EnvironmentError("AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY must be set in .env")
    return SearchIndexClient(
        endpoint=SEARCH_ENDPOINT,
        credential=AzureKeyCredential(SEARCH_KEY),
    )


def create_or_update_index() -> None:
    """
    Create the ContentIQ AI Search index if it doesn't exist, or update
    the schema if new fields are needed.

    Index schema includes:
    - Standard RAG fields (id, content, content_vector)
    - ContentIQ metadata fields (customer_tag, page_number, author, etc.)
    - Access control placeholder (allowed_groups) for v2 RBAC readiness
    - Semantic search config with document_title + content + extracted_caption
    """
    client = get_index_client()

    existing_names = [name for name in client.list_index_names()]
    if INDEX_NAME in existing_names:
        logger.info("Index '%s' already exists — verifying schema.", INDEX_NAME)
        _ensure_fields(client)
        return

    logger.info("Creating new AI Search index: '%s'", INDEX_NAME)

    # ── Vector search configuration ──────────────────────────────────────────
    vectorizer = AzureOpenAIVectorizer(
        vectorizer_name=f"{EMB_DEPLOYMENT}-vectorizer",
        parameters=AzureOpenAIVectorizerParameters(
            resource_url=OPENAI_ENDPOINT,
            deployment_name=EMB_DEPLOYMENT,
            model_name=EMB_DEPLOYMENT,
        ),
    ) if OPENAI_ENDPOINT and EMB_DEPLOYMENT else None

    hnsw_algo = HnswAlgorithmConfiguration(
        name="hnsw_config",
        parameters=HnswParameters(metric="cosine"),
    )
    # ⚠️  IMPORTANT — Azure Search Tier Requirement:
    # BinaryQuantizationCompression requires S1 tier or higher.
    # FREE and BASIC tiers will return 400 Bad Request when create_index() is called.
    # When you provision Azure AI Search in P0, choose:
    #   - S1  (recommended for POC — ~$245/month)
    #   - S2/S3 for production
    # If you only have a Basic tier available for testing, remove the
    # 'compression_name' from vector_profile and remove the 'compression'
    # object from VectorSearch() below to fall back to flat HNSW (no compression).
    compression = BinaryQuantizationCompression(
        compression_name="content_vector-compression",
        truncation_dimension=1024,
        rescoring_options=RescoringOptions(
            enable_rescoring=True,
            default_oversampling=10,
            rescore_storage_method=VectorSearchCompressionRescoreStorageMethod.PRESERVE_ORIGINALS,
        ),
    )
    vector_profile = VectorSearchProfile(
        name="content_vector-profile",
        algorithm_configuration_name="hnsw_config",
        compression_name="content_vector-compression",
        **({
            "vectorizer_name": vectorizer.vectorizer_name
        } if vectorizer else {}),
    )

    # ── Field definitions ─────────────────────────────────────────────────────
    fields: list[Any] = [
        # Core RAG fields
        SimpleField(
            name="id",
            type="Edm.String",
            key=True,
            filterable=True,
            sortable=True,
        ),
        SearchableField(
            name="content",
            type="Edm.String",
            analyzer_name="en.microsoft",
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            hidden=True,
            searchable=True,
            filterable=False,
            sortable=False,
            facetable=False,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="content_vector-profile",
            stored=False,
        ),

        # ── ContentIQ metadata fields ─────────────────────────────────────────
        SearchableField(
            name="document_title",
            type="Edm.String",
            filterable=True,
            facetable=False,
        ),
        SimpleField(
            name="source_url",
            type="Edm.String",
            filterable=True,
            facetable=False,
        ),
        SimpleField(
            name="page_number",
            type="Edm.Int32",
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="slide_number",
            type="Edm.Int32",
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="content_type",
            type="Edm.String",
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="customer_tag",
            type="Edm.String",
            filterable=True,
            facetable=True,
        ),
        SearchableField(
            name="author",
            type="Edm.String",
            filterable=True,
            facetable=False,
        ),
        SimpleField(
            name="created_date",
            type="Edm.DateTimeOffset",
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="last_modified_date",
            type="Edm.DateTimeOffset",
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="chunk_index",
            type="Edm.Int32",
            filterable=True,
            sortable=True,
        ),
        SearchableField(
            name="extracted_caption",
            type="Edm.String",
            filterable=False,
            facetable=False,
        ),
        # v2 RBAC placeholder — set to ["all"] for every chunk in v1
        SearchField(
            name="allowed_groups",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            facetable=False,
        ),
    ]

    # ── Semantic search config ────────────────────────────────────────────────
    semantic_search = SemanticSearch(
        default_configuration_name="default",
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="document_title"),
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[SemanticField(field_name="extracted_caption")],
                ),
            )
        ],
    )

    # ── Assemble VectorSearch ─────────────────────────────────────────────────
    vectorizers = [vectorizer] if vectorizer else []
    vector_search = VectorSearch(
        algorithms=[hnsw_algo],
        profiles=[vector_profile],
        compressions=[compression],
        vectorizers=vectorizers if vectorizers else None,
    )

    # ── Create index ──────────────────────────────────────────────────────────
    index = SearchIndex(
        name=INDEX_NAME,
        fields=fields,
        semantic_search=semantic_search,
        vector_search=vector_search,
    )
    client.create_index(index)
    logger.info("Index '%s' created successfully.", INDEX_NAME)


def _ensure_fields(client: SearchIndexClient) -> None:
    """Add any missing ContentIQ fields to an existing index."""
    existing = client.get_index(INDEX_NAME)
    existing_names = {f.name for f in existing.fields}

    new_fields = []
    expected = {
        "customer_tag": SimpleField(name="customer_tag", type="Edm.String", filterable=True, facetable=True),
        "page_number": SimpleField(name="page_number", type="Edm.Int32", filterable=True, sortable=True),
        "slide_number": SimpleField(name="slide_number", type="Edm.Int32", filterable=True, sortable=True),
        "content_type": SimpleField(name="content_type", type="Edm.String", filterable=True, facetable=True),
        "author": SearchableField(name="author", type="Edm.String", filterable=True),
        "last_modified_date": SimpleField(name="last_modified_date", type="Edm.DateTimeOffset", filterable=True, sortable=True),
        "extracted_caption": SearchableField(name="extracted_caption", type="Edm.String"),
        "source_url": SimpleField(name="source_url", type="Edm.String", filterable=True),
        "document_title": SearchableField(name="document_title", type="Edm.String", filterable=True),
    }
    for field_name, field_def in expected.items():
        if field_name not in existing_names:
            logger.info("Adding missing field '%s' to index '%s'.", field_name, INDEX_NAME)
            new_fields.append(field_def)

    if new_fields:
        existing.fields.extend(new_fields)
        client.create_or_update_index(existing)
        logger.info("Index schema updated with %d new fields.", len(new_fields))
    else:
        logger.info("Index schema is up to date.")


# ─── CLI helper ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    create_or_update_index()
    print(f"Index '{INDEX_NAME}' is ready.")
