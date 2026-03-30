# Content IQ — Product Requirements Document
**Version:** 3.0 (updated to reflect v1 implementation)
**Team:** Raghav · Sania · Yash
**Mentors:** Arvind · Srikantan
**Status:** v1 complete and committed

---

## 1. What Is Content IQ?

Content IQ is an **internal-first, document-grounded intelligence agent**. It lets users ask natural language questions over enterprise documents stored in Azure Blob Storage, retrieves the most relevant content, extracts precise answers, and returns responses with **explicit clickable citations** — including document name, page number, and a direct link to the source file.

If internal knowledge is insufficient (confidence below threshold), it falls back to a **Tavily web search**, clearly labelling the result as `[WEB]` so users always know the source. Web search requires explicit one-time user consent per query (unless the persistent toggle is enabled).

> **This is not a general chat assistant.**
> The LLM never answers from its own memory. Every response is grounded in retrieved content.

---

## 2. Where Content IQ Fits

There are 3 agents planned in total:

| Agent | Description | Status |
|---|---|---|
| **Content IQ** | Information retrieval over internal documents | ✅ Built (v1 complete) |
| Outcome Agent | Meeting summaries, client deliverables, email drafts | 🔜 Later |
| Sales Agent | Sales intelligence via MSX | 🔒 Parked (access issues) |

There is also a **TAB Agent** — the front-door orchestrator. Users will eventually talk to TAB, which routes to Content IQ based on intent. **We are not building TAB.** For v1, users interact with Content IQ directly. The `/chat` API is designed for clean TAB integration with zero refactoring.

---

## 3. Problem Statement

Enterprise knowledge is fragmented, poorly searchable, and hard to trust without attribution. Consultants need to quickly find what their organisation has produced for a specific client — without manually trawling through folders.

**What users want to ask:**
- "What have we presented to Shell recently?"
- "Which documents cover cloud migration for energy clients?"
- "What does the revenue chart in the Shell proposal show?"
- "What are the key deliverables from the Shell engagement?"

**What they don't want:**
- Hallucinated answers with no source
- Uncited summaries that could have come from anywhere
- Web results mixed silently with internal documents
- Having to open 10 files to find one fact

---

## 4. Goals and Non-Goals

### Goals (v1) — all implemented
- Retrieve the right documents from Azure Blob Storage based on user query
- Extract precise answers from within those documents (text, tables, charts)
- Always show where the answer came from: document name + page/slide + clickable URL
- Prefer internal knowledge over external at all times
- Fall back to Tavily web search only when internal confidence is below threshold — and label it `[WEB]`
- Require explicit one-time consent before falling back to web search (unless toggle is on)
- Support follow-up questions grounded in already-retrieved context
- Handle multimodal content: charts, tables, images using Azure Content Understanding
- Clean, functional chat web UI with source badges and citation cards

### Non-Goals (v1)
- SharePoint integration (v2)
- Meeting transcription or recall
- Agenda generation (reused from TAB later)
- Sales intelligence
- Per-user access control (scaffolded with `allowed_groups` field, not enforced)
- Real-time document sync (batch ingestion CLI is sufficient)
- TAB Agent integration (v1 is standalone)

---

## 5. Data Sources

### v1 — Primary
| Source | How documents get in | Connector |
|---|---|---|
| **Azure Blob Storage** | Manual upload via Azure Portal | Custom Python ingestion CLI (`ingest_all.py`) |

> **Implementation note:** v1 uses a custom ingestion pipeline (not the AI Search Blob Indexer). The CLI runs manually or on a schedule. Documents are processed by Azure Content Understanding, chunked, embedded, and uploaded to AI Search in batches.

### Folder Structure Convention (enforced from day 1)
```
blob-container/
  customers/
    Shell/
      shell_proposal_q4_2024.pdf          → customer_tag="Shell"
      shell_cloud_migration.pptx
    IndiGo/
      indigo_digital_brief.pdf            → customer_tag="IndiGo"
    BP/
      bp_sustainability.pdf               → customer_tag="BP"
  internal/
    general/
      energy_sector_trends.pdf            → customer_tag="internal"
```
The folder path is used to populate `customer_tag` automatically during indexing. No manual tagging required.

### v2 — Additional Source
| Source | Notes |
|---|---|
| **SharePoint** | Added later via MS Graph API connector. Same indexing pipeline. |

