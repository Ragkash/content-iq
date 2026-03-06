"""
ContentIQ — Agent: Orchestrator
The main routing brain. Wires together SessionStore, QueryParser,
InternalSearchTool, ConfidenceEvaluator, WebSearchTool, and Synthesiser
in the correct sequence to produce grounded, cited answers.

Routing logic:
  1. Load session for conversation_id
  2. Parse user query → structured intent + filters
  3. Check for follow-up → reuse cached chunks if same scope
  4. Fresh retrieval → run InternalSearchTool
  5. ConfidenceEvaluator → if low → Bing fallback
  6. Synthesise answer + format citations
  7. Update session (chunks, history)
  8. Return response dict
"""

import logging
from typing import Any

from agent.query_parser import parse_query
from agent import internal_search
from agent.confidence import evaluate as confidence_evaluate
from agent import web_search
from agent.synthesiser import synthesise
from agent.session import SessionStore

logger = logging.getLogger(__name__)

# Module-level session store — shared across all requests in the process
_session_store = SessionStore()


def run(
    user_message: str,
    conversation_id: str,
    auth_token: str | None = None,   # accepted but not validated in v1
) -> dict[str, Any]:
    """
    Process a user query and return a grounded answer with citations.

    Args:
        user_message:    The raw user message.
        conversation_id: UUID identifying the conversation (maintained by frontend).
        auth_token:      Ignored in v1. Placeholder for v2 Azure AD integration.

    Returns:
        {
            "answer":       str (Markdown),
            "citations":    list of citation dicts,
            "source_label": "INTERNAL" | "WEB",
        }
    """
    logger.info(
        "=== Orchestrator START | conv=%s | query='%s' ===",
        conversation_id, user_message[:80]
    )

    session = _session_store.get(conversation_id)

    # ── STEP 1: Parse query ──────────────────────────────────────────────────
    parsed = parse_query(user_message, conversation_history=session.history)
    entities = parsed.get("entities", {})
    metadata_filters = parsed.get("metadata_filters", {})
    intent = parsed.get("intent", "find_documents")

    logger.info(
        "Parsed → intent=%s | entities=%s | filters=%s",
        intent, entities, metadata_filters
    )

    # ── STEP 2: Follow-up detection ──────────────────────────────────────────
    if _session_store.is_followup(entities, conversation_id):
        logger.info("FOLLOW-UP: answering from session cache (%d chunks).", len(session.retrieved_chunks))
        chunks = session.retrieved_chunks
        source_label = session.source_label
    else:
        # ── STEP 3: Internal retrieval ───────────────────────────────────────
        # Build a clean search query from the user message + parsed entities
        search_query = _build_search_query(user_message, parsed)
        chunks = internal_search.search(
            query=search_query,
            metadata_filters=metadata_filters,
        )

        # ── STEP 4: Confidence evaluation ────────────────────────────────────
        if confidence_evaluate(chunks, query_entities=entities):
            source_label = "INTERNAL"
            logger.info("Confidence OK — using internal results.")
        else:
            logger.info("Confidence LOW — falling back to Bing web search.")
            chunks = web_search.search(user_message)
            source_label = "WEB"

        # Update session with new retrieval results
        _session_store.update_retrieval(
            conversation_id=conversation_id,
            chunks=chunks,
            entities=entities,
            source_label=source_label,
        )

    # ── STEP 5: Synthesise answer + build citations ──────────────────────────
    response = synthesise(
        user_query=user_message,
        retrieved_chunks=chunks,
        source_label=source_label,
        conversation_history=session.history,
    )

    # ── STEP 6: Persist conversation history ─────────────────────────────────
    _session_store.append_history(
        conversation_id=conversation_id,
        user_message=user_message,
        assistant_message=response["answer"],
    )

    logger.info(
        "=== Orchestrator END | source=%s | citations=%d ===",
        response["source_label"], len(response["citations"])
    )

    return response


def _build_search_query(user_message: str, parsed: dict[str, Any]) -> str:
    """
    Build a clean search query by combining the raw message with
    entity hints from the parser. Keeps it simple: if the parser
    extracted a customer, prepend it for better keyword recall.
    """
    customer = (parsed.get("entities") or {}).get("customer")
    topic = (parsed.get("entities") or {}).get("topic")

    parts = []
    if customer:
        parts.append(customer)
    if topic:
        parts.append(topic)

    # If we extracted useful entities, use them as the primary search query
    # (avoids noisy pronouns like "we", "our" that hurt keyword recall)
    if parts:
        return " ".join(parts)

    # Otherwise, use the raw user message directly
    return user_message


# ─── CLI test runner ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json
    import uuid

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    conv_id = str(uuid.uuid4())

    queries = sys.argv[1:] if len(sys.argv) > 1 else [
        "What have we presented to Shell?",
        "Who authored the Shell proposal?",      # follow-up
        "What about BP?",                        # new entity
        "What is the population of Dubai?",      # should fall back to Bing
    ]

    for q in queries:
        print(f"\n\n{'='*60}")
        print(f"QUERY: {q}")
        print(f"{'='*60}")
        result = run(q, conversation_id=conv_id)
        print(f"SOURCE: {result['source_label']}")
        print(f"ANSWER:\n{result['answer']}")
        print(f"CITATIONS ({len(result['citations'])}):")
        for c in result["citations"]:
            print(f"  [{c['source_label']}] {c['document_title']} — p{c.get('page_number', '?')}")
            print(f"    {c.get('source_url', '')[:80]}")
