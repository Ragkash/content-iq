# Content IQ

**Enterprise Document Intelligence Agent** — ask natural-language questions over your internal document library and get grounded, cited answers with direct links to the source page or slide.

Built for consulting firms that need to surface knowledge locked inside PDFs, Word docs, and PowerPoint decks stored in Azure Blob Storage. Content IQ never hallucinates from training data — every answer is backed by an explicit citation. When internal confidence is low it falls back to Tavily web search, clearly labelled `[WEB]`.

---

## Features

- **Grounded answers only** — LLM is instructed to answer exclusively from retrieved passages; if it can't, it says so.
- **Explicit citations** — every response surfaces document name, page/slide number, and a direct clickable URL (with `#page=N` fragment for PDFs).
- **Hybrid search** — vector similarity + BM25 keyword search, merged via Reciprocal Rank Fusion and re-ranked by Azure's semantic ranker.
- **Multimodal ingestion** — Azure Content Understanding extracts text, tables, and figures from PDFs and PowerPoints; charts become queryable standalone chunks.
- **Confidence-gated web fallback** — if internal results are sparse or off-topic, the agent requests one-time consent to search the web via Tavily. Users can also enable a persistent web-augment toggle.
- **Follow-up memory** — the session store detects follow-up questions (short query, same customer context) and reuses the previous retrieval instead of re-querying.
- **Metadata-aware filtering** — query parser extracts customer names, topics, and time constraints; AI Search applies OData filters automatically.
- **Customer tagging** — blob folder path (`customers/Shell/`) is used at ingestion time to tag every chunk with `customer_tag`, enabling precise per-client scoping.
- **Fluent UI chat interface** — Copilot-inspired chat UI with citation cards, source badges, and a web search toggle.
- **Automated Substack ingestion** — weekly Azure Function scrapes `sandeepalur.substack.com`, chunks each post, and upserts it into the same AI Search index so thought leadership content stays current automatically.

---

## Architecture

### Runtime Query Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                          React Chat UI                              │
│  (ContentIQChat.tsx — web toggle, citation cards, session UUID)     │
└────────────────────────────┬────────────────────────────────────────┘
                             │  POST /chat
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    FastAPI  (main.py :8000)                         │
│  ChatRequest → validate → route → ChatResponse                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Orchestrator                                  │
│                     (orchestrator.py)                               │
│                                                                     │
│  1. Session lookup / create  (session.py)                           │
│  2. Query parse  ──────────► QueryParser                            │
│                              (query_parser.py, Groq llama-3.3-70b) │
│                              → intent, entities, OData filters      │
│  3. Follow-up check ────────► SessionStore.is_followup()           │
│                              reuse cached chunks if True            │
│  4. Internal search ────────► InternalSearchTool                   │
│                              (internal_search.py)                   │
│                              vector + BM25 + RRF + semantic rerank  │
│  5. Confidence check ───────► ConfidenceEvaluator                  │
│                              (confidence.py)                        │
│                              → pass / needs_web_permission=True     │
│  6. Web fallback (opt-in) ──► WebSearchTool                        │
│                              (web_search.py, Tavily API)            │
│  7. Synthesis ──────────────► Synthesiser                          │
│                              (synthesiser.py, Groq llama-3.3-70b)  │
│                              → grounded answer + citation list      │
│  8. Session update           store chunks, history, entities        │
└─────────────────────────────────────────────────────────────────────┘
```

### Ingestion Pipeline (one-time CLI)

```
Azure Blob Storage
       │
       │  blob URLs (PDF, DOCX, PPTX)
       ▼
  analyzer.py  ──►  Azure Content Understanding (prebuilt-layout)
                     extracts text, tables, figures as Markdown
                     results cached in .cu_cache/ (no repeat API cost)
       │
       ▼
  chunker.py   ──►  500-token chunks, 50-token overlap
                     splits at page boundaries
                     figures → standalone chunks (content_type=chart/image)
                     customer_tag extracted from blob path
       │
       ▼
  embedder.py  ──►  Azure OpenAI text-embedding-ada-002
                     1536-dim vectors, batch of 100, tenacity retry
       │
       ▼
  uploader.py  ──►  Azure AI Search
                     HNSW index, BinaryQuantization compression
                     semantic config on document_title + content
```

### Confidence & Web Fallback Flow

```
Internal results returned
         │
         ▼
  ConfidenceEvaluator
  ├── results < 2 ?          ─► needs_web_permission = True
  ├── customer entity not
  │   found in any chunk?   ─► needs_web_permission = True
  └── pass                  ─► synthesise from internal chunks
         │
  if web_search_enabled=True or webOnly=True:
         └──► Tavily search → merge with internal (or replace)