### External Fallback
- **Tavily Search API** — triggered only when internal confidence score is below threshold AND the user consents
- Results are labelled `[WEB]` — never mixed silently with internal results
- Web search toggle: persistent ON/OFF toggle in the UI; also supported as one-time consent per query

---

## 6. Key Design Decisions

### 6.1 Why Azure Content Understanding (not Document Intelligence alone)
Azure Content Understanding is the evolved version of Document Intelligence. It handles everything in one service:
- Extracts text from PDFs, DOCX, PPTX
- Extracts tables as structured rows/columns
- **Extracts charts/graphs into data descriptions** — critical for answering questions about charts
- Captions images
- Outputs clean Markdown that feeds directly into the RAG pipeline

The LLM never sees raw images. It sees structured text extracted by CU at indexing time. CU was run once against the actual documents — results are cached in `.cu_cache/`. Subsequent re-ingestions (e.g. after chunking logic changes) use `--skip-cu` to reprocess from cache without calling the API again. A `--no-cu` (pypdf) flag also exists but is only for plain-text-only scenarios.

### 6.2 Why a Custom Ingestion CLI (not AI Search Blob Indexer)
The custom pipeline (`ingest_all.py`) was chosen over the Blob Indexer to:
- Integrate Azure Content Understanding (CU) as the document parser
- Support a local result cache (`.cu_cache/`) so CU only needs to be called once — re-ingestions after chunking changes reuse the cache via `--skip-cu`
- Allow `--dry-run` mode for validation before uploading
- Support selective re-indexing of a single file (`--file` flag)
- Give full control over chunking strategy (500 tokens, 50 overlap, with figure extraction)
- Enable pypdf fallback (`--no-cu`) for plain-text-only scenarios where CU is not needed

### 6.3 Why Groq (not Azure OpenAI) for LLM
v1 uses **Groq llama-3.3-70b-versatile** for query parsing and answer synthesis. Reasons:
- Free tier available for development
- Fast inference latency
- Three API keys rotated round-robin to handle rate limits
- Azure OpenAI (`text-embedding-ada-002`) is still used for embeddings, which require ADA-002 dimensions

### 6.4 Why Tavily (not Bing) for Web Search
- Simpler API (single POST, no Azure subscription required)
- Returns clean snippets ready for synthesis
- Easy to swap with Bing v7 in production by replacing `web_search.py`
- Web search is always opt-in — requires user consent per query unless the persistent toggle is ON

### 6.5 Why Hybrid Search
Azure AI Search supports hybrid retrieval — vector search + BM25 keyword search merged via RRF (Reciprocal Rank Fusion), followed by a semantic reranker. This gives the best results: semantic meaning AND exact keyword matching. Both are used.

### 6.6 Why Metadata Matters
RAG alone cannot answer:
- "Most recent Shell document" — needs `last_modified_date` sorted
- "Who wrote this?" — needs `author`
- "All Shell documents" — needs `customer_tag` filtered

Metadata is stored at indexing time and queried explicitly by the orchestrator via OData filters.

---

## 7. Architecture

### 7.1 High-Level Runtime Flow

```
User (Chat UI)
    │  HTTP POST /chat
    │  { message, conversation_id, web_search_enabled, web_only }
    ▼
FastAPI Backend  (main.py)
    │  Validates request, calls orchestrator
    ▼
Agent Orchestrator  (agent/orchestrator.py)
    │
    ├─ 1. Session Retrieval (in-memory SessionStore)
    │
    ├─ 2. Query Parser  (Groq llama-3.3-70b)
    │       Extracts: intent · customer entity · topic · time constraint
    │       Builds: metadata_filters (OData) for AI Search
    │
    ├─ 3. Route decision:
    │       web_only=True?        → skip to Tavily directly
    │       web_search_enabled?   → run internal + Tavily, combine
    │       else                  → internal-only path (steps 4-6)
    │
    ├─ 4. Follow-up Detection
    │       Short query (≤8 words) OR signal word (that/this/why/explain…)
    │       AND same customer entity AND cached chunks exist?
    │       → YES: reuse cached chunks, skip retrieval
    │       → NO: continue to retrieval
    │
    ├─ 5. Internal Search  (agent/internal_search.py)
    │       → Azure AI Search: vector + BM25 + RRF + semantic reranker
    │       → OData filters: customer_tag, content_type, sort
    │
    ├─ 6. Confidence Evaluator  (agent/confidence.py)
    │       Checks: result count ≥ 2, reranker score ≥ 1.0, customer match
    │       → Pass: chunks go to synthesiser
    │       → Fail: return needs_web_permission=True (ask user for consent)
    │
    ├─ 7. Synthesiser  (agent/synthesiser.py, Groq llama-3.3-70b)
    │       Grounded LLM synthesis from retrieved chunks only
    │       Builds citation list with #page=N URL fragments
    │
    └─ 8. Session Update (append history, store chunks for follow-up)

    ▼
ChatResponse: { answer, citations, source_label, conversation_id, needs_web_permission }
    ▼
React Frontend  (ContentIQChat.tsx)
    ├── Renders markdown answer
    ├── Renders CitationCard per citation (icon + badge + clickable link)
    ├── Shows consent card if needs_web_permission=True
    └── INTERNAL badge (blue) | WEB badge (orange)
```

