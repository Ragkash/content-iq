"""
ContentIQ — Agent: LLM Synthesiser
Takes retrieved chunks (internal or web) and produces a grounded answer
with explicit citations. The LLM is forbidden from using its own training
knowledge — it must cite every claim from the provided passages.
"""

import os
import re
import logging
from typing import Any

from dotenv import load_dotenv
from bs4 import BeautifulSoup

from agent.groq_client import chat_completion, GROQ_MODEL

load_dotenv()
logger = logging.getLogger(__name__)

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
7. When passages contain table data (pipe-separated rows), extract the EXACT numeric values
   from the table cells — do NOT round, paraphrase, or approximate them.
   Prefer table figures over narrative summaries when both are present.
Your response must contain ONLY the answer text.
Citations will be added automatically from the retrieved chunk metadata — do not repeat them.
""".strip()

SYNTHESIS_USER_TEMPLATE = """
User Question:
{user_query}

Retrieved Passages:
{passages}
"""



def _html_to_text(content: str) -> str:
    """
    Convert HTML (including tables) to plain readable text so the LLM can
    extract exact figures from table cells rather than seeing raw HTML markup.

    Table rows are rendered as pipe-separated lines:
        Header1 | Header2 | Header3
        val1    | val2    | val3
    """
    if "<" not in content:
        return content  # fast path — no HTML tags

    soup = BeautifulSoup(content, "html.parser")

    # Convert each <table> to pipe-delimited plain text before global tag strip
    for table in soup.find_all("table"):
        rows_text = []
        for row in table.find_all("tr"):
            cells = [cell.get_text(separator=" ", strip=True) for cell in row.find_all(["th", "td"])]
            if cells:
                rows_text.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows_text) + "\n")

    # Strip remaining tags (headings, spans, etc.) and collapse whitespace
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_passages(chunks: list[dict[str, Any]]) -> str:
    """
    Format retrieved chunks into a numbered passage list for the LLM context.
    Includes document title and page for grounding transparency.
    HTML tables are converted to pipe-delimited plain text for accurate extraction.
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

        lines.append(f"[{i}] {title}{loc}\n{_html_to_text(content).strip()}")
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
    # ── Score-based filtering ─────────────────────────────────────────────────
    # Only cite chunks that are competitive with the top result.
    # Chunks that happen to mention a keyword but aren't the actual source
    # of the answer will have significantly lower reranker/search scores.
    # We use a relative threshold: drop anything below 50% of the top score.
    # Web results carry a placeholder score of 0.5 — skip filtering for them.
    if source_label == "INTERNAL" and chunks:
        def _score(c: dict) -> float:
            return c.get("@search.reranker_score") or c.get("@search.score") or 0.0

        top_score = max(_score(c) for c in chunks)
        if top_score > 0:
            cutoff = top_score * 0.5
            chunks = [c for c in chunks if _score(c) >= cutoff]

    seen_keys: set[tuple] = set()
    citations = []
    for chunk in chunks:
        base_url = chunk.get("source_url", "")
        page_num = chunk.get("page_number")
        slide_num = chunk.get("slide_number")

        # Deduplicate by (url, page_number) so different pages of the same
        # document each appear as a separate citation.
        dedup_key = (base_url, page_num)
        if base_url and dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        # Append #page=N fragment so PDF viewers open directly to the cited page.
        if page_num and base_url:
            url = f"{base_url}#page={page_num}"
        else:
            url = base_url

        citations.append({
            "document_title": chunk.get("document_title", chunk.get("title", "Unknown")),
            "page_number": page_num,
            "slide_number": slide_num,
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
    # Add last 4 history turns for multi-turn follow-up context
    if conversation_history:
        messages.extend(conversation_history[-4:])
    messages.append({"role": "user", "content": user_message})

    try:
        response = chat_completion(
            messages=messages,
            model=GROQ_MODEL,
            temperature=0.1,    # Low creativity — accuracy over style
            max_tokens=1024,
        )
        answer = response.choices[0].message.content or "I could not find this in our internal documents."
    except Exception as exc:
        logger.error("Synthesiser: LLM call failed (%s) — returning fallback.", exc)
        answer = "I could not find this in our internal documents."

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
