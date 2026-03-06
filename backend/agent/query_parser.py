"""
ContentIQ — Agent: Query Parser
Uses GPT-4o to extract structured intent, entities, and metadata filters
from a natural-language user query.
"""

import os
import json
import logging
from typing import Any

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

PARSE_SYSTEM_PROMPT = """
You are a query understanding assistant for an enterprise document search system.
Extract structured information from the user's query and return a JSON object.

Return ONLY valid JSON with these exact keys:
{
  "intent": one of ["find_documents", "get_metadata", "explain_content", "general"],
  "entities": {
    "customer": string or null,    // e.g. "Shell", "BP"
    "topic": string or null        // e.g. "cloud migration", "revenue chart"
  },
  "time_constraint": "recent" | "oldest" | null,
  "metadata_filters": {
    "customer_tag": string or null,
    "sort": "last_modified_date desc" | "last_modified_date asc" | null,
    "content_type": "chart" | "table" | "text" | "image" | null
  }
}

Rules:
- "recent" time_constraint → set sort to "last_modified_date desc"
- If a specific customer is mentioned (Shell, BP, etc.) → set customer_tag
- Chart/revenue/graph questions → set content_type to "chart"
- Questions about who wrote something → intent is "get_metadata"
- Return null for any field you cannot confidently determine
""".strip()


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
    client = _get_client()

    # Build messages: include last 4 turns of history for context
    messages: list[dict[str, str]] = [
        {"role": "system", "content": PARSE_SYSTEM_PROMPT}
    ]
    if conversation_history:
        messages.extend(conversation_history[-4:])
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model=CHAT_DEPLOYMENT,
            messages=messages,  # type: ignore[arg-type]
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
        return {
            "intent": "find_documents",
            "entities": {"customer": None, "topic": None},
            "time_constraint": None,
            "metadata_filters": {},
        }


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What have we presented to Shell recently?"
    result = parse_query(q)
    import json; print(json.dumps(result, indent=2))