### 7.2 Ingestion Flow (one-time CLI)

```
Blob Storage
    │  List all blobs (azure-storage-blob SDK)
    ▼
analyzer.py  (Azure Content Understanding REST API)
    │  POST document URL → poll until Succeeded
    │  Extracts: text, tables, figures, per-page markdown
    │  Cache: .cu_cache/ (skip CU API on re-runs)
    │  Fallback: pypdf (--no-cu flag)
    ▼
chunker.py
    │  Split markdown at <!-- PageNumber="N" --> markers
    │  500-token chunks, 50-token overlap (tiktoken cl100k_base)
    │  Extract figures separately → chart/image content_type chunks
    │  Attach all metadata (customer_tag from folder path, etc.)
    ▼
embedder.py  (text-embedding-ada-002, Azure OpenAI)
    │  embed_batch(): 100 chunks at a time, tenacity retry on rate limit
    │  Returns 1536-dim float vectors
    ▼
uploader.py  (azure-search-documents SDK)
    │  upload_documents() in batches of 100
    │  Optional --dry-run: validate without uploading
    ▼
Azure AI Search Index  (content-iq-index)
    HNSW + BinaryQuantization (S1+ tier required)
    Semantic config: title=document_title, content=content, keywords=extracted_caption
```

### 7.3 Component Table

| Component | Implementation | Purpose |
|---|---|---|
| **API Layer** | FastAPI (`main.py`) + uvicorn | HTTP interface, request validation, CORS |
| **Agent Orchestrator** | Pure Python (`agent/orchestrator.py`) | Query routing, session management, internal-first policy |
| **Query Parser** | Groq llama-3.3-70b (`agent/query_parser.py`) | Extracts intent, entities, OData filters from user query |
| **Session Store** | In-memory dict (`agent/session.py`) | Conversation history + cached chunks per `conversation_id` |
| **Internal Search** | Azure AI Search SDK (`agent/internal_search.py`) | Hybrid vector + BM25 + RRF + semantic reranker |
| **Confidence Evaluator** | Pure Python (`agent/confidence.py`) | Decides if internal results are good enough |
| **Web Search** | Tavily API (`agent/web_search.py`) | External fallback, results tagged `[WEB]` |
| **Synthesiser** | Groq llama-3.3-70b (`agent/synthesiser.py`) | Grounded answer synthesis + citation formatting |
| **Groq Client** | `agent/groq_client.py` | Round-robin key rotation (3 keys), 429 retry with backoff |
| **Document Store** | Azure Blob Storage | Raw file storage, organized by customer/project folders |
| **Content Extraction** | Azure Content Understanding REST (`ingestion/analyzer.py`) | PDFs/PPTX/DOCX → structured Markdown |
| **Chunker** | tiktoken + custom logic (`ingestion/chunker.py`) | 500-token overlapping chunks + figure extraction |
| **Embedder** | Azure OpenAI ADA-002 (`ingestion/embedder.py`) | 1536-dim embeddings with tenacity retry |
| **Index Schema** | Azure AI Search SDK (`ingestion/indexer.py`) | HNSW + BinaryQuantization + semantic config |
| **Uploader** | Azure AI Search SDK (`ingestion/uploader.py`) | Batch embed + upload (100 chunks/batch) |
| **Chat UI** | React 19 + Vite + Fluent UI (`frontend/`) | Chat interface, citation cards, source badges |
| **Citation Card** | React component (`CitationCard.tsx`) | Clickable card with icon, location, badge |
| **Source Badge** | React component (`SourceBadge.tsx`) | Blue [INTERNAL] / Orange [WEB] pill badge |

