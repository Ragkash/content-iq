# Content IQ — Architecture

**Version:** 1.0
**Stack:** Python 3.11 · FastAPI · Groq llama-3.3-70b · Azure AI Search · Azure Blob Storage · React 19 · Vite

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        USER BROWSER                          │
│   React 19 + Vite + Fluent UI  (ContentIQChat.tsx)           │
│                                                              │
│   [ Chat Input ]  [ Web Search Toggle ]                      │
│   ┌────────────────────────────────────────────┐            │
│   │ Assistant: Here is what I found...          │            │
│   │                                             │            │
│   │  Sources                                    │            │
│   │  📄 Shell_Proposal.pdf · Page 3 [INTERNAL] │            │
│   │  📊 Shell_Revenue_Chart.pdf · Page 7        │            │
│   └────────────────────────────────────────────┘            │
└──────────────────────────┬──────────────────────────────────┘
                           │ POST /chat
                           │ { message, conversation_id,
                           │   web_search_enabled, web_only }
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    FASTAPI BACKEND  (main.py)                 │
│   Port 8000 · CORS: localhost:3000, localhost:5173            │
│   ChatRequest → Orchestrator → ChatResponse                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              AGENT ORCHESTRATOR  (orchestrator.py)           │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Session    │  │ Query Parser │  │  Follow-up        │  │
│  │  Store      │  │ (Groq JSON)  │  │  Detector         │  │
│  │ (in-memory) │  │ intent       │  │  ≤8 words OR      │  │
│  │ history     │  │ entities     │  │  signal word +    │  │
│  │ chunks      │  │ OData filters│  │  same customer    │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  ROUTING LOGIC                        │   │
│  │                                                       │   │
│  │  web_only=True ──────────────────────────────┐        │   │
│  │                                              │        │   │
│  │  web_search_enabled ──► Internal + Tavily    │        │   │
│  │                         (both, combined)     │        │   │
│  │                                              │        │   │
│  │  default ──► Follow-up? ──YES──► use cache   │        │   │
│  │                │                             │        │   │
│  │                NO                            │        │   │
│  │                │                             │        │   │
│  │                ▼                             │        │   │
│  │          Internal Search                     │        │   │
│  │                │                             │        │   │
│  │          Confidence Check ──FAIL──► return   │        │   │
│  │                │              needs_web_     │        │   │
│  │               PASS            permission     │        │   │
│  │                │                             │        │   │
│  │                └─────────────────────────────┘        │   │
│  │                                              │        │   │
│  │                                         Tavily API    │   │
│  │                                         (web_search)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                  │
│                     SYNTHESISER                              │
│                   (Groq llama-3.3-70b)                       │
│                   Grounded LLM · temp=0.1                    │
│                   Citation builder (#page=N fragments)       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Runtime Data Flow (step-by-step)

| Step | Module | What happens |
|------|--------|--------------|
| 1 | `main.py` | Receives `ChatRequest`, validates, calls `run()` |
| 2 | `orchestrator.py` | Loads or creates `Session` for `conversation_id` |
| 3 | `query_parser.py` | Groq llama-3.3-70b (JSON mode, temp=0) extracts intent, customer entity, topic, OData metadata_filters |
| 4 | `orchestrator.py` | Checks routing flags (`web_only`, `web_search_enabled`) |
| 5 | `session.py` | `is_followup()` → reuse cached chunks if follow-up detected |
| 6 | `internal_search.py` | Azure AI Search hybrid query (vector + BM25 + RRF + semantic reranker), OData filters applied |
| 7 | `confidence.py` | Checks: count ≥ 2, reranker_score ≥ 1.0, customer_tag match |
| 8 | `web_search.py` | (if needed) Tavily API, returns top 3 results as ContentIQ-compatible dicts |
| 9 | `synthesiser.py` | Groq LLM, grounded on chunks, builds citations with `#page=N` URL fragments |
| 10 | `session.py` | `append_history()` + `update_retrieval()`, cap at 20 msgs |
| 11 | `main.py` | Returns `ChatResponse` with answer, citations, source_label, needs_web_permission |

---

## 3. Ingestion Pipeline (one-time CLI)

```
Azure Blob Storage
        │
        │  DefaultAzureCredential (Managed Identity)
        │  List all blobs in container
        ▼
  ingest_all.py  (CLI orchestrator)
  ┌─────────────────────────────────────────────────────┐
  │  --create-index  → Run indexer.py                   │
  │  --dry-run       → Skip upload, validate only       │
  │  --file PATH     → Single blob only                 │
  │  --skip-cu       → Reprocess from .cu_cache/ (no API cost, preferred for re-chunking) │
  │  --no-cu         → pypdf fallback (text only, no tables/figures)                     │
  └─────────────────────────────────────────────────────┘
        │
        ▼
  analyzer.py  (Azure Content Understanding REST)
  ┌─────────────────────────────────────────────────────┐
  │  API Version: 2024-12-01-preview                    │
  │  Analyzer: prebuilt-layout                          │
  │  POST blob URL → 202 Accepted → poll Operation-     │
  │  Location until status=Succeeded                    │
  │  Output: { markdown, tables, figures, pages }       │
  │                                                     │
  │  ✅ CU was run once for real in v1                  │
  │  Cache: .cu_cache/<blob_path>.json                  │
  │    └─ indigo_Indigo.pdf.json (71 MB confirmed)      │
  │       322 pages · 405 tables · 160 figures          │
  │       1.2M chars of structured markdown             │
  │                                                     │
  │  Re-ingestion workflow (after chunking changes):    │
  │    --skip-cu → reprocess from cache (no API cost)  │
  │    --no-cu   → pypdf fallback (text only, no figs) │
  └─────────────────────────────────────────────────────┘
        │
        ▼
  chunker.py  (token-based + figure extraction)
  ┌─────────────────────────────────────────────────────┐
  │  CHUNK_SIZE = 500 tokens (tiktoken cl100k_base)     │
  │  CHUNK_OVERLAP = 50 tokens                          │
  │  Split at: <!-- PageNumber="N" --> markers           │
  │  customer_tag from path: customers/Shell/ → "Shell" │
  │  Figures → standalone chunks (chart/image type)    │
  │  content_type auto-detection from figure keywords  │
  └─────────────────────────────────────────────────────┘
        │
        ▼
  embedder.py  (Azure OpenAI ADA-002)
  ┌─────────────────────────────────────────────────────┐
  │  embed_batch(): 100 chunks at a time                │
  │  Model: text-embedding-ada-002                      │
  │  Output: 1536-dim float32 vectors                   │
  │  Retry: tenacity, exponential backoff, 5 attempts   │
  └─────────────────────────────────────────────────────┘
        │
        ▼
  uploader.py  (Azure AI Search SDK)
  ┌─────────────────────────────────────────────────────┐
  │  upload_documents() in batches of 100               │
  │  Validates required fields before upload            │
  │  Reports success/failure per batch                  │
  └─────────────────────────────────────────────────────┘
        │
        ▼
  Azure AI Search Index  (content-iq-index)
  ┌─────────────────────────────────────────────────────┐
  │  Algorithm: HNSW (cosine)                           │
  │  Compression: BinaryQuantization (S1+ tier)         │
  │    truncation_dimension=1024, rescore top 10        │
  │  Vectorizer: Azure OpenAI (ada-002, 1536 dims)      │
  │  Semantic config:                                   │
  │    title=document_title                             │
  │    content=[content]                                │
  │    keywords=[extracted_caption]                     │
  └─────────────────────────────────────────────────────┘
```

---

## 4. Web Search Consent Flow

```
User asks question
       │
       ▼
Internal search runs
       │
       ▼
Confidence evaluator fails
(< 2 results OR reranker_score < 1.0 OR customer mismatch)
       │
       ▼
Backend returns: needs_web_permission=True
       │
       ▼
Frontend renders consent card:
  "Internal documents don't have enough information.
   Allow a web search for this query?"
   [ Allow ]  [ Decline ]
       │                 │
    Allow              Decline
       │                 │
       ▼                 ▼
Frontend resends     Card dismissed
with web_only=True   Toggle stays OFF
       │
       ▼
Orchestrator skips internal search
Calls Tavily API directly
Returns answer with source_label="WEB"
       │
       ▼
Frontend renders [WEB] badge (orange)
Toggle stays OFF for next query
```

---

## 5. Search Architecture (Azure AI Search)

```
Query: "What is Shell's cloud migration plan?"
       │
       ├── Embed query → 1536-dim vector
       │
       ├── BM25 keyword search (search_text="Shell cloud migration plan")
       │     ↑ query string prepended with customer name for keyword boost
       │
       ├── Vector search (k_nearest_neighbors = TOP_K * 2 = 10 candidates)
       │
       ├── RRF merge (Reciprocal Rank Fusion)
       │     Combines BM25 and vector rankings
       │
       ├── Semantic reranker (Azure AI Search semantic config)
       │     Re-scores top candidates on 0-4 scale
       │     Provides @search.reranker_score
       │
       └── OData filter (if applicable):
             customer_tag eq 'shell'
             content_type eq 'chart'
             $orderby=last_modified_date desc

Output: top 5 chunks with all metadata fields
```

### Confidence Thresholds
| Condition | Threshold | Notes |
|-----------|-----------|-------|
| Minimum results | 2 | `MIN_RESULTS_REQUIRED` |
| Semantic reranker score | 1.0 | 0-4 scale; tunable via `CONFIDENCE_THRESHOLD` env var |
| Customer match | exact | Normalized lowercase match on `customer_tag` |

---

## 6. Groq Client — Rate Limit Strategy

```
.env: GROQ_API_KEY_1, GROQ_API_KEY_2, GROQ_API_KEY_3

groq_client.py:
  _clients = [Groq(api_key=k) for k in valid_keys]
  _cycle = itertools.cycle(_clients)  # round-robin iterator

  chat_completion(messages, **kwargs):
    for attempt in 1..4:
      client = next(_cycle)
      try:
        return client.chat.completions.create(...)
      except RateLimitError (429):
        rotate key
        wait: 5s → 15s → 30s
    raise last exception
```

Used by: `query_parser.py` (temp=0.0, max_tokens=400) and `synthesiser.py` (temp=0.1, max_tokens=1024)

---

## 7. Session State

```python
@dataclass
class Session:
    conversation_id: str
    retrieved_chunks: list[dict]      # From last retrieval — reused for follow-ups
    history: list[dict[str, str]]     # [{"role": "user", "content": "..."}]
    last_entities: dict               # {"customer": "Shell", "topic": "cloud migration"}
    source_label: str                 # "INTERNAL" or "WEB"

# Key behaviours:
# - get(): creates empty session if conversation_id not seen before
# - append_history(): caps at 20 messages (10 turns)
# - is_followup(): True if (has chunks AND same/no customer AND short query OR signal word)
# - update_retrieval(): called after fresh internal search, stores chunks + entities
#
# Future v2: swap for Redis with same interface (zero orchestrator changes)
```

---

## 8. Frontend Architecture

```
frontend/src/
├── index.tsx                        ← React entry, RouterProvider
├── pages/
│   ├── chat/
│   │   ├── ContentIQChat.tsx        ← Main chat UI (v1, no auth)
│   │   │   State: messages, isLoading, error, webSearchEnabled,
│   │   │         pendingWebQuery, conversationIdRef
│   │   ├── Chat.tsx                 ← Full demo UI (auth, settings) — v2 ready
│   │   └── ContentIQChat.module.css
│   └── layout/Layout.tsx
├── api/
│   └── contentiqApi.ts              ← fetch() adapter to /chat + /health
│       VITE_BACKEND_URL env var (defaults to "" for same-origin)
├── components/
│   ├── CitationCard/CitationCard.tsx ← 📄📊📋🌐 icon + SourceBadge + click→new tab
│   └── SourceBadge/SourceBadge.tsx  ← [INTERNAL] blue | [WEB] orange pill

vite.config.ts:
  build.outDir: ../backend/static    ← Single deployment: FastAPI serves frontend
  proxy: /chat → localhost:8000      ← Dev mode only
  chunks: fluentui-icons, fluentui-react, vendor
```

---

## 9. API Contract

### POST /chat

**Request:**
```json
{
  "message": "What is Shell's cloud migration plan?",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "auth_token": null,
  "web_search_enabled": false,
  "web_only": false
}
```

**Response:**
```json
{
  "answer": "Shell's cloud migration plan focuses on...",
  "citations": [
    {
      "document_title": "shell_cloud_migration.pdf",
      "page_number": 3,
      "slide_number": null,
      "source_url": "https://storage.blob.core.windows.net/.../shell_cloud_migration.pdf#page=3",
      "content_type": "text",
      "source_label": "INTERNAL",
      "extracted_caption": null
    }
  ],
  "source_label": "INTERNAL",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "needs_web_permission": false
}
```

**When confidence fails:**
```json
{
  "answer": "",
  "citations": [],
  "source_label": "INTERNAL",
  "conversation_id": "...",
  "needs_web_permission": true
}
```

### GET /health
```json
{ "status": "ok", "service": "content-iq-backend" }
```

---

## 10. Deployment Layout

```
content-iq/
├── backend/
│   ├── main.py              ← uvicorn app (python -m main → 0.0.0.0:8000)
│   ├── static/              ← Vite build output (served by FastAPI)
│   ├── agent/               ← Runtime pipeline modules
│   ├── ingestion/           ← CLI ingestion pipeline (run once)
│   └── requirements.txt
├── frontend/                ← Source only; builds to backend/static/
└── .env                     ← Not committed; copy from .env.example

Dev:
  Terminal 1: cd backend && python -m main
  Terminal 2: cd frontend && npm run dev  (proxy → :8000)

Prod:
  npm run build  (outputs to backend/static/)
  python -m main  (FastAPI serves React + /chat API at :8000)
```

---

## 11. Azure Services Summary

| Service | Tier Requirement | Used for |
|---------|-----------------|---------|
| Azure Blob Storage | Any | Document storage (PDF/PPTX/DOCX), Managed Identity auth |
| Azure Content Understanding | Standard (quota-limited) | Document extraction at ingestion time — **run once, results cached in `.cu_cache/`** |
| Azure AI Search | **S1 or higher** (BinaryQuantization) | Hybrid search index (HNSW + BM25 + semantic reranker) |
| Azure OpenAI | Any | text-embedding-ada-002 (1536-dim embeddings only) |
| Groq API | Free/paid | llama-3.3-70b for query parsing + answer synthesis |
| Tavily API | Free/paid | Web search fallback (3 results max) |

> **AI Search tier note:** BinaryQuantization (`compression_name`) requires S1+. On Free/Basic tier, remove the `compression_name` from the vector profile in `ingestion/indexer.py` and re-create the index.
