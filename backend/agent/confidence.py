"""
ContentIQ — Agent: Confidence Evaluator
Decides whether AI Search results are good enough to answer the user's
query, or whether we should fall back to web search.

Design note — why there is no score threshold:
  The semantic reranker score encodes query-document vocabulary similarity,
  not answer-ability. When a user says "planes" and the document says
  "aircraft", the reranker penalises the mismatch and returns a low score
  even though the chunks are perfectly relevant. Using a score threshold as
  a pre-filter causes false negatives for any query where user vocabulary
  diverges from document vocabulary — which happens constantly in enterprise
  settings ("revenue" vs "turnover", "workers" vs "headcount", etc.).

  The actual quality gate is the post-synthesis check in orchestrator.py:
  if Llama cannot ground an answer from the retrieved chunks, it says so
  explicitly and the orchestrator triggers the web fallback at that point.
  That LLM-based check is the right place to judge answer quality.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_RESULTS_REQUIRED = 2


def evaluate(
    results: list[dict[str, Any]],
    query_entities: dict[str, Any] | None = None,
) -> bool:
    """
    Evaluate whether AI Search results are sufficient to answer the user's query.

    Returns True  → sufficient, proceed with internal results
    Returns False → insufficient, fall back to web search

    Fall-back triggers:
    1. Fewer than MIN_RESULTS_REQUIRED (2) chunks returned
    2. A specific customer entity was requested but NO chunk matches that customer_tag

    Actual answer quality is judged downstream by the LLM synthesiser.

    Args:
        results:         List of result dicts from internal_search.search()
        query_entities:  Parsed entities dict from QueryParser (contains "customer" key)
    """
    if not results:
        logger.info("ConfidenceEvaluator → FAIL: No results returned.")
        return False

    # ── Trigger 1: Not enough results ────────────────────────────────────────
    if len(results) < MIN_RESULTS_REQUIRED:
        logger.info(
            "ConfidenceEvaluator → FAIL: Only %d results (need ≥ %d).",
            len(results), MIN_RESULTS_REQUIRED
        )
        return False

    # ── Trigger 2: Customer entity not found in results ───────────────────────
    if query_entities:
        # Normalize same way as internal_search: lowercase + spaces → underscores
        requested_customer = (query_entities.get("customer") or "").lower().replace(" ", "_")
        if requested_customer:
            # Check if any result belongs to the requested customer
            matching = [
                r for r in results
                if (r.get("customer_tag") or "").lower().replace(" ", "_") == requested_customer
            ]
            if not matching:
                logger.info(
                    "ConfidenceEvaluator → FAIL: No results for customer '%s' "
                    "(found: %s).",
                    requested_customer,
                    list({r.get("customer_tag", "?") for r in results})
                )
                return False

    logger.info("ConfidenceEvaluator → PASS: %d results.", len(results))
    return True