---

## 8. Metadata Schema

Stored at indexing time on every chunk. Required for accurate retrieval and citation generation.

| Field | Type | Indexed | Purpose |
|---|---|---|---|
| `id` | String (UUID) | key | Unique key per chunk |
| `content` | String | searchable | Text content (BM25 search, en.microsoft analyzer) |
| `content_vector` | Vector (1536 dims) | searchable, hidden | Embedding for semantic similarity search |
| `document_title` | String | searchable, filterable | Shown in citations |
| `source_url` | URL | filterable | Direct link to Blob file (+ `#page=N` fragment appended by synthesiser) |
| `page_number` | Integer | filterable, sortable | Makes citations precise ("Page 3") |
| `slide_number` | Integer | filterable, sortable | For PPTX files |
| `content_type` | Enum | filterable, facetable | `text` / `table` / `chart` / `image` |
| `customer_tag` | String | filterable, facetable | Derived from folder path (e.g. "Shell", "internal") |
| `author` | String | searchable, filterable | From blob metadata (empty in v1) |
| `created_date` | DateTime | filterable, sortable | For temporal queries |
| `last_modified_date` | DateTime | filterable, sortable | Enables "most recent" queries |
| `chunk_index` | Integer | filterable, sortable | Position in document |
| `extracted_caption` | String | searchable | Caption/description for charts and images (semantic keywords) |
| `allowed_groups` | String[] | filterable | v2 RBAC placeholder — `["all"]` in v1 |

---

## 9. Orchestrator Routing Logic

The orchestrator (`agent/orchestrator.py`) controls all routing — not the LLM.

```
1. Load session (conversation_id → Session dataclass)
2. Parse query → intent, entities, metadata_filters (Groq, JSON mode, temp=0.0)
3. If web_only=True → go to step 7 (skip internal, user already consented)
4. If web_search_enabled=True → run internal + Tavily in parallel, merge
5. Else:
   a. Follow-up check: short query + same customer + cached chunks → reuse chunks
   b. Internal search: Azure AI Search hybrid with OData filters
   c. Confidence evaluation → if fails, return needs_web_permission=True
6. Synthesise: Groq LLM, grounded on chunks only, temp=0.1
7. Update session: append history (cap 20 msgs), store chunks
8. Return ChatResponse
```

---

## 10. Retrieval and Confidence Logic

### Hybrid Search Strategy
- **Vector search:** query embedded → cosine similarity against chunk vectors
- **BM25 keyword search:** exact term matching (boosted by prepending customer name to query)
- **RRF merge:** both result sets re-ranked by Reciprocal Rank Fusion
- **Semantic reranker:** Azure AI Search re-scores top candidates (0-4 scale)
- **OData filters:** `customer_tag eq 'shell'`, `content_type eq 'chart'`, `$orderby=last_modified_date desc`

### Confidence Evaluator — Fails (triggers web permission request) when ANY:
- Fewer than 2 results returned (`MIN_RESULTS_REQUIRED = 2`)
- Top result's `@search.reranker_score` < `CONFIDENCE_THRESHOLD` (default `1.0`, env-tunable, 0-4 scale)
- Specific customer was requested but not found in any result's `customer_tag`

### Web Search Consent Flow
1. Internal confidence fails → backend sets `needs_web_permission=True`
2. Frontend renders permission card: "Allow web search for this query?"
3. **Allow:** Frontend resends with `web_only=True` → Tavily results, source_label=`WEB`
4. **Decline:** Card dismissed, toggle remains OFF, conversation continues
5. Persistent toggle ON: skips the consent step entirely, always web-augmented

---

## 11. Answer Generation Rules

### The LLM must:
- Synthesise answers **only** from retrieved passages
- Attach a citation to **every** factual claim
- State document name, page/slide number in every citation
- Return clickable source URLs (with `#page=N` fragment for PDFs)
- Label web results `[WEB]` clearly in the source badge
- Say "I could not find this in our internal documents." when nothing relevant is retrieved

### The LLM must not:
- Answer from its own training knowledge
- Make any claim without a retrieved source
- Mix web results silently with internal data
- Hallucinate document names or page numbers