```

---

## Substack Scraper & Weekly Cron Job

Content IQ automatically ingests posts from `sandeepalur.substack.com` on a weekly schedule using an Azure Functions timer trigger. This keeps the AI Search index fresh with new thought leadership without any manual steps.

### How it works

```
rss2json proxy  ──►  fetch_all_posts()
(bypasses Cloudflare      pulls title, URL, published_date,
 on Azure datacenter IPs)  and full article text from RSS feed
       │
       ▼
  new posts only  ──►  state.json in Blob Storage tracks
                        which URLs have already been indexed
       │
       ▼
  chunk_post()   ──►  500-token chunks, 50-token overlap
                       same metadata schema as PDF/PPTX chunks
                       customer_tag = "internal"
                       author = "Sandeep Alur"
                       source_url = live Substack post URL (direct citation)
       │
       ├──►  save {slug}.json to Blob Storage  (audit trail)
       │
       └──►  embed_batch() + upload_chunks()  → Azure AI Search
```

**Fallback chain for content extraction:**
1. `rss2json` response includes full article HTML — parsed to plain text directly.
2. If empty: HTML scrape of the live post page (`div.body.markup`, `div.post-content`, `article`).
3. If blocked: Substack per-slug API endpoint (`/api/v1/posts/by-slug/{slug}`).

### Azure Function timer trigger

The scraper is deployed as an Azure Function (`backend/scraper/function_app.py`). Azure reads the `@app.timer_trigger` decorator and fires it automatically — no external scheduler needed.

```python
@app.timer_trigger(
    schedule="0 0 9 * * 1",   # every Monday at 09:00 UTC
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def substack_weekly_ingest(timer: func.TimerRequest) -> None:
    run(dry_run=False, reset=False)
```

**Cron expression breakdown:** `0 0 9 * * 1` → second=0, minute=0, hour=9, any day-of-month, any month, Monday only.

### Scraper files

```
backend/scraper/
├── function_app.py       ← Azure Function entry point (timer trigger)
├── substack_scraper.py   ← scrape → chunk → embed → upload pipeline
├── embedder.py           ← copy of ingestion/embedder.py for standalone deploy
├── uploader.py           ← copy of ingestion/uploader.py for standalone deploy
├── host.json             ← Azure Functions runtime config (v2, extension bundle 4.x)
├── local.settings.json   ← local dev env vars (not committed)
└── requirements.txt      ← Azure Oryx build dependencies
```

### Running locally

```bash
cd backend/scraper
pip install -r requirements.txt

# Ingest all new posts (skips already-indexed URLs via state.json):
python substack_scraper.py

# Dry run — saves JSONs to Blob but skips AI Search upload:
python substack_scraper.py --dry-run

# Full reset — clears state and reingests every post from scratch:
python substack_scraper.py --reset
```

### Blob Storage layout (scraper)

```
<RAW_DOCUMENT_CONTAINER>/
  sandeep-alur/
    state.json           ← list of already-indexed post URLs + last_run timestamp
    run_log.json         ← last run stats (posts discovered, ingested, skipped, chunks)
    {slug}.json          ← raw scraped post data (title, content, word_count, scraped_at)
```

### Additional env vars (scraper only)

| Variable | Description |
|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string (scraper uses this instead of account name) |
| `RAW_DOCUMENT_CONTAINER` | Container for scraped JSON files (default: `container`) |

---

## Repo Structure

```
content-iq/
├── .env.example                  ← environment variable template
├── .gitignore
├── README.md
├── Docs/
│   ├── content_iq_prd.md         ← product requirements
│   ├── content_iq_buildplan.md   ← phased build checklist (P0–P11)
│   └── architecture.md           ← detailed architecture reference
├── backend/
│   ├── main.py                   ← FastAPI: POST /chat, GET /health
│   ├── requirements.txt
│   ├── sdk_patch.py              ← Azure SDK timeout patches
│   ├── agent/
│   │   ├── orchestrator.py       ← main routing pipeline
│   │   ├── query_parser.py       ← Groq: intent / entity / filter extraction
│   │   ├── internal_search.py    ← Azure AI Search hybrid (vector + BM25 + RRF)
│   │   ├── confidence.py         ← fallback trigger logic
│   │   ├── web_search.py         ← Tavily web search fallback
│   │   ├── synthesiser.py        ← grounded LLM synthesis + citation builder
│   │   ├── session.py            ← in-memory conversation store
│   │   └── groq_client.py        ← Groq client with 3-key round-robin
│   ├── ingestion/
│   │   ├── ingest_all.py         ← CLI orchestrator (entry point)
│   │   ├── analyzer.py           ← Azure Content Understanding REST wrapper
│   │   ├── chunker.py            ← token-based chunker + metadata tagger
│   │   ├── embedder.py           ← ADA-002 embeddings, batch + retry
│   │   ├── indexer.py            ← AI Search index schema creator
│   │   └── uploader.py           ← batch embed + upload to index
│   └── scraper/
│       ├── function_app.py       ← Azure Function timer trigger (every Monday 09:00 UTC)
│       ├── substack_scraper.py   ← scrape → chunk → embed → upload pipeline
│       ├── embedder.py           ← standalone copy for Azure deployment
│       ├── uploader.py           ← standalone copy for Azure deployment
│       ├── host.json             ← Azure Functions runtime config
│       ├── local.settings.json   ← local dev env vars (not committed)
│       └── requirements.txt      ← Azure Oryx build dependencies
└── frontend/
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── api/
        │   └── contentiqApi.ts         ← fetch adapter for FastAPI /chat
        ├── components/
        │   ├── CitationCard/
        │   │   ├── CitationCard.tsx    ← clickable citation card
        │   │   └── CitationCard.module.css
        │   └── SourceBadge/
        │       ├── SourceBadge.tsx     ← INTERNAL (blue) / WEB (orange) pill
        │       └── SourceBadge.module.css
        └── pages/chat/
            ├── ContentIQChat.tsx       ← main chat UI
            └── ContentIQChat.module.css
```

---

## Quickstart

### Prerequisites

- Python 3.11+
- Node.js 20+
- Azure subscription with: AI Foundry (OpenAI), AI Search (S1+), Blob Storage, Content Understanding
- Groq API key (free tier works for dev)
- Tavily API key (free tier works for dev)

### 1. Clone

```bash
git clone <repo-url>
cd content-iq
```

### 2. Configure environment

```bash
cp .env.example .env
# Open .env and fill in all values — see the table below
```

### 3. Install backend dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Upload documents to Blob Storage

Upload your PDFs, DOCX, and PPTX files to the correct folder in your Azure Blob container:

```
documents/customers/Shell/proposal.pdf
documents/customers/IndiGo/deck.pptx
documents/internal/general/playbook.docx
```

### 5. Run ingestion

```bash
# From backend/
# Step A: create the AI Search index schema
python -m ingestion.ingest_all --create-index

# Step B: ingest all documents (calls Azure Content Understanding, embeds, uploads)
python -m ingestion.ingest_all

# Re-ingest without paying for Content Understanding again (uses local cache):
python -m ingestion.ingest_all --skip-cu

# Ingest a single file:
python -m ingestion.ingest_all --file customers/Shell/proposal.pdf

# Dry run (validate pipeline without uploading):
python -m ingestion.ingest_all --dry-run
```

### 6. Start the backend

```bash
# From backend/
uvicorn main:app --reload --port 8000
# Swagger docs: http://localhost:8000/docs
# Health check: http://localhost:8000/health
```

### 7. Start the frontend

```bash
# From frontend/
npm install
npm run dev
# App: http://localhost:5173
```

---

## Environment Variables

Copy `.env.example` to `.env` in the project root and fill in the values below.

### Azure OpenAI (embeddings)

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_EMB_DEPLOYMENT` | Embedding deployment name (default: `text-embedding-ada-002`) |

### Azure AI Search

| Variable | Description |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | AI Search resource endpoint |
| `AZURE_SEARCH_KEY` | AI Search admin key |
| `AZURE_SEARCH_INDEX_NAME` | Index name (default: `content-iq-index`) |

### Azure Content Understanding (ingestion only)

| Variable | Description |
|---|---|
| `AZURE_CU_ENDPOINT` | Content Understanding endpoint |
| `AZURE_CU_KEY` | Content Understanding API key |

### Azure Blob Storage

| Variable | Description |
|---|---|
| `AZURE_STORAGE_ACCOUNT_NAME` | Storage account name (used by ingestion pipeline) |
| `AZURE_STORAGE_CONTAINER` | Container for ingested documents (default: `documents`) |
| `AZURE_STORAGE_CONNECTION_STRING` | Connection string used by the Substack scraper |
| `RAW_DOCUMENT_CONTAINER` | Container for scraped Substack JSON files (default: `container`) |

### Groq (LLM — query parsing + synthesis)

| Variable | Description |
|---|---|
| `GROQ_API_KEY_1` | Primary Groq API key |
| `GROQ_API_KEY_2` | Second key (round-robin to avoid rate limits) |
| `GROQ_API_KEY_3` | Third key (round-robin to avoid rate limits) |
| `GROQ_MODEL` | Model name (default: `llama-3.3-70b-versatile`) |

### Tavily (web search fallback)

| Variable | Description |
|---|---|
| `TAVILY_API_KEY` | Tavily Search API key |

### Tuning

| Variable | Default | Description |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | `1.0` | Minimum Azure semantic reranker score before web fallback |
| `TOP_K_RESULTS` | `5` | Number of chunks returned per search |

---

## Usage

### Asking questions

Open the app at `http://localhost:5173`. Type any natural-language question in the input box and press **Enter**.

Content IQ will:
1. Parse your query to extract intent, customer name, and any filters.
2. Search the internal document index (hybrid vector + keyword).
3. Evaluate confidence — if results are weak, ask for web search consent.
4. Generate a grounded answer and display citation cards below the response.

### Web search

- **Toggle** (top right): enable **Web Search** to always augment internal results with Tavily web results.
- **One-time consent**: if internal confidence is low and the toggle is off, a consent card appears — click **Search the web** to approve a single web search for that query.

### Citation cards

Each citation card shows:
- Icon: 📄 text · 📊 chart · 📋 table · 🌐 web
- Document title and page/slide number
- Source badge: **INTERNAL** (blue) or **WEB** (orange)
- Click the card to open the source URL directly (PDFs open at the cited page).

### Follow-up questions

After asking about a customer or topic, short follow-ups like *"Who wrote that?"* or *"What were the deliverables?"* automatically reuse the previous retrieval — no repeat search.

### API (direct)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What have we presented to Shell recently?",
    "conversation_id": "abc-123",
    "web_search_enabled": false,
    "web_only": false
  }'
