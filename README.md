# Content IQ

**Enterprise Document Intelligence Agent** — answers natural-language questions over internal documents in Azure Blob Storage with explicit clickable citations.

Built on top of [`azure-search-openai-demo`](https://github.com/Azure-Samples/azure-search-openai-demo).

---

## Architecture

```
User → React Chat UI → FastAPI /chat → Orchestrator
                                         ├── QueryParser (GPT-4o)
                                         ├── InternalSearchTool (AI Search hybrid)
                                         ├── ConfidenceEvaluator
                                         ├── WebSearchTool (Bing fallback) [WEB]
                                         ├── Synthesiser (GPT-4o, grounded)
                                         └── SessionStore (follow-up memory)
                                         ↑
                                    Ingestion Pipeline
                                    Blob Storage → Content Understanding
                                    → Chunk → Embed (ada-002) → AI Search Index
```

---

## Quickstart

### 1. Clone and navigate
```bash
git clone <this-repo>
cd ContentIQ/contentiq
```

### 2. Configure credentials
```bash
cp .env.example .env
# Fill in all values in .env (Azure OpenAI, AI Search, CU, Blob, Bing)
```

### 3. Run the ingestion pipeline
```bash
cd backend
pip install -r requirements.txt

# Create the AI Search index schema
python -m ingestion.ingest_all --create-index

# Ingest all documents from Blob Storage
python -m ingestion.ingest_all
```

### 4. Start the backend
```bash
cd backend
uvicorn main:app --reload --port 8000
# API docs: http://localhost:8000/docs
```

### 5. Start the frontend
```bash
cd frontend
npm install
npm run dev
# App: http://localhost:5173
```

---

## Project Structure

```
contentiq/
  .env.example          ← environment variable template
  .gitignore
  backend/
    main.py             ← FastAPI: POST /chat, GET /health
    requirements.txt
    agent/
      orchestrator.py   ← main routing pipeline
      query_parser.py   ← GPT-4o intent/entity extractor
      internal_search.py← Azure AI Search hybrid (vector + BM25)
      confidence.py     ← fallback trigger logic
      web_search.py     ← Bing Search v7 fallback
      synthesiser.py    ← grounded LLM synthesis + citation builder
      session.py        ← in-memory conversation store
    ingestion/
      ingest_all.py     ← CLI: run full pipeline
      analyzer.py       ← Azure Content Understanding (REST)
      chunker.py        ← token-based chunker + metadata
      embedder.py       ← ADA-002 embeddings (batch)
      indexer.py        ← AI Search index schema creator
      uploader.py       ← batch embed + upload to index
  frontend/             ← Forked from azure-search-openai-demo
    src/
      api/
        contentiqApi.ts ← FastAPI /chat adapter
      components/
        CitationCard/   ← clickable citation card (INTERNAL blue / WEB orange)
        SourceBadge/    ← INTERNAL (blue) / WEB (orange) pill badge
      pages/chat/
        ContentIQChat.tsx     ← main chat page
        ContentIQChat.module.css
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT` | Chat model deployment name (e.g. `gpt-4o`) |
| `AZURE_OPENAI_EMB_DEPLOYMENT` | Embedding deployment name (e.g. `text-embedding-ada-002`) |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint |
| `AZURE_SEARCH_KEY` | Azure AI Search admin key |
| `AZURE_SEARCH_INDEX_NAME` | Index name (default: `content-iq-index`) |
| `AZURE_CU_ENDPOINT` | Azure Content Understanding endpoint |
| `AZURE_CU_KEY` | Azure Content Understanding key |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string |
| `AZURE_STORAGE_CONTAINER` | Container name (default: `documents`) |
| `BING_API_KEY` | Bing Search v7 API key |
| `CONFIDENCE_THRESHOLD` | Min score before Bing fallback (default: `0.6`) |

---

## Blob Folder Convention

```
documents/
  customers/Shell/     ← auto-tagged customer_tag = "Shell"
  customers/BP/        ← auto-tagged customer_tag = "BP"
  internal/general/    ← auto-tagged customer_tag = "internal"
```

Upload documents to the correct folder — `customer_tag` is extracted automatically.

---

## Test Prompts (P11 Demo)

| # | Prompt | Expected |
|---|---|---|
| 1 | "What have we presented to Shell recently?" | `[INTERNAL]` Shell docs |
| 2 | "What does the revenue chart in the Shell proposal show?" | Chart data from CU, exact slide cited |
| 3 | "Who authored the Shell cloud migration proposal?" | Author from metadata |
| 4 | "What are the key deliverables from the Shell engagement?" | Summarised with citations |
| 5 | "What does industry research say about digital transformation in energy?" | `[WEB]` Bing results |
| 6 | _(After prompt 1)_ "Who wrote that proposal?" | Session memory — no new retrieval |

---

## Reference Repos

- [`azure-search-openai-demo`](https://github.com/Azure-Samples/azure-search-openai-demo) — base repo (cloned to `azure-search-openai-demo/`, do not modify)
- [`azure-ai-content-understanding-python`](https://github.com/Azure-Samples/azure-ai-content-understanding-python) — CU samples
