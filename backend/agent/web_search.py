"""
ContentIQ — Agent: Bing Web Search Tool
Calls Bing Search API v7 as a fallback when internal AI Search confidence is low.
Results are always tagged with source_label="WEB" so they're never mixed silently.
"""

import os
import logging
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BING_API_KEY = os.getenv("BING_API_KEY", "")
BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
TOP_WEB_RESULTS = 3


def search(query: str) -> list[dict[str, Any]]:
    """
    Search Bing and return the top-3 results formatted as ContentIQ citation-compatible dicts.

    Args:
        query: The user's search query.

    Returns:
        List of dicts with keys: title, snippet, source_url, source_label="WEB"
        Returns an empty list (with a log warning) if BING_API_KEY is not set.
    """
    if not BING_API_KEY:
        logger.warning(
            "BING_API_KEY not set — WebSearchTool returning empty results. "
            "Set BING_API_KEY in .env to enable Bing fallback."
        )
        return []

    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params = {
        "q": query,
        "count": TOP_WEB_RESULTS,
        "mkt": "en-US",
        "safeSearch": "Moderate",
        "textDecorations": False,
        "textFormat": "Raw",
    }

    logger.info("WebSearchTool → Bing search: '%s'", query[:80])

    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(BING_ENDPOINT, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        web_pages = data.get("webPages", {}).get("value", [])
        results = []
        for page in web_pages[:TOP_WEB_RESULTS]:
            results.append({
                "title": page.get("name", ""),
                "snippet": page.get("snippet", ""),
                "source_url": page.get("url", ""),
                "source_label": "WEB",
                # Populate ContentIQ citation fields for consistent response format
                "document_title": page.get("name", ""),
                "page_number": None,
                "slide_number": None,
                "content_type": "text",
                "customer_tag": None,
                "content": page.get("snippet", ""),
                "@search.score": 0.5,  # Placeholder score for web results
            })

        logger.info("WebSearchTool returned %d results from Bing.", len(results))
        return results

    except httpx.HTTPStatusError as e:
        logger.error("Bing API error: %s — %s", e.response.status_code, e.response.text[:200])
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
