# Content IQ — Product Requirements Document
**Version:** 2.0  
**Team:** Raghav · Sania · Yash  
**Mentors:** Arvind · Srikantan  
**Target:** Working v1 by end of week

---

## 1. What Is Content IQ?

Content IQ is an **internal-first, document-grounded intelligence agent**. It lets users ask natural language questions over enterprise documents stored in Azure Blob Storage, retrieves the most relevant content, extracts precise answers, and returns responses with **explicit clickable citations** — including document name, page number, and a direct link to the source file.

If internal knowledge is insufficient (confidence below threshold), it automatically falls back to a **Bing web search**, clearly labelling the result as `[WEB]` so users always know the source.

> **This is not a general chat assistant.**  
> The LLM never answers from its own memory. Every response is grounded in retrieved content.

---

## 2. Where Content IQ Fits

There are 3 agents planned in total:

| Agent | Description | Status |
|---|---|---|
| **Content IQ** | Information retrieval over internal documents | ✅ Building now |
| Outcome Agent | Meeting summaries, client deliverables, email drafts | 🔜 Later |
| Sales Agent | Sales intelligence via MSX | 🔒 Parked (access issues) |

There is also a **TAB Agent** — the front-door orchestrator. Users will eventually talk to TAB, which routes to Content IQ based on intent. **We are not building TAB.** For v1, users interact with Content IQ directly. Design it with a clean API so TAB can call it later with zero refactoring.

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

### Goals (v1)
- Retrieve the right documents from Azure Blob Storage based on user query
- Extract precise answers from within those documents (text, tables, charts)
- Always show where the answer came from: document name + page/slide + clickable URL
- Prefer internal knowledge over external at all times
- Fall back to Bing only when internal confidence is below threshold — and label it `[WEB]`
- Support follow-up questions grounded in already-retrieved context
- Handle multimodal content: charts, tables, images using Azure Content Understanding
- Clean, functional chat web UI

