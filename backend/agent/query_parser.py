"""
ContentIQ — Agent: Query Parser
Uses Groq (llama-3.3-70b) to extract structured intent, entities, and metadata filters
from a natural-language user query.
"""

import os
import json
import logging
from typing import Any

from dotenv import load_dotenv

from agent.groq_client import chat_completion, GROQ_MODEL

load_dotenv()
logger = logging.getLogger(__name__)

# Used by the parse-failure fallback — simple string match, no LLM needed
CUSTOMER_NAME_MAP: dict[str, str] = {
    "indigo":     "indigo",
    "shell":      "shell",
    "bp":         "bp",
    "air india":  "air_india",
    "air_india":  "air_india",
    "openai":     "openai",
}


def _extract_customer_fallback(query: str) -> str | None:
    """Case-insensitive substring match against known customer names."""
    q = query.lower()
    for name, tag in CUSTOMER_NAME_MAP.items():
        if name in q:
            return tag
    return None

PARSE_SYSTEM_PROMPT = """
You are a query understanding assistant for an enterprise document search system used by a consulting firm.
Extract structured information from the user's query and return a JSON object.

Return ONLY valid JSON with these exact keys:
{
  "intent": one of ["find_documents", "get_metadata", "explain_content", "general"],
  "entities": {
    "customer": string or null,    // The consulting firm's direct CLIENT (e.g. "Shell", "IndiGo", "Air India", "OpenAI")
    "topic": string or null        // e.g. "cloud migration", "revenue chart", "AI adoption"
  },
  "time_constraint": "recent" | "oldest" | null,
  "metadata_filters": {
    "customer_tag": string or null,
    "sort": "last_modified_date desc" | "last_modified_date asc" | null,
    "content_type": "chart" | "table" | "text" | "image" | null
  }
}

CRITICAL Rules:
- Known clients and their customer_tag values:
    "IndiGo"    → "indigo"
    "Shell"     → "shell"
    "Air India" → "air_india"
    "BP"        → "bp"
    "OpenAI"    → "openai"
- Set customer_tag whenever a known client name appears ANYWHERE in the query — in any phrasing:
    "What was IndiGo's PAT?"          → customer_tag = "indigo"
    "Show me Shell's revenue chart"   → customer_tag = "shell"
    "load factor for IndiGo"          → customer_tag = "indigo"
    "Air India fleet size"            → customer_tag = "air_india"
- Also inherit customer_tag from conversation history: if the last user turn established a
  customer context (e.g. asked about IndiGo) and the current query does NOT introduce a
  different customer name, carry the same customer_tag forward.
    History: "What was IndiGo's PAT?" → Current: "What about load factor?" → customer_tag = "indigo"
- Only leave customer_tag null if the query is genuinely customer-agnostic AND there is no
  prior customer context in history:
    "What is EBITDAR?" (no history)   → customer_tag = null
    "What does the revenue chart show?" (no history) → customer_tag = null
- Do NOT set customer_tag for companies that are merely referenced as examples or case studies
  inside documents (e.g. Intercom, BBVA, Lowe's, McKinsey, Gartner) — only for known clients above.
- "recent" time_constraint → set sort to "last_modified_date desc"
- content_type filter — IMPORTANT index constraint:
    Set content_type to "chart" ONLY when the user explicitly asks about a chart, graph,
    bar chart, pie chart, visual, or figure (e.g. "show me the revenue chart").
    Set content_type to "image" ONLY when explicitly asking about an image or photo.
    NEVER set content_type to "table" — financial tables, metrics, and data are stored
    as content_type="text" in this index. Setting "table" returns zero results.
    Leave content_type null for all financial metric questions (PAT, EBITDAR, RASK,
    load factor, revenue, cost, fleet size, etc.) even though they come from tables.
- Questions about who wrote/authored something → intent is "get_metadata"
- Return null for any field you cannot confidently determine
""".strip()




def parse_query(
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Parse a user query into structured intent + filters using GPT-4o.

    Args:
        user_message: The raw user message.
        conversation_history: Optional list of past messages for context.

    Returns:
        dict with keys: intent, entities, time_constraint, metadata_filters
        Falls back to a safe default dict if GPT-4o returns invalid JSON.
    """
    # Build messages: include last 4 turns of history for context
    messages: list[dict[str, str]] = [
        {"role": "system", "content": PARSE_SYSTEM_PROMPT}
    ]
    if conversation_history:
        messages.extend(conversation_history[-4:])
    messages.append({"role": "user", "content": user_message})

    try:
        response = chat_completion(
            messages=messages,
            model=GROQ_MODEL,
            temperature=0.0,    # deterministic parsing
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)

        # Normalise — ensure all expected keys exist
        parsed.setdefault("intent", "find_documents")
        parsed.setdefault("entities", {})
        parsed.setdefault("time_constraint", None)
        parsed.setdefault("metadata_filters", {})
        parsed["entities"].setdefault("customer", None)
        parsed["entities"].setdefault("topic", None)

        logger.info(
            "QueryParser → intent=%s | customer=%s | time=%s",
            parsed["intent"],
            parsed["entities"].get("customer"),
            parsed["time_constraint"],
        )
        return parsed

    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.warning("QueryParser failed (%s), using safe defaults.", e)
        customer_tag = _extract_customer_fallback(user_message)
        return {
            "intent": "find_documents",
            "entities": {"customer": None, "topic": None},
            "time_constraint": None,
            "metadata_filters": {"customer_tag": customer_tag},
        }


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What have we presented to Shell recently?"
    result = parse_query(q)
    import json; print(json.dumps(result, indent=2))
