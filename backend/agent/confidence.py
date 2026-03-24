"""
ContentIQ — Agent: Confidence Evaluator
Decides whether AI Search results are good enough to answer the user's
query, or whether we should fall back to Bing web search.
"""

import os
import logging
from typing import Any

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Reranker scores range 0-4. Default threshold of 1.0 filters out poor matches
# while accepting good semantic matches (typically score 2.0+).
# The old 0.6 threshold was calibrated for BM25 @search.score (0-1 scale),
# but we now use @search.reranker_score from Azure semantic reranker (0-4 scale).
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "1.0"))
MIN_RESULTS_REQUIRED = 2


def evaluate(
    results: list[dict[str, Any]],
    query_entities: dict[str, Any] | None = None,
) -> bool:
    """
    Evaluate whether AI Search results are sufficient to answer the user's query.

    Returns True  → sufficient, proceed with internal results
    Returns False → insufficient, fall back to Bing Web Search

    Fall-back triggers (ANY of these causes False):
    1. Fewer than MIN_RESULTS_REQUIRED (2) chunks returned
    2. Top result's search score < CONFIDENCE_THRESHOLD (default 0.6)
    3. A specific customer entity was requested but NO chunk matches that customer_tag

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

    # ── Trigger 2: Top reranker score below threshold ────────────────────────
    # Use semantic reranker score (0-4 scale) when available; fall back to
    # @search.score (RRF fusion, ~0-0.05 scale) only if reranker didn't run.
    top_score = results[0].get("@search.reranker_score") or results[0].get("@search.score", 0.0)
    if top_score < CONFIDENCE_THRESHOLD:
        logger.info(
            "ConfidenceEvaluator → FAIL: Top score %.3f < threshold %.3f.",
            top_score, CONFIDENCE_THRESHOLD
        )
        return False

    # ── Trigger 3: Customer entity not found in results ───────────────────────
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

    logger.info(
        "ConfidenceEvaluator → PASS: %d results, top score %.3f.",
        len(results), top_score
    )
    return True
