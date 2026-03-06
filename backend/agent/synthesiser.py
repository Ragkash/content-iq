"""
ContentIQ — Agent: LLM Synthesiser
Takes retrieved chunks (internal or web) and produces a grounded answer
with explicit citations. The LLM is forbidden from using its own training
knowledge — it must cite every claim from the provided passages.
"""

import os
import logging
from typing import Any

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

SYNTHESIS_SYSTEM_PROMPT = """
You are an internal knowledge assistant for a consulting firm.

CRITICAL RULES — you MUST follow all of these:
1. Answer ONLY using the document passages provided below. Never use your training knowledge.
2. Every factual claim in your response must be directly supported by a retrieved passage.
3. If the provided passages do not clearly contain the answer, say:
   "I could not find this in our internal documents."
4. Do NOT hallucinate document names, page numbers, or facts not in the passages.
5. Format your answer as Markdown (use bold, bullets, and headers where helpful).
6. Keep your answer concise — 2-5 sentences or a short list. Do not over-explain.

Your response must contain ONLY the answer text.
Citations will be added automatically from the retrieved chunk metadata — do not repeat them.
""".strip()

SYNTHESIS_USER_TEMPLATE = """
User Question:
{user_query}

Retrieved Passages:
{passages}
"""

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


def _format_passages(chunks: list[dict[str, Any]]) -> str:
    """
    Format retrieved chunks into a numbered passage list for the LLM context.
    Includes document title and page for grounding transparency.
    """
    lines = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("document_title", "Unknown")
        page = chunk.get("page_number")
        slide = chunk.get("slide_number")
        content = chunk.get("content", chunk.get("snippet", ""))

        loc = ""
        if slide:
            loc = f" | Slide {slide}"
        elif page:
            loc = f" | Page {page}"

        lines.append(f"[{i}] {title}{loc}\n{content.strip()}")
    return "\n\n".join(lines)


def format_citations(
    chunks: list[dict[str, Any]],
    source_label: str = "INTERNAL",
) -> list[dict[str, Any]]:
    """
    Build a list of citation dicts from retrieved chunks.

    Each citation contains:
        document_title, page_number, slide_number, source_url,
        content_type, source_label ("INTERNAL" or "WEB")

    Args:
        chunks:       Retrieved chunk dicts (internal or web results).
        source_label: "INTERNAL" if from AI Search, "WEB" if from Bing.
    """
    seen_urls: set[str] = set()
    citations = []
    for chunk in chunks:
        url = chunk.get("source_url", "")
        # Deduplicate by URL (same document cited multiple times)
        if url and url in seen_urls:
            continue
        seen_urls.add(url)

        citations.append({
            "document_title": chunk.get("document_title", chunk.get("title", "Unknown")),
            "page_number": chunk.get("page_number"),
            "slide_number": chunk.get("slide_number"),
            "source_url": url,
            "content_type": chunk.get("content_type", "text"),
            "source_label": chunk.get("source_label", source_label),
            "extracted_caption": chunk.get("extracted_caption", ""),
        })
    return citations


def synthesise(
    user_query: str,
    retrieved_chunks: list[dict[str, Any]],
    source_label: str = "INTERNAL",
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Generate a grounded answer and build citations from retrieved content.

    Args:
        user_query:           The user's question.
        retrieved_chunks:     Chunks from InternalSearchTool or WebSearchTool.
        source_label:         "INTERNAL" or "WEB".
        conversation_history: Optional past messages for multi-turn context.

    Returns:
        dict with keys:
            answer:       Markdown answer string (grounded only).
            citations:    List of citation dicts.
            source_label: "INTERNAL" or "WEB".
    """
    if not retrieved_chunks:
        return {
            "answer": "I could not find this in our internal documents.",
            "citations": [],
            "source_label": source_label,
        }

    passages_text = _format_passages(retrieved_chunks)
    user_message = SYNTHESIS_USER_TEMPLATE.format(
        user_query=user_query,
        passages=passages_text,
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT}
    ]
    # Add last 4 history turns for follow-up context
    if conversation_history:
        messages.extend(conversation_history[-4:])
    messages.append({"role": "user", "content": user_message})

    client = _get_client()
    response = client.chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.1,    # Low creativity — accuracy over style
        max_tokens=1024,
    )
    answer = response.choices[0].message.content or "I could not find this in our internal documents."

    citations = format_citations(retrieved_chunks, source_label)

    logger.info(
        "Synthesiser → source=%s | citations=%d | answer_len=%d chars",
        source_label, len(citations), len(answer)
    )

    return {
        "answer": answer.strip(),
        "citations": citations,
        "source_label": source_label,
    }
