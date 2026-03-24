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
    auth_token: str | None = None,      # accepted but not validated in v1
    web_search_enabled: bool = False,   # toggle ON — hybrid internal + web
    web_only: bool = False,             # one-time consent — web only (internal already failed)
) -> dict[str, Any]:
    """
    Process a user query and return a grounded answer with citations.

    Args:
        user_message:       The raw user message.
        conversation_id:    UUID identifying the conversation (maintained by frontend).
        auth_token:         Ignored in v1. Placeholder for v2 Azure AD integration.
        web_search_enabled: When True, skip internal search and go straight to web.
                            When False (default), try internal first; if confidence
                            is low, return needs_web_permission=True instead of
                            auto-searching — letting the user decide.

    Returns:
        {
            "answer":               str (Markdown),
            "citations":            list of citation dicts,
            "source_label":         "INTERNAL" | "WEB",
            "needs_web_permission": bool — True signals the frontend to prompt
                                    the user for web search consent.
        }
    """
    logger.info(
        "=== Orchestrator START | conv=%s | web_search=%s | query='%s' ===",
        conversation_id, web_search_enabled, user_message[:80]
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

    # ── STEP 2: Route based on web_search_enabled flag ───────────────────────
    if web_only:
        # One-time consent path: internal search already ran and failed,
        # so go straight to Tavily without wasting time on internal again.
        logger.info("One-time web consent — web-only search.")
        chunks = web_search.search(user_message)
        source_label = "WEB"
        _session_store.update_retrieval(
            conversation_id=conversation_id,
            chunks=chunks,
            entities=entities,
            source_label=source_label,
        )

    elif web_search_enabled:
        # Toggle ON path: user proactively wants web — run hybrid so internal
        # document context is also included alongside fresh web results.
        logger.info("Toggle ON — running hybrid internal + Tavily search.")
        search_query = _build_search_query(user_message, parsed)
        internal_chunks = internal_search.search(
            query=search_query,
            metadata_filters=metadata_filters,
        )
        web_chunks = web_search.search(user_message)
        chunks = web_chunks + internal_chunks
        source_label = "WEB"
        _session_store.update_retrieval(
            conversation_id=conversation_id,
            chunks=chunks,
            entities=entities,
            source_label=source_label,
        )

    else:
        # ── STEP 3: Follow-up detection ──────────────────────────────────────
        if _session_store.is_followup(entities, conversation_id, user_message=user_message):
            # Expand the vague follow-up query with the prior user question so the
            # retrieval step gets a richer, unambiguous query (e.g. "list their names"
            # → "who is the governing body of indigo? list their names").
            prior_user_q = next(
                (m["content"] for m in reversed(session.history) if m["role"] == "user"),
                "",
            )
            expanded_query = f"{prior_user_q} {user_message}".strip() if prior_user_q else user_message
            logger.info("FOLLOW-UP: expanded query = '%s'", expanded_query[:120])
            chunks = internal_search.search(
                query=expanded_query,
                metadata_filters=metadata_filters,
            )
            source_label = "INTERNAL"

        else:
            # ── STEP 4: Internal retrieval ───────────────────────────────────
            search_query = _build_search_query(user_message, parsed)
            chunks = internal_search.search(
                query=search_query,
                metadata_filters=metadata_filters,
            )

            # ── STEP 5: Confidence evaluation ────────────────────────────────
            if confidence_evaluate(chunks, query_entities=entities):
                source_label = "INTERNAL"
                logger.info("Confidence OK — using internal results.")
            else:
                # Toggle is OFF and confidence is low — ask the user for
                # one-time consent to search the web for this query.
                logger.info("Confidence LOW — requesting one-time web search permission.")
                return {
                    "answer": "",
                    "citations": [],
                    "source_label": "INTERNAL",
                    "needs_web_permission": True,
                }

        _session_store.update_retrieval(
            conversation_id=conversation_id,
            chunks=chunks,
            entities=entities,
            source_label=source_label,
        )

    # ── STEP 6: Synthesise answer + build citations ──────────────────────────
    response = synthesise(
        user_query=user_message,
        retrieved_chunks=chunks,
        source_label=source_label,
        conversation_history=session.history,
    )
    response["needs_web_permission"] = False

    # ── STEP 6b: Post-synthesis confidence check ─────────────────────────────
    # Confidence passed (enough results, scores OK) but the LLM still couldn't
    # ground an answer because the chunks were off-topic. Ask for web permission.
    if (
        source_label == "INTERNAL"
        and not web_search_enabled
        and "could not find" in response["answer"].lower()
    ):
        logger.info("Post-synthesis: LLM could not ground answer — requesting web search permission.")
        return {
            "answer": "",
            "citations": [],
            "source_label": "INTERNAL",
            "needs_web_permission": True,
        }

    # ── STEP 7: Persist conversation history ─────────────────────────────────
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
    Build the search query sent to AI Search.

    Always uses the full user message as the base (preserves semantic richness
    for vector search). Prepends the customer name as a keyword boost only when
    it isn't already present in the message text.

    Previously this replaced the full message with just "customer topic", which
    discarded critical query terms — e.g. "AI adoption and business impact" —
    that are the exact headings of relevant document sections.
    """
    customer = (parsed.get("entities") or {}).get("customer") or ""

    # Prepend customer for BM25 keyword boost if not already in the message
    if customer and customer.lower() not in user_message.lower():
        return f"{customer} {user_message}"

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