### Synthesiser System Prompt (key rules)
- Format: Markdown (bold, bullets, headers)
- Length: concise — 2-5 sentences or a short list
- Grounding: passage references only
- Includes last 4 history turns for follow-up context
- Temperature: 0.1 (low creativity, factual accuracy)

---

## 12. Follow-Up Question Handling

After initial retrieval, chunks are stored in session memory per `conversation_id`. Follow-up detection in `session.py`:

**Is a follow-up when ALL of:**
- Session has non-empty `retrieved_chunks`
- No new customer entity introduced (same or none)
- Query is short (≤ 8 words) OR starts with a signal word: `that`, `this`, `it`, `why`, `explain`, `tell me more`, `what about`, `how`, `summarize`, `show me`, `describe`

| Scenario | Behaviour |
|---|---|
| "Who authored that?" | Answered from session memory. No new retrieval. |
| "Explain the chart on slide 7 more" | Follow-up detected → reuse cached chunks |
| "What about our work with BP?" | New entity (BP) detected → fresh retrieval triggered |
| "What did we discuss at the Shell meeting?" | Out of scope (meeting recall = Outcome Agent) |

---

## 13. Multimodal Handling

**Key principle:** The LLM never sees raw images. Multimodality is handled at ingestion time by Azure Content Understanding.

### What Content Understanding extracts
| File / Content Type | What CU extracts |
|---|---|
| PDF text | Clean structured Markdown preserving headings and layout |
| PDF tables | Structured rows and columns as Markdown tables |
| PDF/PPTX charts | Data descriptions — e.g. "Bar chart: Q1 revenue $2.3M, Q2 $2.8M" |
| PPTX slides | Slide-level text, titles, table content, chart data |
| Embedded images | Caption + visual description |
| DOCX | Full text, tables, and embedded figure captions |

### Figure Extraction in chunker.py
Each figure becomes a **standalone chunk** with `content_type="chart"` or `"image"`. Three fallback strategies for extracting figure content:
1. `caption.content` field (future CU API versions)
2. Markdown span offset (character range lookup in raw markdown)
3. Element paths (traverse raw CU response structure)

`content_type` is auto-classified from keywords: "chart", "graph", "bar", "pie", "trend", "revenue" → `"chart"`; everything else → `"image"`.

---

## 14. Access Control — Scaffolded for v2

**Not enforced in v1. Built for trivial addition.**

Architecture in place:
- `auth_token` parameter accepted in `ChatRequest` (not validated in v1)
- `allowed_groups: ["all"]` stored on every indexed chunk
- `allowed_groups` is filterable in AI Search — OData filter can be added to `internal_search.py` in one line
- When v2 ships: validate Azure AD token, extract group memberships, filter at query time

---

## 15. Chat UI

### Stack (actual implementation)
- **Framework:** React 19 + Vite (not Next.js)
- **Component library:** Microsoft Fluent UI (`@fluentui/react-components`)
- **Markdown rendering:** `react-markdown`
- **API:** `fetch()` to FastAPI `/chat` (`contentiqApi.ts`)
- **Session:** `conversation_id` UUID in React state (ref), sent with every request
- **Build output:** `../backend/static/` (served by FastAPI in production)

### Layout
- Chat history: user messages right-aligned, agent responses left-aligned
- Agent response rendered as Markdown (bold, bullets, headers)
- Citations section below each response (list of `CitationCard` components)
- Each `CitationCard`: document name + page/slide label + source badge + clickable external link
- `SourceBadge`: `[INTERNAL]` = dark blue, `[WEB]` = orange
- Citation icons: 📄 text, 📊 chart, 📋 table, 🌐 web
- Web search toggle (top of chat, default OFF)
- One-time consent card rendered inline when `needs_web_permission=True`

### Key UI State
| State | Type | Purpose |
|---|---|---|
| `messages` | `ChatMessage[]` | Full chat history rendered |
| `isLoading` | boolean | Disables input while request in-flight |
| `error` | `string \| null` | Error banner |
| `webSearchEnabled` | boolean | Persistent toggle (default OFF) |
| `pendingWebQuery` | `string \| null` | Query waiting for one-time web consent |
| `conversationIdRef` | UUID ref | Session ID (persists across turns, reset on page reload) |

---

## 16. Environment Variables

