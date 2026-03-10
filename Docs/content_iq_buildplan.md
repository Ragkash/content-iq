# Content IQ — Phased Build Plan & Checklist
**Team:** Raghav · Sania · Yash  
**Rule: Complete phases in order. Each phase directly unblocks the next.**

---

## Phase Overview

| Phase | Name | Owner | Est. Time |
|---|---|---|---|
| [P0](#p0-environment--credentials) | Environment & Credentials | All | ~2 hrs |
| [P1](#p1-sample-data-setup) | Sample Data Setup | Raghav | ~1 hr |
| [P2](#p2-blob-storage--folder-structure) | Blob Storage & Folder Structure | Raghav | ~30 min |
| [P3](#p3-content-understanding-ingestion-pipeline) | Content Understanding Ingestion Pipeline | Sania + Yash | ~3 hrs |
| [P4](#p4-azure-ai-search-index) | Azure AI Search Index | Sania | ~2 hrs |
| [P5](#p5-agent-orchestrator) | Agent Orchestrator | Raghav | ~3 hrs |
| [P6](#p6-llm-synthesiser--citations) | LLM Synthesiser + Citations | Raghav + Yash | ~2 hrs |
| [P7](#p7-bing-web-fallback) | Bing Web Fallback | Yash | ~1 hr |
| [P8](#p8-session-memory--follow-ups) | Session Memory & Follow-Ups | Raghav | ~2 hrs |
| [P9](#p9-fastapi-backend) | FastAPI Backend | Yash | ~1.5 hrs |
| [P10](#p10-chat-ui) | Chat UI | Sania | ~3 hrs |
| [P11](#p11-end-to-end-testing--demo-prep) | End-to-End Testing + Demo Prep | All | ~2 hrs |

**Total estimate: ~23 hours across the team**

---

## Starting Point — Clone This Repo First

Before anything else, clone the reference repo:

```bash
git clone https://github.com/Azure-Samples/azure-search-openai-demo
cd azure-search-openai-demo
```

Read the README fully. Understand what each folder does. You are **adapting** this, not building from scratch. The ingestion pipeline, AI Search integration, and React UI are already built here.

Also clone for Content Understanding samples:
```bash
git clone https://github.com/Azure-Samples/azure-ai-content-understanding-python
```

---

## P0 — Environment & Credentials

> Get every Azure resource provisioned and all credentials in `.env` before writing a line of code.

**Divide this:** Raghav → Entra app registration. Sania → AI Search + Content Understanding. Yash → AI Foundry + Blob Storage.

### Tasks

- [ ] **0.1** — Create or confirm access to a shared Azure subscription. Check with Arvind if an intern subscription is provided.  
  `Owner: All`

- [ ] **0.2** — Create an **Azure AI Foundry** project at `ai.azure.com`. This is the central hub. Everything (AI Search, Content Understanding, OpenAI) connects here.  
  `Owner: Yash`

- [ ] **0.3** — Deploy **GPT-4o** inside the Foundry project. Go to Deployments → Create → select `gpt-4o`. Note the deployment name and endpoint URL.  
  `Owner: Yash`

- [ ] **0.4** — Deploy **text-embedding-ada-002** in the same Foundry project. This is used to vectorise document chunks.  
  `Owner: Yash`

- [ ] **0.5** — Create an **Azure Blob Storage** account in Azure Portal. Create a container called `documents`. Note the connection string and container name.  
  `Owner: Yash`

- [ ] **0.6** — Create an **Azure AI Search** resource (Basic tier). Note the endpoint URL and admin API key.  
  `Owner: Sania`

- [ ] **0.7** — Create an **Azure Content Understanding** resource inside AI Foundry (Foundry Tools → Content Understanding). Note endpoint and key.  
  `Owner: Sania`

- [ ] **0.8** — Get a **Bing Search API v7** key from Azure Marketplace. Search "Bing Search v7" in Azure Portal → Create. Note the key.  
  `Owner: Raghav`

- [ ] **0.9** — Set up local dev environment: Python 3.11+, Node.js 18+, VS Code, Azure CLI. Run `az login` to confirm subscription access.  
  `Owner: All`

- [ ] **0.10** — Create a `.env` file at the project root with all credentials:
  ```
  AZURE_OPENAI_ENDPOINT=
  AZURE_OPENAI_KEY=
  AZURE_OPENAI_DEPLOYMENT=gpt-4o
  AZURE_OPENAI_EMB_DEPLOYMENT=text-embedding-ada-002
  AZURE_SEARCH_ENDPOINT=
  AZURE_SEARCH_KEY=
  AZURE_SEARCH_INDEX_NAME=content-iq-index
  AZURE_CU_ENDPOINT=
  AZURE_CU_KEY=
  AZURE_STORAGE_CONNECTION_STRING=
  AZURE_STORAGE_CONTAINER=documents
  BING_API_KEY=
  ```
  Add `.env` to `.gitignore` immediately.  
  `Owner: All`

### Phase Gate — do not proceed until all of these are true
- [ ] All Azure resources exist and are visible in Azure Portal
- [ ] You can print your GPT-4o deployment name from a Python test script
- [ ] `.env` is complete and committed to `.gitignore`
- [ ] All 3 teammates have confirmed Azure subscription access

---

## P1 — Sample Data Setup

> You need real documents before writing any ingestion code. Do this in parallel with P0.

**Use GPT-4o to generate these documents.** Ask it to write a realistic consulting deliverable. Make sure some include charts and tables — you need these to test multimodal later.

### Tasks

- [ ] **1.1** — Generate 5-10 sample documents using GPT. Suggested set:
  - `shell_digital_transformation_proposal.pdf` — 3-4 pages, includes a timeline table
  - `shell_cloud_migration.pptx` — 8-10 slides, includes a bar chart showing migration phases
  - `shell_executive_summary_q4.pdf` — 2 pages, narrative + key metrics table
  - `bp_digital_brief.pdf` — 2 pages, different client for testing filter
  - `energy_sector_trends_2024.pdf` — internal general document, no specific client  
  `Owner: Raghav`

- [ ] **1.2** — Format documents to look like real consulting deliverables. Add headers, client name, date, document title. Include at least one bar/pie chart in the PPT and one data table in a PDF.  
  `Owner: Raghav`

- [ ] **1.3** — Verify you have at least: 2 PDFs with tables, 1 PPTX with a chart, 1 DOCX.  
  `Owner: Raghav`

### Phase Gate
- [ ] At least 5 documents exist locally, formatted and ready to upload
- [ ] At least 2 documents contain a table or chart (for multimodal testing)

---

## P2 — Blob Storage & Folder Structure

> Set up the folder structure and upload sample docs. The AI Search indexer will pick these up automatically.

### Tasks

- [ ] **2.1** — Create the following folder structure inside your Blob `documents` container:
  ```
  customers/Shell/
  customers/BP/
  internal/general/
  ```
  `Owner: Raghav`

- [ ] **2.2** — Upload all sample documents into the correct folders:
  - Shell documents → `customers/Shell/`
  - BP documents → `customers/BP/`
  - General documents → `internal/general/`  
  `Owner: Raghav`

- [ ] **2.3** — Write a quick Python test script to confirm you can list files in the container using the Azure Blob Storage SDK (`azure-storage-blob`). Print each file's name and full URL.  
  `Owner: Raghav`

### Phase Gate
- [ ] All documents are uploaded into the correct folder paths in Blob
- [ ] Python test script successfully lists all files with their Blob URLs
- [ ] Folder structure matches the convention: `customers/{ClientName}/filename.ext`

---

## P3 — Content Understanding Ingestion Pipeline

> Build the pipeline that converts raw documents into structured, searchable chunks with metadata.  
> **This is the foundation everything else sits on. Get it right.**

**Reference:** `github.com/Azure-Samples/azure-ai-content-understanding-python` — start with `content_extraction.ipynb`  
**Also reference:** `azure-search-openai-demo/app/backend/prepdocs.py` — this is the full ingestion script

### Tasks

- [ ] **3.1** — Read the Content Understanding Python samples repo. Run `content_extraction.ipynb`. Understand what CU returns: `markdown`, `tables`, `figures`, `pages`.  
  `Owner: Sania`

- [ ] **3.2** — Write a function `analyze_document(blob_url: str) -> dict` that:
  - Sends the document URL to Content Understanding via the Analyze API
  - Waits for the result (long-running operation — use SDK polling)
  - Returns the full result including `markdown`, `tables`, `figures`  
  `Owner: Sania`

  ```python
  # Pseudocode
  from azure.ai.contentunderstanding import ContentUnderstandingClient
  
  def analyze_document(blob_url: str) -> dict:
      client = ContentUnderstandingClient(endpoint, credential)
      poller = client.begin_analyze(url=blob_url)
      result = poller.result()
      return result
  ```

- [ ] **3.3** — Test `analyze_document()` on a plain PDF. Confirm you get back:
  - Structured markdown text with headings preserved
  - Tables as markdown table format  
  `Owner: Sania`

- [ ] **3.4** — Test `analyze_document()` on the PPTX with a chart. Check whether figures/charts come back with descriptions. Note the exact output format — this determines how good your chart Q&A will be.  
  `Owner: Sania`

- [ ] **3.5** — Write a function `chunk_document(result: dict, blob_url: str, file_path: str) -> list[dict]` that:
  - Splits the markdown content into chunks of ~500 tokens with 50-token overlap (use `tiktoken`)
  - Extracts `customer_tag` from the `file_path` (e.g. `customers/Shell/file.pdf` → `"Shell"`)
  - Attaches metadata to every chunk: `document_title`, `source_url`, `page_number`, `content_type`, `customer_tag`, `last_modified_date`, `chunk_index`
  - For figures/charts: creates a separate chunk with the extracted caption as `content`, `content_type = "chart"`  
  `Owner: Yash`

- [ ] **3.6** — Test the full pipeline: `analyze_document()` → `chunk_document()` on 2 documents. Print the first 3 chunks of each. Verify metadata is correct on every chunk.  
  `Owner: Sania + Yash`

### Phase Gate
- [ ] `analyze_document()` works on a plain PDF and returns structured markdown
- [ ] `analyze_document()` works on PPTX with chart — chart description is in the output
- [ ] `chunk_document()` produces chunks with all metadata fields populated
- [ ] `customer_tag` is correctly extracted from the Blob folder path
- [ ] Full pipeline runs without errors on at least 2 documents

---

## P4 — Azure AI Search Index

> Define the index schema, create it, embed all chunks, and push everything to the index.  
> At the end of this phase you should be able to search your documents from a Python script.

**Reference:** `azure-search-openai-demo/app/backend/` — look at how they define the index schema and upload documents

### Tasks

- [ ] **4.1** — Define the index schema as a Python dict or JSON file. Include all fields from the metadata schema in the PRD. Mark which fields are:
  - `searchable: true` — `content`, `document_title`, `extracted_caption`
  - `filterable: true` — `customer_tag`, `content_type`, `author`
  - `sortable: true` — `last_modified_date`, `created_date`
  - `retrievable: true` — all fields  
  `Owner: Sania`

- [ ] **4.2** — Create the index in Azure AI Search using the Python SDK (`azure-search-documents`). Confirm it appears in the Azure Portal.  
  `Owner: Sania`

- [ ] **4.3** — Write an embedding function `embed_text(text: str) -> list[float]` that calls `text-embedding-ada-002` and returns a 1536-dim vector.  
  `Owner: Yash`

- [ ] **4.4** — Write a function `upload_chunks(chunks: list[dict])` that:
  - Takes the output of `chunk_document()`
  - Calls `embed_text()` on each chunk's content
  - Adds the vector to the chunk as `content_vector`
  - Batch-uploads to the Azure AI Search index  
  `Owner: Yash`

- [ ] **4.5** — Run the full pipeline on all sample documents: Blob → CU → chunk → embed → upload. Check the Azure Portal that your index now has records. Spot-check 2-3 records — verify metadata fields are populated correctly.  
  `Owner: All`

- [ ] **4.6** — Write and run a test hybrid search query in Python:
  ```python
  results = search_client.search(
      search_text="Shell digital transformation",
      vector_queries=[VectorizedQuery(vector=embed_text("Shell digital transformation"), fields="content_vector")],
      top=5
  )
  for r in results:
      print(r["document_title"], r["source_url"], r["@search.score"])
  ```
  Confirm you get relevant results.  
  `Owner: Sania`

- [ ] **4.7** — Test a metadata-filtered query: search for "proposal" filtered by `customer_tag eq 'Shell'` sorted by `last_modified_date desc`. Confirm only Shell documents come back in date order.  
  `Owner: Sania`

### Phase Gate
- [ ] Index exists in Azure Portal with the correct field schema
- [ ] All sample documents are indexed — correct record count visible in Portal
- [ ] Hybrid search returns relevant results with all metadata fields populated
- [ ] Filtered query (`customer_tag = Shell`, sorted by date) returns correct documents only

---

## P5 — Agent Orchestrator

> Build the brain of the system.  
> **Hardest phase. Build and test each piece independently before wiring together.**

**Reference:** `github.com/Azure/gpt-rag-orchestrator` for the full agentic RAG orchestrator pattern  
**Reference:** `github.com/microsoft/semantic-kernel` Python samples for agent + plugin setup

### Tasks

- [ ] **5.1** — Install Semantic Kernel: `pip install semantic-kernel`. Read the Agents section in the SK docs. Understand: what is a `ChatCompletionAgent`, what is a `KernelPlugin`, how does tool routing work.  
  `Owner: All`

- [ ] **5.2** — Create `agent.py`. Initialise a `ChatCompletionAgent` with your GPT-4o deployment. System prompt:
  ```
  You are an internal knowledge assistant for a consulting firm.
  You answer questions ONLY using the document passages provided to you.
  You never use your own knowledge or training data to answer questions.
  Every factual claim in your response must be supported by a retrieved passage.
  If the provided passages do not contain the answer, say:
  "I could not find this in our internal documents."
  ```
  `Owner: Raghav`

- [ ] **5.3** — Build `QueryParser`: a function that sends the user's raw prompt to GPT-4o and returns structured JSON:
  ```json
  {
    "intent": "find_documents",
    "entities": {"customer": "Shell", "topic": "cloud migration"},
    "time_constraint": "recent",
    "metadata_filters": {"customer_tag": "Shell", "sort": "last_modified_date desc"}
  }
  ```
  `Owner: Raghav`

- [ ] **5.4** — Build `InternalSearchTool` as a Semantic Kernel plugin. Method: `search(query: str, customer_filter: str = None, sort_by_date: bool = False) -> list[dict]`. Calls your Azure AI Search hybrid search with filters. Returns top-5 chunks with all metadata.  
  `Owner: Raghav`

- [ ] **5.5** — Build `ConfidenceEvaluator`: function that takes search results and returns `True` (good) or `False` (fall back). Criteria:
  - Top result `@search.score` < 0.6 → False
  - Fewer than 2 results returned → False
  - No result contains the queried customer entity → False  
  `Owner: Yash`

- [ ] **5.6** — Wire the orchestrator routing logic in sequence:
  1. `QueryParser(user_message)` → structured filters
  2. `InternalSearchTool(query, filters)`
  3. `ConfidenceEvaluator(results)`
  4. If False → `WebSearchTool(query)` (built in P7)
  5. Pass retrieved content to LLM  
  `Owner: Raghav`

- [ ] **5.7** — Test routing with 3 prompts before building anything else:
  - Prompt A: "What have we presented to Shell?" → should hit internal docs
  - Prompt B: "What is the population of Dubai?" → should fall back to Bing (no match internally)
  - Prompt C: "Recent Shell documents" → should filter by `customer_tag = Shell`, sort by date  
  `Owner: All`

### Phase Gate
- [ ] Agent receives a prompt and calls `InternalSearchTool` automatically
- [ ] `ConfidenceEvaluator` correctly identifies Prompt B as low-confidence
- [ ] Orchestrator routing runs end-to-end for all 3 test prompts
- [ ] Routing decisions are logged clearly for debugging

---

## P6 — LLM Synthesiser + Citations

> Make the agent produce actual answers with grounded citations.

### Tasks

- [ ] **6.1** — Build `synthesise(user_query: str, retrieved_chunks: list[dict]) -> str`: sends the user query + retrieved passages to GPT-4o. System prompt enforces grounded-only answers. Returns the answer text.  
  `Owner: Raghav`

- [ ] **6.2** — Build `format_citations(retrieved_chunks: list[dict]) -> list[dict]`: returns a list of citation objects:
  ```python
  [
    {
      "document_title": "Shell_Proposal_Q4.pdf",
      "page_number": 3,
      "source_url": "https://blob.../Shell_Proposal_Q4.pdf",
      "content_type": "text",
      "source_label": "INTERNAL"
    }
  ]
  ```
  `Owner: Yash`

- [ ] **6.3** — Build `build_response(user_query, retrieved_chunks, source_type) -> dict`:
  ```python
  {
    "answer": "Based on internal documents...",
    "citations": [...],
    "source_label": "INTERNAL"  # or "WEB"
  }
  ```
  `Owner: Raghav + Yash`

- [ ] **6.4** — **Hallucination test:** ask a question with no answer in your documents (e.g. about a client you haven't added). The agent MUST say "I could not find this in our internal documents" and NOT make up an answer. If it hallucinates, tighten the system prompt.  
  `Owner: All`

- [ ] **6.5** — **Citation accuracy test:** ask a specific question and manually open the cited source URL. Verify the cited document and page actually contain the answer. Repeat for 3 different prompts.  
  `Owner: All`

### Phase Gate
- [ ] LLM produces coherent grounded answers from retrieved chunks
- [ ] Hallucination test passes — agent refuses to answer without retrieved content
- [ ] All citations include `document_title`, `page_number`, and valid `source_url`
- [ ] `build_response()` returns the correct structure

---

## P7 — Bing Web Fallback

> Add the external fallback. Triggered only by the ConfidenceEvaluator.

### Tasks

- [ ] **7.1** — Build `WebSearchTool` as a Semantic Kernel plugin. Method: `search(query: str) -> list[dict]`. Calls Bing Search API v7. Returns top-3 results:
  ```python
  [
    {
      "title": "...",
      "snippet": "...",
      "url": "https://...",
      "source_label": "WEB"
    }
  ]
  ```
  `Owner: Yash`

- [ ] **7.2** — Register `WebSearchTool` in the orchestrator. Wire it into the routing logic (already stubbed in P5 step 5.6).  
  `Owner: Yash`

- [ ] **7.3** — Test: ask a question with no internal match. Confirm:
  - `ConfidenceEvaluator` returns False
  - `WebSearchTool` is called
  - Response is labelled `[WEB]`
  - Bing results appear as citations with external URLs  
  `Owner: Yash`

### Phase Gate
- [ ] `WebSearchTool` returns results correctly from Bing API
- [ ] Web fallback fires for a low-confidence prompt
- [ ] Response is clearly labelled `[WEB]` — distinct from `[INTERNAL]`

---

## P8 — Session Memory & Follow-Up Questions

> Add memory so follow-ups don't re-run full retrieval every time.

### Tasks

- [ ] **8.1** — Create a session store:
  ```python
  sessions: dict = {}
  # sessions[conversation_id] = {
  #   "retrieved_chunks": [...],
  #   "history": [...],
  #   "last_entities": {"customer": "Shell"}
  # }
  ```
  In-memory for v1. Design as a class so swapping to Redis later is trivial.  
  `Owner: Raghav`

- [ ] **8.2** — Build `is_followup(new_query: str, session: dict) -> bool`: returns True if the new query introduces no new entities and stays within the scope of `session["last_entities"]`. Use the `QueryParser` output to compare.  
  `Owner: Raghav`

- [ ] **8.3** — Update the orchestrator:
  - If `is_followup` is True → skip retrieval, answer from `session["retrieved_chunks"]`
  - If False → run full retrieval, update `session["retrieved_chunks"]` and `session["last_entities"]`
  - Always append to `session["history"]` so LLM has conversation context  
  `Owner: Raghav`

- [ ] **8.4** — Test the follow-up sequence:
  1. "What have we presented to Shell?" → full retrieval fires, stored in session
  2. "Who authored the proposal?" → answered from session, NO new AI Search call
  3. "What about BP?" → new entity, full retrieval fires for BP  
  `Owner: All`

### Phase Gate
- [ ] Q1 (initial) triggers retrieval and stores chunks in session
- [ ] Q2 (same scope follow-up) is answered without a new AI Search call — verified by logs
- [ ] Q3 (new entity) correctly triggers fresh retrieval
- [ ] Conversation history is passed to LLM so it can reference previous answers

---

## P9 — FastAPI Backend

> Expose the agent as an API endpoint for the frontend to call.

### Tasks

- [ ] **9.1** — Create `backend/main.py`. Set up FastAPI with a `POST /chat` endpoint:
  ```python
  @app.post("/chat")
  async def chat(request: ChatRequest):
      # ChatRequest: { message: str, conversation_id: str }
      # Returns: { answer: str, citations: list, source_label: str }
  ```
  `Owner: Yash`

- [ ] **9.2** — Wire `/chat` to the full agent pipeline: session check → parse → retrieve → confidence → synthesise → format.  
  `Owner: Yash`

- [ ] **9.3** — Add CORS middleware so the React frontend (running on localhost:3000) can call the backend (running on localhost:8000).  
  `Owner: Yash`

- [ ] **9.4** — Test with `curl`:
  ```bash
  curl -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "What have we presented to Shell?", "conversation_id": "test-123"}'
  ```
  Verify you get back a JSON response with `answer`, `citations`, and `source_label`.  
  `Owner: Yash`

### Phase Gate
- [ ] `POST /chat` returns `{ answer, citations, source_label }` correctly from curl
- [ ] CORS is configured — frontend can reach backend
- [ ] Consecutive requests with same `conversation_id` maintain session state

---

## P10 — Chat UI

> Build the frontend. Clean and functional. Citations must be clickable.  
> **Focus on working first, pretty second.**

**Reference:** `azure-search-openai-demo/app/frontend/` — this is a full working React + TypeScript chat UI. Adapt it.

### Tasks

- [ ] **10.1** — Bootstrap project: `npx create-next-app@latest frontend --typescript`. Install: `tailwindcss`, `react-markdown`, `axios`.  
  `Owner: Sania`

- [ ] **10.2** — Build the chat layout:
  - Input bar at bottom
  - Message history scrolling up
  - User messages: right-aligned, grey bubble
  - Agent messages: left-aligned, white card with subtle border
  - Typing indicator while waiting for response  
  `Owner: Sania`

- [ ] **10.3** — Render agent answer as Markdown using `react-markdown`. Test that bold, bullets, and headers render correctly.  
  `Owner: Sania`

- [ ] **10.4** — Build `CitationCard` component. Below each agent response, render a "Sources" section. Each card shows:
  - Document icon + document title
  - Page or slide reference (e.g. "Page 3" or "Slide 7")
  - `[INTERNAL]` badge (blue) or `[WEB]` badge (orange)
  - Entire card is clickable → opens `source_url` in a new tab  
  `Owner: Sania`

- [ ] **10.5** — Connect frontend to backend:
  - On message send: `POST http://localhost:8000/chat` with `{ message, conversation_id }`
  - `conversation_id` is a UUID stored in React state — generated once on page load
  - Display returned `answer` + `citations`  
  `Owner: Sania`

- [ ] **10.6** — Style `[INTERNAL]` and `[WEB]` badges so they are visually distinct:
  - `[INTERNAL]` → blue background, white text
  - `[WEB]` → orange background, white text
  - Must be immediately obvious which source type an answer came from  
  `Owner: Sania`

### Phase Gate
- [ ] Chat input and message history render correctly
- [ ] Agent answer renders as formatted Markdown
- [ ] Citations appear below each answer with document name, page ref, and source label
- [ ] Clicking a citation opens the correct Blob URL in a new tab
- [ ] `[INTERNAL]` is blue, `[WEB]` is orange — visually distinct
- [ ] Frontend successfully calls the FastAPI backend and displays responses

---

## P11 — End-to-End Testing + Demo Prep

> Run all 6 required prompts. Fix gaps. Prepare the architecture diagram. Rehearse.

### The 6 Required Test Prompts

Run these in order. All 6 must work.

| # | Prompt | Expected behaviour |
|---|---|---|
| 1 | "What have we presented to Shell recently?" | Internal retrieval. Citations to Shell documents. Source labelled `[INTERNAL]`. |
| 2 | "What does the revenue chart in the Shell proposal show?" | Multimodal — CU's extracted chart description retrieved. LLM interprets it. Citation to specific page. |
| 3 | "Who authored the Shell cloud migration proposal?" | Answered from `author` metadata. Citation with page ref. |
| 4 | "What are the key deliverables from the Shell engagement?" | Retrieves relevant sections. Summarises deliverables. Citations. |
| 5 | "What does industry research say about digital transformation in energy?" | No strong internal match → falls back to Bing. Labelled `[WEB]`. |
| 6 | [After prompt 1] "Who wrote that proposal?" | Follow-up. Answered from session memory. No new retrieval call to AI Search. |

### Tasks

- [ ] **11.1** — Run all 6 prompts. Log: what the parser extracted, what search returned, confidence score, what the LLM received. Fix at the layer that fails.  
  `Owner: All`

- [ ] **11.2** — Verify every citation is accurate: click each source URL. Confirm it opens the right document. Confirm the page/slide number is correct.  
  `Owner: All`

- [ ] **11.3** — Verify prompt 2 (chart question) returns a real interpreted answer, not "I don't know". If it fails, check CU output for that document — the chart description may be missing or too vague.  
  `Owner: Sania`

- [ ] **11.4** — Verify prompt 5 fires the Bing fallback: check logs to confirm `ConfidenceEvaluator` returned False and `WebSearchTool` was called.  
  `Owner: Yash`

- [ ] **11.5** — Verify prompt 6 is answered from session: check logs to confirm no new AI Search call was made.  
  `Owner: Raghav`

- [ ] **11.6** — Build the architecture diagram in draw.io, Miro, or PowerPoint. Every component on it. Every arrow labelled. Export as PNG. Practice explaining each component in 1 sentence.  
  `Owner: Raghav`

- [ ] **11.7** — Write a 1-page summary: what problem it solves, what components were used and why, what works, what doesn't, what's next. Hand this to Arvind and Srikantan at the demo.  
  `Owner: All`

- [ ] **11.8** — Demo rehearsal. One person runs the prompts, one narrates the architecture, one handles questions. Time it — under 10 minutes. Run it twice.  
  `Owner: All`

### Final Demo Checklist
- [ ] All 6 prompts return correct, grounded answers
- [ ] All citations are accurate and clickable
- [ ] Prompt 2 (chart) works with real chart interpretation
- [ ] Prompt 5 fires Bing fallback correctly, labelled `[WEB]`
- [ ] Prompt 6 (follow-up) answered from session memory
- [ ] Architecture diagram complete, every component labelled
- [ ] Demo rehearsal done at least once

---

## Quick Reference — Who Does What

| Person | Owns |
|---|---|
| **Raghav** | Sample data (P1) · Blob folder setup (P2) · Entra app reg (P0.8 moved to Bing) · Agent Orchestrator (P5) · LLM Synthesiser (P6) · Session Memory (P8) · Architecture diagram (P11.6) |
| **Sania** | AI Search + CU setup (P0.6, P0.7) · CU ingestion pipeline (P3) · AI Search index schema + queries (P4) · Chat UI (P10) · Chart prompt verification (P11.3) |
| **Yash** | AI Foundry + Blob storage setup (P0.2–0.5) · Embedding function + uploader (P4.3, P4.4) · ConfidenceEvaluator (P5.5) · Citation formatter (P6.2) · WebSearchTool + Bing (P7) · FastAPI backend (P9) |

---

## Debugging Cheatsheet

| Symptom | Where to look |
|---|---|
| Agent makes up answers | System prompt too weak. Add "If passages don't contain the answer, say you couldn't find it." |
| Wrong customer's documents returned | `customer_tag` filter not applied. Check `QueryParser` output and `InternalSearchTool` filter logic. |
| Chart question returns "I don't know" | CU didn't extract chart data. Check raw CU output for that file. Try `prebuilt-layout` analyser. |
| Follow-up triggers new retrieval | `is_followup()` returning False incorrectly. Log `QueryParser` output for both queries and compare entities. |
| Bing not firing | `ConfidenceEvaluator` threshold too low. Lower it temporarily to test, then tune. |
| Citations have wrong page number | `page_number` metadata not populated correctly during chunking. Check `chunk_document()` logic. |
| Frontend can't reach backend | CORS not configured. Check FastAPI middleware. Check ports (frontend: 3000, backend: 8000). |
