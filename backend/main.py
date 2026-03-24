"""
ContentIQ — FastAPI Backend
Main API entry point. Exposes /chat and /health endpoints.
Bridges the React frontend to the ContentIQ agent.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Import orchestrator ──────────────────────────────────────────────────────
# This import validates env vars at startup — if they are missing,
# the service will log a warning but still start (endpoints will error
# gracefully when called without credentials).
from agent.orchestrator import run as orchestrator_run


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ContentIQ backend starting up...")
    yield
    logger.info("ContentIQ backend shutting down.")


app = FastAPI(
    title="ContentIQ API",
    description=(
        "Document-grounded enterprise intelligence agent. "
        "Returns answers with explicit citations from Azure Blob Storage documents."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS — allow the React dev server (localhost:3000) ──────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",     # Vite dev server port
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's question")
    conversation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID identifying the conversation. Generate on first message, reuse for follow-ups.",
    )
    auth_token: str | None = Field(
        default=None,
        description="Azure AD token (v1: accepted but not validated). Required in v2.",
    )
    web_search_enabled: bool = Field(
        default=False,
        description="Toggle ON — run hybrid internal + Tavily search.",
    )
    web_only: bool = Field(
        default=False,
        description="One-time consent — skip internal (already failed) and go straight to Tavily.",
    )


class CitationResponse(BaseModel):
    document_title: str
    page_number: int | None
    slide_number: int | None
    source_url: str
    content_type: str
    source_label: str               # "INTERNAL" or "WEB"
    extracted_caption: str | None


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    source_label: str               # "INTERNAL" or "WEB"
    conversation_id: str
    needs_web_permission: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> Any:
    """
    Process a user message and return a grounded answer with clickable citations.

    - Checks session for follow-up detection (no re-retrieval if same scope)
    - Searches internal documents via Azure AI Search (hybrid vector + BM25)
    - Falls back to Bing web search if internal confidence is below threshold
    - Returns Markdown answer + citation list (document name, page, URL, badge)
    """
    logger.info(
        "POST /chat | conv=%s | query='%s'",
        request.conversation_id, request.message[:80]
    )

    try:
        result = orchestrator_run(
            user_message=request.message,
            conversation_id=request.conversation_id,
            auth_token=request.auth_token,
            web_search_enabled=request.web_search_enabled,
            web_only=request.web_only,
        )
    except EnvironmentError as e:
        # Missing Azure credentials — give a helpful error during development
        raise HTTPException(
            status_code=503,
            detail=(
                f"Azure service not configured: {e}. "
                "Fill in your .env file and restart the server."
            ),
        )
    except Exception as e:
        logger.exception("Orchestrator error for conv=%s", request.conversation_id)
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    return ChatResponse(
        answer=result["answer"],
        citations=[
            CitationResponse(
                document_title=c.get("document_title", ""),
                page_number=c.get("page_number"),
                slide_number=c.get("slide_number"),
                source_url=c.get("source_url", ""),
                content_type=c.get("content_type", "text"),
                source_label=c.get("source_label", result["source_label"]),
                extracted_caption=c.get("extracted_caption"),
            )
            for c in result.get("citations", [])
        ],
        source_label=result["source_label"],
        conversation_id=request.conversation_id,
        needs_web_permission=result.get("needs_web_permission", False),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint. Returns 200 if the service is running."""
    return {"status": "ok", "service": "content-iq-backend"}


# ── Dev server entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