```

**Response schema:**

```json
{
  "answer": "...",
  "citations": [
    {
      "document_title": "Shell Cloud Migration Proposal",
      "page_number": 4,
      "slide_number": null,
      "source_url": "https://..../shell_proposal.pdf#page=4",
      "content_type": "text",
      "source_label": "INTERNAL",
      "extracted_caption": null
    }
  ],
  "source_label": "INTERNAL",
  "conversation_id": "abc-123",
  "needs_web_permission": false
}
```

---

## Demo Test Prompts

| # | Prompt | Expected behaviour |
|---|---|---|
| 1 | "What have we presented to Shell recently?" | `[INTERNAL]` Shell docs, multiple citations |
| 2 | "What does the revenue chart in the Shell proposal show?" | Chart chunk from CU, exact slide cited |
| 3 | "Who authored the Shell cloud migration proposal?" | Author metadata from chunk |
| 4 | "What are the key deliverables from the Shell engagement?" | Summarised answer with citations |
| 5 | "What does industry research say about digital transformation in energy?" | `[WEB]` Tavily results |
| 6 | *(after prompt 1)* "Who wrote that proposal?" | Session reuse — no new retrieval |

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM (query parse + synthesis) | Groq `llama-3.3-70b-versatile` (3-key round-robin) |
| Embeddings | Azure OpenAI `text-embedding-ada-002` (1536-dim) |
| Vector store | Azure AI Search (HNSW + BinaryQuantization, S1+) |
| Document extraction | Azure Content Understanding (prebuilt-layout) |
| Blob storage | Azure Blob Storage |
| Web fallback | Tavily Search API |
| Substack scraper | Azure Functions (Python v2, timer trigger), rss2json proxy, BeautifulSoup |
| Backend | Python 3.11, FastAPI, uvicorn |
| Frontend | React 19, TypeScript, Vite, Fluent UI v9 |

---

## Team

| Member | Contribution |
|---|---|
| **Raghav** | Orchestrator, synthesiser, session, sample data, architecture |
| **Sania** | CU ingestion, AI Search index, chat UI |
| **Yash** | Azure Foundry/Blob setup, embedder, uploader, confidence evaluator, web search, FastAPI |
| Mentors | Arvind, Srikantan |

---

## Future Phases

- **v2** — SharePoint ingestion via MS Graph, per-user RBAC via Azure AD (`allowed_groups` field already scaffolded in index schema)
- **v3** — Content IQ becomes a sub-agent inside a larger TAB Agent framework
