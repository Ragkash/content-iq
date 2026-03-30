"""
ContentIQ — Playwright Eval Runner
===================================
Tests the full stack (frontend + backend) by driving a real browser.
Uses network interception to capture raw API JSON — no fragile DOM scraping.

Usage:
    python eval/playwright_eval.py
    python eval/playwright_eval.py --dataset D:/MICROSOFT/indigo_eval_dataset.json
    python eval/playwright_eval.py --out eval/playwright_results.json --headless
    python eval/playwright_eval.py --questions Q01 Q05 Q10   # run specific questions only

Prerequisites:
    - Backend running:  python main.py
    - Frontend running: npm run dev   (inside frontend/)
    - playwright + chromium installed: pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, Route, Request, TimeoutError as PWTimeout

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_DATASET  = r"D:\MICROSOFT\indigo_eval_dataset.json"
DEFAULT_OUT      = r"eval/playwright_results.json"
FRONTEND_URL     = "http://localhost:5173"
BACKEND_CHAT_URL = "http://localhost:8000/chat"
RESPONSE_TIMEOUT = 120_000   # ms — max wait for a single answer
DELAY_BETWEEN    = 6         # seconds — Groq rate-limit buffer
# ─────────────────────────────────────────────────────────────────────────────


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _contains(text: str, phrase: str) -> bool:
    """Case-insensitive substring check, ignoring punctuation noise."""
    text  = re.sub(r"[,₹\u20b9]", "", text.lower())
    phrase = re.sub(r"[,₹\u20b9]", "", phrase.lower()).strip()
    return phrase in text


def score_completeness(answer: str, key_facts: list[str]) -> int:
    """0 — none found | 1 — some found | 2 — all found."""
    if not key_facts:
        return 2
    found = sum(1 for kf in key_facts if _contains(answer, kf))
    if found == len(key_facts):
        return 2
    if found > 0:
        return 1
    return 0


def score_faithfulness(answer: str, citations: list[dict]) -> int:
    """
    2 — answer has citations (grounded)
    1 — no citations but answer is substantive (hedged)
    0 — empty answer or explicit 'could not find'
    """
    if not answer or "could not find" in answer.lower():
        return 0
    if citations:
        return 2
    return 1


def score_retrieval_precision(citations: list[dict], expected_source: str) -> int:
    """
    Check whether the expected source document appears in the citations.
    Matches on the base filename part (before ' — ') to stay flexible.

    2 — expected doc found in citations
    1 — partial match (any citation shares a word from the expected doc name)
    0 — no match
    """
    if not citations or not expected_source:
        return 0

    # "indigo_deck.pdf — Financial Summary slide" → "indigo_deck.pdf"
    expected_file = expected_source.split(" — ")[0].strip().lower().replace("_", " ").replace(".pdf", "").replace(".pptx", "")

    cited_titles = [
        (c.get("document_title") or "").lower().replace("_", " ")
        for c in citations
    ]

    for title in cited_titles:
        if expected_file in title or title in expected_file:
            return 2

    # Partial — any significant word overlaps
    expected_words = set(expected_file.split()) - {"the", "a", "of", "and", "in"}
    for title in cited_titles:
        if any(w in title for w in expected_words if len(w) > 3):
            return 1

    return 0


def score_question(item: dict, api_response: dict) -> dict:
    answer    = api_response.get("answer", "")
    citations = api_response.get("citations", [])
    key_facts = item.get("key_facts", [])
    source    = item.get("source", "")

    c  = score_completeness(answer, key_facts)
    f  = score_faithfulness(answer, citations)
    rp = score_retrieval_precision(citations, source)

    return {
        "retrieval_precision": rp,
        "faithfulness":        f,
        "completeness":        c,
        "total":               rp + f + c,
    }


# ── Browser helpers ───────────────────────────────────────────────────────────

async def check_servers() -> tuple[bool, bool]:
    """Quick reachability check for backend and frontend."""
    import httpx
    backend_ok  = False
    frontend_ok = False
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.get("http://localhost:8000/health")
            backend_ok = r.status_code == 200
        except Exception:
            pass
        try:
            r = await client.get(FRONTEND_URL)
            frontend_ok = r.status_code == 200
        except Exception:
            pass
    return backend_ok, frontend_ok


async def ask_question(page: Page, question: str) -> dict:
    """
    Types a question into the chat UI, intercepts the /chat API response,
    and returns the parsed JSON payload.
    """
    captured: list[dict] = []

    async def handle_route(route: Route, request: Request):
        # Pass the request through to the real backend, capture the response
        response = await route.fetch()
        try:
            body = await response.json()
            captured.append(body)
        except Exception:
            pass
        await route.fulfill(response=response)

    await page.route("**/chat", handle_route)

    # Click "New Chat" to get a fresh conversation_id each time
    new_chat_btn = page.locator("button", has_text="New Chat")
    if await new_chat_btn.count() > 0:
        await new_chat_btn.click()
        await page.wait_for_timeout(300)

    # Type the question
    textarea = page.locator("textarea")
    await textarea.click()
    await textarea.fill(question)
    await page.wait_for_timeout(100)

    # Submit with Enter
    t_start = time.perf_counter()
    await textarea.press("Enter")

    # Wait until a response arrives (captured via route interception) or timeout
    deadline = time.perf_counter() + RESPONSE_TIMEOUT / 1000
    while not captured and time.perf_counter() < deadline:
        await page.wait_for_timeout(500)

    latency = round(time.perf_counter() - t_start, 2)

    await page.unroute("**/chat", handle_route)

    if captured:
        result = captured[0]
        result["_latency_s"] = latency
        # When needs_web_permission is true the backend returns answer="" but
        # the UI shows a real message. Store the actual UI text so results.json
        # reflects what the user sees rather than an empty string.
        if result.get("needs_web_permission") and not result.get("answer"):
            result["answer"] = (
                "I couldn't find enough information in the internal documents. "
                "Would you like me to search the web for this?"
            )
            result["_needs_web_permission"] = True
        return result

    return {"answer": "", "citations": [], "source_label": "INTERNAL", "_latency_s": latency, "_timeout": True}


# ── Main eval loop ─────────────────────────────────────────────────────────────

async def run_eval(dataset_path: str, out_path: str, headless: bool, filter_ids: list[str]):
    # Load dataset
    with open(dataset_path, encoding="utf-8") as f:
        raw = json.load(f)
    # Support both a top-level list and {"questions": [...]} / {"data": [...]}
    if isinstance(raw, list):
        dataset: list[dict] = raw
    else:
        dataset = raw.get("questions") or raw.get("data") or []

    if filter_ids:
        dataset = [q for q in dataset if q.get("id") in filter_ids]

    # Server check
    backend_ok, frontend_ok = await check_servers()
    if not backend_ok:
        print("❌  Backend not reachable at http://localhost:8000")
        print("    Start it:  python main.py")
        return
    if not frontend_ok:
        print("❌  Frontend not reachable at http://localhost:5173")
        print("    Start it:  cd frontend && npm run dev")
        return

    print(f"\nContentIQ Playwright Eval — {len(dataset)} questions")
    print(f"  Frontend : {FRONTEND_URL}")
    print(f"  Backend  : http://localhost:8000")
    print(f"  Headless : {headless}")
    print(f"  Output   : {out_path}\n")

    results   = []
    tier_scores: dict[str, list[int]] = {"T1": [], "T2": [], "T3": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context()
        page    = await context.new_page()

        # Navigate once; "New Chat" resets state between questions
        await page.goto(FRONTEND_URL, wait_until="networkidle")
        print("  Browser open — starting questions…\n")

        for idx, item in enumerate(dataset, 1):
            qid      = item.get("id", f"Q{idx:02d}")
            tier     = item.get("tier", "T1")
            question = item["question"]

            print(f"  [{idx:02d}/{len(dataset)}] {qid} ({tier}) — {question[:70]}…")

            try:
                api_resp = await ask_question(page, question)
            except PWTimeout:
                api_resp = {"answer": "", "citations": [], "_latency_s": RESPONSE_TIMEOUT / 1000, "_timeout": True}

            answer    = api_resp.get("answer", "")
            citations = api_resp.get("citations", [])
            latency   = api_resp.get("_latency_s", 0)
            timed_out = api_resp.get("_timeout", False)

            scores = score_question(item, api_resp)
            tier_scores.setdefault(tier, []).append(scores["total"])

            status = "⚠️  TIMEOUT" if timed_out else f"✅  {scores['total']}/6"
            print(f"         → {status}  |  latency {latency}s  |  citations: {len(citations)}")

            results.append({
                "id":                   qid,
                "tier":                 tier,
                "difficulty":           item.get("difficulty", ""),
                "question":             question,
                "expected_answer":      item.get("expected_answer", ""),
                "key_facts":            item.get("key_facts", []),
                "source":               item.get("source", ""),
                "system_answer":        answer,
                "citations":            citations,
                "source_label":         api_resp.get("source_label", ""),
                "needs_web_permission": api_resp.get("_needs_web_permission", False),
                "scores":               scores,
                "claude_scored":        False,
                "latency_s":            latency,
                "timed_out":            timed_out,
            })

            # Rate-limit buffer (skip after last question)
            if idx < len(dataset):
                await page.wait_for_timeout(DELAY_BETWEEN * 1000)

        await browser.close()

    # Summary
    summary = {}
    for tier, scores_list in tier_scores.items():
        if scores_list:
            summary[tier] = {
                "avg":   round(sum(scores_list) / len(scores_list), 2),
                "count": len(scores_list),
            }

    all_totals  = [r["scores"]["total"] for r in results]
    overall_avg = round(sum(all_totals) / len(all_totals), 2) if all_totals else 0

    output = {
        "run_at":      datetime.now().isoformat(timespec="seconds"),
        "summary":     summary,
        "overall_avg": overall_avg,
        "results":     results,
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary table
    print("\n" + "═" * 52)
    print(f"  {'Tier':<8} {'Avg Score':>10} {'/ 6':>5}   {'Count':>5}")
    print("─" * 52)
    for tier in ("T1", "T2", "T3"):
        if tier in summary:
            s = summary[tier]
            bar = "█" * int(s["avg"] * 2)
            print(f"  {tier:<8} {s['avg']:>10.2f} {'':>5}   {s['count']:>5}  {bar}")
    print("─" * 52)
    print(f"  {'OVERALL':<8} {overall_avg:>10.2f}")
    print("═" * 52)
    print(f"\n  Results saved → {out_path}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ContentIQ Playwright Eval Runner")
    parser.add_argument("--dataset",   default=DEFAULT_DATASET, help="Path to eval dataset JSON")
    parser.add_argument("--out",       default=DEFAULT_OUT,     help="Output path for results JSON")
    parser.add_argument("--headless",  action="store_true",     help="Run browser in headless mode")
    parser.add_argument("--questions", nargs="*",               help="Run only specific question IDs (e.g. Q01 Q05)")
    args = parser.parse_args()

    asyncio.run(run_eval(
        dataset_path = args.dataset,
        out_path     = args.out,
        headless     = args.headless,
        filter_ids   = args.questions or [],
    ))


if __name__ == "__main__":
    main()
