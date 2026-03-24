"""
ContentIQ — Agent: Web Search Tool (Tavily)
Calls Tavily Search API as a fallback when internal AI Search confidence is low,
or when the user explicitly enables web search via the toggle.
Results are always tagged with source_label="WEB" so they're never mixed silently.
"""

import os
import logging
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_ENDPOINT = "https://api.tavily.com/search"
TOP_WEB_RESULTS = 3


def search(query: str) -> list[dict[str, Any]]:
    """
    Search the web via Tavily and return the top results formatted as
    ContentIQ citation-compatible dicts.

    Args:
        query: The user's search query.

    Returns:
        List of dicts with keys: title, snippet, source_url, source_label="WEB"
        Returns an empty list (with a log warning) if TAVILY_API_KEY is not set.
    """
    if not TAVILY_API_KEY:
        logger.warning(
            "TAVILY_API_KEY not set — WebSearchTool returning empty results. "
            "Set TAVILY_API_KEY in .env to enable web search fallback."
        )
        return []

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": TOP_WEB_RESULTS,
        "search_depth": "basic",
        "include_answer": False,
    }

    logger.info("WebSearchTool → Tavily search: '%s'", query[:80])

    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(TAVILY_ENDPOINT, json=payload)
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("results", [])[:TOP_WEB_RESULTS]:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("content", ""),
                "source_url": item.get("url", ""),
                "source_label": "WEB",
                # Populate ContentIQ citation fields for consistent response format
                "document_title": item.get("title", ""),
                "page_number": None,
                "slide_number": None,
                "content_type": "text",
                "customer_tag": None,
                "content": item.get("content", ""),
                "@search.score": item.get("score", 0.5),
            })

        logger.info("WebSearchTool returned %d results from Tavily.", len(results))
        return results

    except httpx.HTTPStatusError as e:
        logger.error("Tavily API error: %s — %s", e.response.status_code, e.response.text[:200])
        return []
    except Exception as e:
        logger.error("WebSearchTool unexpected error: %s", e)
        return []


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "digital transformation energy sector 2024"
    results = search(q)
    for r in results:
        print(f"  [WEB] {r['title']}")
        print(f"        {r['source_url']}")
        print(f"        {r['snippet'][:120]}")