### Non-Goals (v1)
- SharePoint integration (v2)
- Meeting transcription or recall
- Agenda generation (reused from TAB later)
- Sales intelligence
- Per-user access control (architect for it, don't implement)
- Real-time document sync (batch indexing on a schedule is fine)
- TAB Agent integration (v1 is standalone)

---

## 5. Data Sources

### v1 — Primary
| Source | How documents get in | Connector |
|---|---|---|
| **Azure Blob Storage** | Manual upload via Azure Portal | AI Search Blob Indexer |

### Folder Structure Convention (enforced from day 1)
```
blob-container/
  customers/
    Shell/
      shell_proposal_q4_2024.pdf
      shell_cloud_migration.pptx
    BP/
      bp_digital_brief.pdf
  internal/
    general/
      energy_sector_trends.pdf
```
The folder path is used to populate `customer_tag` automatically during indexing. No manual tagging required.

### v2 — Additional Source
| Source | Notes |
|---|---|
| **SharePoint** | Added later via MS Graph API connector. Same indexing pipeline. |

### External Fallback
- **Bing Search API v7** — triggered only when internal confidence score is below threshold
- Results are labelled `[WEB]` — never mixed silently with internal results

---

## 6. Key Design Decisions

### 6.1 Why Azure Content Understanding (not Document Intelligence alone)
Azure Content Understanding is the evolved version of Document Intelligence. It handles everything we need in one service:
- Extracts text from PDFs, DOCX, PPTX
- Extracts tables as structured rows/columns
- **Extracts charts/graphs into data descriptions** — this is critical for our hard requirement
- Captions images
- Outputs clean Markdown that feeds directly into the RAG pipeline
- It is now GA (November 2025 API version)

The LLM never sees raw images. It sees structured text extracted from them.

### 6.2 Why Azure AI Search Blob Indexer (not manual pipeline)
Azure AI Search has a built-in **Blob Indexer** that:
- Monitors the Blob container automatically
- Triggers re-indexing when new files are uploaded
- Handles chunking and embedding natively (integrated vectorisation)
- Supports metadata extraction from file paths (how we get `customer_tag` from folder structure)

This eliminates the need to write custom code to read from Blob — the indexer does it.

### 6.3 Why Hybrid Search
Azure AI Search supports hybrid retrieval — vector search + BM25 keyword search merged via RRF (Reciprocal Rank Fusion). This gives the best results: semantic meaning AND exact keyword matching. We use both.

### 6.4 Metadata — why it matters
RAG alone cannot answer:
- "Most recent Shell document" — needs `last_modified_date` sorted
- "Who wrote this?" — needs `author`
- "All Shell documents" — needs `customer_tag` filtered

Metadata is stored at indexing time and queried explicitly by the orchestrator.

---

## 7. Architecture

### 7.1 High-Level Flow

```
User (Chat UI)
    │  HTTP POST /chat  {message, conversation_id}
    ▼
FastAPI Backend
    │
    ▼
Agent Orchestrator  (Semantic Kernel / Microsoft Agent Framework)
    │
    ▼
Query Parser  (GPT-4o)
    │  Extracts: intent · entities (customer) · time constraint · metadata filters
    ▼
Session Memory Check
    │  Follow-up? → answer from cached chunks (no re-retrieval)
    │  New query? → continue to retrieval
    ▼
Internal Knowledge Router
    │
    ├── Azure Blob Storage  (raw files)
    │       │  AI Search Blob Indexer crawls automatically
    │       ▼
    │   Azure Content Understanding
    │       │  Extracts: text · tables → rows · charts → data · images → captions
    │       ▼
    │   Azure AI Search Index
    │       │  Hybrid search: vector + BM25 + RRF merge + metadata filters
    │       ▼
    │   Confidence Evaluator
    │       │
    │       ├── Score ≥ threshold → [INTERNAL] chunks passed to LLM
    │       └── Score < threshold → WebSearchTool (Bing)
    │                                   │
    │                                   └── Results tagged [WEB]
    ▼
LLM Synthesiser  (GPT-4o · Azure AI Foundry)
    │  Answers ONLY from retrieved content · attaches citation to every claim
    ▼
Response Formatter
    │  { answer, citations: [{title, page, url}], source_label }
    ▼
Chat UI  (React + Next.js)
    │  Renders: markdown answer · citation cards · clickable source links
    └── [INTERNAL] badge (blue) or [WEB] badge (orange)
```

### 7.2 Component Table

| Component | Azure Service / Tool | Purpose |
|---|---|---|
| Agent Orchestrator | Microsoft Agent Framework (Semantic Kernel) | Controls tool routing, session state, internal-first policy |
| Query Parser | GPT-4o via Azure AI Foundry | Extracts structured intent + filters from raw user prompt |
| Document Store | Azure Blob Storage | Raw file storage. Organised by folder (customer/project) |
| Ingestion + Extraction | Azure Content Understanding | Converts PDFs/PPTs/DOCX/images/charts into structured Markdown |
| Indexer | Azure AI Search Blob Indexer | Automatically crawls Blob, chunks, embeds, and indexes content |
| Search Index | Azure AI Search | Hybrid vector + keyword search with metadata filtering |
| Embedding Model | text-embedding-ada-002 via Azure OpenAI | Vectorises chunks for semantic similarity search |
| LLM | GPT-4o via Azure AI Foundry | Synthesises grounded answers from retrieved chunks |
| Web Fallback | Bing Search API v7 | External fallback only. Triggered by confidence evaluator. |
| Session Memory | In-memory dict (Redis later) | Stores retrieved chunks per conversation for follow-up handling |
| API Layer | FastAPI (Python) | Bridges UI to agent. Stateless. |
| Chat UI | React + Next.js + Tailwind | User-facing interface with citation rendering |

---

## 8. Metadata Schema

Stored at indexing time on every chunk. Required for accurate retrieval and citation generation.

| Field | Type | Purpose |
|---|---|---|
| `id` | String (UUID) | Unique key per chunk |
| `content` | String | The text content of the chunk (searchable) |
| `content_vector` | Vector (1536 dims) | Embedding for semantic search |
| `document_title` | String | Shown in citations |
| `source_url` | URL | Direct link to file in Blob — used for clickable citations |
| `page_number` | Integer | Makes citations precise ("Page 3") |
| `slide_number` | Integer | For PPTX files |
| `content_type` | Enum: text/table/chart/image | Lets agent know what it retrieved |
| `customer_tag` | String | Populated from folder path (e.g. "Shell") |
| `author` | String | Extracted from document metadata |
| `created_date` | DateTime | For temporal queries |
| `last_modified_date` | DateTime (indexed, sortable) | Enables "most recent" queries |
| `chunk_index` | Integer | Position in document for ordering |
| `extracted_caption` | String | Caption/description for charts and images |

---

## 9. Agent Design

### Tools registered in the Orchestrator

| Tool | What it does |
|---|---|
| `InternalSearchTool` | Queries Azure AI Search with hybrid search + metadata filters. Returns top-5 ranked chunks. |
| `WebSearchTool` | Calls Bing API v7. Only invoked when ConfidenceEvaluator returns False. Results tagged `[WEB]`. |
| `DocumentReaderTool` | Fetches specific document sections. Used for follow-up drill-downs. |
| `ContextMemoryTool` | Reads/writes session context. Stores chunks per conversation so follow-ups skip re-retrieval. |

### Orchestrator routing logic (the orchestrator controls this, not the LLM)
1. Parse query → extract intent, entities, filters
2. Check session → is this a follow-up in the same scope?
3. If yes → answer from session memory, skip steps 4-6
4. Call `InternalSearchTool` with parsed filters
5. Run `ConfidenceEvaluator` on results
6. If low confidence → call `WebSearchTool`
7. Pass all retrieved content to LLM Synthesiser
8. Format response with citations
9. Update session memory

---

## 10. Retrieval and Confidence Logic

### How Azure AI Search hybrid search works
- **Vector search:** query is embedded → compared against chunk vectors by cosine similarity
- **BM25 keyword search:** classic full-text matching for exact terms and names
- **RRF merge:** both result sets are merged and re-ranked. Best of both approaches.
- **Metadata filters:** applied before/after search. E.g. `customer_tag eq 'Shell'`, `sort by last_modified_date desc`

### Confidence Evaluator — fall back to Bing when ANY of these are true
- Top result relevance score is below threshold (start at 0.6, tune empirically)
- Retrieved chunks don't contain the entity the user asked about
- Metadata constraints can't be satisfied (e.g. user asked "last month", no recent docs exist)
- Fewer than 2 chunks returned above threshold

---

## 11. Answer Generation Rules

### The LLM must:
- Synthesise answers **only** from retrieved passages
- Attach a citation to **every** factual claim
- State document name, page/slide number in every citation
- Return clickable source URLs for every citation
- Label web results `[WEB]` clearly
- Say "I could not find this in internal documents" when nothing relevant is retrieved

### The LLM must not:
- Answer from its own training knowledge
- Make any claim without a retrieved source
- Mix web results silently with internal data
- Hallucinate document names or page numbers

### Response structure
```
[Summary answer — 2-4 sentences]

Sources  [INTERNAL]
📄  Shell_Proposal_Q4.pdf — Page 3    → clickable link
📄  Shell_Cloud_Migration.pptx — Slide 7    → clickable link
```

---

## 12. Follow-Up Question Handling

After initial retrieval, chunks are stored in **session memory** per `conversation_id`.

| Scenario | Behaviour |
|---|---|
| "Who authored that?" | Answered from session memory. No new retrieval. |
| "Explain the chart on slide 7 more" | `DocumentReaderTool` fetches that chunk. Still session-scoped. |
| "What about our work with BP?" | New entity (BP) detected → fresh retrieval triggered. |
| "What did we discuss at the Shell meeting?" | Explicitly out of scope (meeting recall = Outcome Agent). Content IQ says so. |

---

## 13. Multimodal Handling

**Key principle:** The LLM never sees raw images. Multimodality is handled at ingestion time by Azure Content Understanding, not at generation time.

### What Content Understanding does with each file type
| File / Content Type | What CU extracts |
|---|---|
| PDF text | Clean structured Markdown preserving headings and layout |
| PDF tables | Structured rows and columns as Markdown tables |
| PDF/PPTX charts | Data descriptions — e.g. "Bar chart: Q1 revenue $2.3M, Q2 $2.8M, Q3 $3.1M" |
| PPTX slides | Slide-level text, titles, table content, chart data |
| Embedded images | Caption + visual description |
| DOCX | Full text, tables, and embedded figure captions |

When a user asks "What does the revenue chart show?", the agent retrieves the extracted chart description stored in the index and the LLM explains it — citing the exact slide/page.

---

## 14. Access Control — Architecture for Later

**Not implemented in v1. But build in a way that makes it trivial to add.**

When added, the system needs:
- Azure AD / Entra ID token passed through the API gateway
- Azure AI Search security filters (OData-style) filtering by user or group at query time
- `allowed_groups` field stored on each indexed chunk

**How to architect for it now:**
- Accept an `auth_token` parameter in the API even if you ignore it
- Store an `allowed_groups` placeholder field in the index schema (even if it's set to `["all"]` for now)
- Keep auth logic in the API gateway — not in the LLM prompt

---

## 15. Chat UI Requirements

### Layout
- Chat history: user messages right-aligned, agent responses left-aligned
- Agent response rendered as **Markdown** (bold, bullets, headers)
- **Citations section** below each response — clearly labelled "Sources"
- Each citation: document name + page/slide reference + `[INTERNAL]`/`[WEB]` badge + clickable link opening source in new tab
- `[INTERNAL]` badge = blue, `[WEB]` badge = orange — visually distinct

### Tech Stack
- **Framework:** React + Next.js
- **Styling:** Tailwind CSS
- **Markdown rendering:** `react-markdown`
- **API calls:** `fetch()` to FastAPI `/chat` endpoint
- **Session:** `conversation_id` (UUID) stored in React state, sent with every request

---

## 16. Reference Repositories

These are the repos to start from, not build from scratch:

| Repo | What it gives you | Link |
|---|---|---|
| **azure-search-openai-demo** | Full working RAG app: Blob + AI Search + Content Understanding + OpenAI + React UI. **This is your primary reference.** | `github.com/Azure-Samples/azure-search-openai-demo` |
| **azure-ai-content-understanding-python** | Python samples for CU ingestion, field extraction, RAG integration | `github.com/Azure-Samples/azure-ai-content-understanding-python` |
| **data-extraction-using-azure-content-understanding** | Document processing + NL querying with citations using CU + OpenAI — very close to what we're building | `github.com/Azure-Samples/data-extraction-using-azure-content-understanding` |
| **gpt-rag-orchestrator** | Agentic RAG orchestration layer on Azure AI Foundry Agent Service + Semantic Kernel | `github.com/Azure/gpt-rag-orchestrator` |
| **azure-ai-agents-labs** | Hands-on labs: RAG agent with AI Search, multi-agent system with SK | `github.com/Azure/azure-ai-agents-labs` |
| **semantic-kernel** | Agent Framework, plugins/tools, Azure AI Search integration | `github.com/microsoft/semantic-kernel` |

**Start with `azure-search-openai-demo`.** It already uses Blob Storage + AI Search + Content Understanding + OpenAI + Python backend + React frontend. Your job is to adapt it, not rebuild it.

---

## 17. Deliverables for v1 (Friday)

- [ ] Working Content IQ agent answering 5-6 prompts with grounded responses
- [ ] Every response includes summary answer + citations (doc name, page/slide, clickable URL)
- [ ] At least 1 multimodal prompt works (question about a chart or table)
- [ ] Bing fallback fires correctly for a prompt with no internal match — labelled `[WEB]`
- [ ] Follow-up questions answered from session memory (no full re-retrieval)
- [ ] Clean chat UI with citation cards and source badges
- [ ] Architecture diagram showing every component
- [ ] You can explain every component in 1 sentence

---

## 18. Future Phases (post-Friday)

| Phase | What it adds |
|---|---|
| v2 | SharePoint as a second data source via MS Graph API |
| v2 | Per-user access control via Azure AD / Entra ID |
| v3 | Integration with TAB Agent (Content IQ becomes a sub-agent) |
| v3 | Outcome Agent built separately, then connected |
| Later | Sales Agent (pending MSX access) |