```bash
# Azure OpenAI (Embeddings only in v1)
AZURE_OPENAI_ENDPOINT=https://....openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o                        # Not used in v1 (Groq is primary LLM)
AZURE_OPENAI_EMB_DEPLOYMENT=text-embedding-ada-002    # Used for all embeddings

# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://....search.windows.net
AZURE_SEARCH_KEY=...
AZURE_SEARCH_INDEX_NAME=content-iq-index

# Azure Content Understanding (ingestion only)
AZURE_CU_ENDPOINT=https://....cognitiveservices.azure.com/
AZURE_CU_KEY=...

# Azure Blob Storage (Managed Identity — no key required at runtime)
AZURE_STORAGE_ACCOUNT_NAME=mycompanydocs
AZURE_STORAGE_CONTAINER=documents

# Groq (Primary LLM for parsing + synthesis)
GROQ_API_KEY_1=...
GROQ_API_KEY_2=...    # optional — enables round-robin rotation
GROQ_API_KEY_3=...    # optional
GROQ_MODEL=llama-3.3-70b-versatile

# Tavily (Web search fallback)
TAVILY_API_KEY=...

# Tuning
CONFIDENCE_THRESHOLD=1.0   # Semantic reranker score threshold (0-4 scale)
TOP_K_RESULTS=5            # Chunks returned from AI Search per query
```

---

## 17. Ingestion CLI

```bash
# Create/update the AI Search index schema
python -m ingestion.ingest_all --create-index

# Full ingestion (all blobs) — calls CU API, results cached in .cu_cache/
python -m ingestion.ingest_all

# Single file
python -m ingestion.ingest_all --file customers/Shell/proposal.pdf

# Validate without uploading
python -m ingestion.ingest_all --dry-run

# Re-chunk using cached CU results (no API call — preferred for logic changes)
python -m ingestion.ingest_all --skip-cu

# Use pypdf instead of CU (PDFs only, no tables/charts/figures)
python -m ingestion.ingest_all --no-cu
```

**Ingestion workflow used in v1:**
1. Ran `ingest_all.py` once (default mode) → CU called for all documents → results cached in `.cu_cache/`
2. When chunking logic was updated, ran with `--skip-cu` → reprocessed from cache without re-calling CU

**Verified cache contents** (`.cu_cache/indigo_Indigo.pdf.json`, 71 MB):
- 322 pages · 405 tables · 160 figures · 1.2M characters of structured markdown

**Note:** BinaryQuantization requires **Azure AI Search S1 tier or higher**. On Free/Basic tier, remove `compression_name` from the vector profile in `indexer.py`.

---

## 18. Tech Stack Summary

| Layer | Technology |
|---|---|
| Backend language | Python 3.11 |
| API framework | FastAPI + uvicorn |
| Primary LLM (parse + synthesise) | Groq llama-3.3-70b-versatile |
| Embeddings | Azure OpenAI text-embedding-ada-002 (1536 dims) |
| Vector + keyword search | Azure AI Search (HNSW + BM25 + RRF + semantic reranker) |
| Document extraction | Azure Content Understanding (prebuilt-layout, 2024-12-01-preview) |
| Document storage | Azure Blob Storage (Managed Identity auth) |
| Web fallback | Tavily Search API |
| Retry / resilience | tenacity (embedder), custom retry loop (Groq 429) |
| Frontend | React 19 + TypeScript + Vite |
| UI components | Microsoft Fluent UI v9 |
| Markdown rendering | react-markdown |
| Routing | React Router v7 |

---

## 19. Deliverables for v1 — Status

- [x] Working Content IQ agent answering questions with grounded responses
- [x] Every response includes summary answer + citations (doc name, page/slide, clickable URL)
- [x] Multimodal support — charts and tables indexed as structured text via CU
- [x] Web search fallback fires correctly — one-time consent + labelled `[WEB]`
- [x] Follow-up questions answered from session memory (no full re-retrieval)
- [x] Clean chat UI with citation cards and source badges
- [x] Architecture diagram (see `Docs/architecture.md`)
- [x] Ingestion CLI with dry-run, single-file, and cache support

---

## 20. Future Phases

| Phase | What it adds |
|---|---|
| v2 | SharePoint as a second data source via MS Graph API |
| v2 | Per-user access control via Azure AD / Entra ID (using `allowed_groups` already in schema) |
| v2 | Redis-backed session store (swap `session.py`, zero API changes) |
| v3 | Integration with TAB Agent (Content IQ becomes a sub-agent via `/chat` API) |
| v3 | Outcome Agent built separately, then connected |
| Later | Sales Agent (pending MSX access) |
