"""
ContentIQ — Automated Evaluation Runner (Option A: String-Match Scoring)
=========================================================================
Fires every question at the running /chat endpoint, scores with deterministic
string matching, and saves full results to JSON (including raw system answers)
so T3 questions can be re-scored later with Claude via judge_t3.py.

Usage (backend must be running on :8000):
    cd backend
    python eval/eval_runner.py
    python eval/eval_runner.py --out eval/results.json
    python eval/eval_runner.py --start Q21 --end Q30
    python eval/eval_runner.py --backend http://localhost:8000
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv()
console = Console()

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_DATASET = r"D:\MICROSOFT\indigo_eval_dataset.json"
DEFAULT_BACKEND  = "http://localhost:8000"
DEFAULT_OUT      = "eval/results.json"
REQUEST_TIMEOUT  = 120   # T3 analytical questions need up to 2 min for multi-chunk synthesis
DELAY_BETWEEN    = 6.0   # seconds between calls — avoids Groq/LLM rate limits (free tier ~30 RPM)


# ── Scoring helpers ───────────────────────────────────────────────────────────

def normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for loose matching."""
    text = text.lower()
    text = re.sub(r"[,\.\(\)\[\]\%\+\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fact_present(fact: str, answer: str) -> bool:
    """
    Check if a key fact appears in the answer.
    Handles numeric facts (e.g. '21,763' → '21763') and short phrases.
    """
    norm_answer = normalise(answer)
    norm_fact   = normalise(fact)

    # Direct substring match
    if norm_fact in norm_answer:
        return True

    # Strip commas from numbers and retry  (e.g. "21,763" → "21763")
    stripped_fact   = norm_fact.replace(",", "").replace(" ", "")
    stripped_answer = norm_answer.replace(",", "").replace(" ", "")
    if stripped_fact in stripped_answer:
        return True

    # For multi-word facts, check if all significant tokens are present
    tokens = [t for t in norm_fact.split() if len(t) > 2]
    if tokens and all(t in norm_answer for t in tokens):
        return True

    return False


def score_completeness(key_facts: list[str], answer: str) -> int:
    """0-2: how many key facts appear in the answer."""
    if not key_facts:
        return 1
    found = sum(1 for f in key_facts if fact_present(f, answer))
    ratio = found / len(key_facts)
    if ratio >= 0.85:
        return 2
    elif ratio >= 0.4:
        return 1
    return 0


def score_faithfulness(chat_response: dict) -> int:
    """
    0-2 heuristic based on citations presence.
    The system is grounded-only — if it produced citations it retrieved real chunks.
    """
    answer    = chat_response.get("answer", "")
    citations = chat_response.get("citations", [])
    source    = chat_response.get("source_label", "")

    # Explicit failure / "I don't know" patterns
    no_info_phrases = [
        "i don't have", "i do not have", "no information",
        "not found", "cannot find", "unable to find",
        "no relevant", "not available in",
    ]
    if any(p in answer.lower() for p in no_info_phrases):
        return 0

    if citations:
        return 2   # Has citations → grounded
    if source == "WEB":
        return 1   # Web fallback — partially grounded
    if len(answer) > 100:
        return 1   # Has an answer but no citations — partial credit
    return 0


def score_retrieval_precision(q: dict, chat_response: dict) -> int:
    """
    0-2: did the citation doc titles match the expected source document?
    Uses the 'source' field in the question as ground truth.

    Normalises underscores ↔ spaces so "indigo_deck.pdf" matches
    "indigo deck.pdf" (the index stores titles with spaces, the eval
    dataset uses underscores).
    """
    citations = chat_response.get("citations", [])
    expected  = q.get("source", "").lower()

    if not citations:
        return 0

    # Normalise citation titles: lowercase, underscores → spaces
    cited_titles = " ".join(
        (c.get("document_title") or "").lower().replace("_", " ")
        for c in citations
    )

    # Extract filenames from the expected source field, then normalise
    # e.g. "indigo_deck.pdf — Financial Summary" → "indigo deck"
    raw_files = re.findall(r"[\w][\w\s]*\.pdf", expected.replace("_", " "))
    expected_stems = [f.replace(".pdf", "").strip() for f in raw_files]

    if not expected_stems:
        return 1 if citations else 0

    matched = sum(1 for stem in expected_stems if stem in cited_titles)

    if matched == len(expected_stems):
        return 2
    elif matched > 0:
        return 1
    return 0


# ── HTTP helper ───────────────────────────────────────────────────────────────

def call_chat(backend: str, question: str, conv_id: str) -> tuple[dict, float]:
    payload = {
        "message": question,
        "conversation_id": conv_id,
        "web_search_enabled": False,
        "web_only": False,
    }
    t0 = time.time()
    try:
        resp = httpx.post(f"{backend}/chat", json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json(), round(time.time() - t0, 2)
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}, round(time.time() - t0, 2)
    except Exception as e:
        return {"error": str(e)}, round(time.time() - t0, 2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ContentIQ eval runner — string-match scoring")
    parser.add_argument("--dataset",  default=DEFAULT_DATASET)
    parser.add_argument("--backend",  default=DEFAULT_BACKEND)
    parser.add_argument("--out",      default=DEFAULT_OUT, help="Path to save JSON results")
    parser.add_argument("--start",    default=None, help="First question ID e.g. Q01")
    parser.add_argument("--end",      default=None, help="Last question ID (inclusive) e.g. Q10")
    args = parser.parse_args()

    # Load dataset
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        console.print(f"[bold red]Dataset not found:[/] {dataset_path}")
        sys.exit(1)

    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)

    questions = dataset["questions"]

    if args.start or args.end:
        ids = [q["id"] for q in questions]
        start_i = ids.index(args.start) if args.start in ids else 0
        end_i   = (ids.index(args.end) + 1) if args.end and args.end in ids else len(ids)
        questions = questions[start_i:end_i]

    console.print(f"\n[bold cyan]ContentIQ Eval Runner[/] — {len(questions)} questions | backend: {args.backend}")
    console.print("[dim]Scoring: string-match (deterministic). T3 answers saved for later Claude re-scoring.[/]\n")

    # Health check
    try:
        httpx.get(f"{args.backend}/health", timeout=5).raise_for_status()
        console.print("[green]Backend healthy ✓[/]\n")
    except Exception as e:
        console.print(f"[bold red]Backend not reachable:[/] {e}")
        console.print("Start it with:  [bold]uvicorn main:app --reload[/]")
        sys.exit(1)

    # Run
    results = []
    tier_scores: dict[str, list[int]] = {"T1": [], "T2": [], "T3": []}

    table = Table(
        "ID", "Tier", "RP", "FTH", "COMP", "Total", "Facts found", "Status",
        box=box.SIMPLE_HEAD,
        title="ContentIQ Results (string-match scoring)",
    )

    for q in questions:
        qid  = q["id"]
        tier = q["tier"]

        console.print(f"  [{qid}] {q['question'][:72]}...")

        chat_resp, elapsed = call_chat(args.backend, q["question"], str(uuid.uuid4()))

        if "error" in chat_resp:
            rp, fth, comp = 0, 0, 0
            system_answer = f"ERROR: {chat_resp['error']}"
            citations     = []
            facts_detail  = "request failed"
            status        = "[red]ERROR[/]"
        else:
            system_answer = chat_resp.get("answer", "")
            citations     = chat_resp.get("citations", [])

            rp   = score_retrieval_precision(q, chat_resp)
            fth  = score_faithfulness(chat_resp)
            comp = score_completeness(q["key_facts"], system_answer)

            # Human-readable facts breakdown
            found     = [f for f in q["key_facts"] if fact_present(f, system_answer)]
            not_found = [f for f in q["key_facts"] if not fact_present(f, system_answer)]
            facts_detail = f"{len(found)}/{len(q['key_facts'])}"
            if not_found:
                facts_detail += f" missing: {', '.join(not_found[:2])}"
                if len(not_found) > 2:
                    facts_detail += f" +{len(not_found)-2}"

            status = f"[dim]{elapsed}s[/]"

        total = rp + fth + comp
        tier_scores[tier].append(total)

        # Colour helpers
        def col(v): return ["[red]0[/]", "[yellow]1[/]", "[green]2[/]"][v]
        def tot_col(s):
            if s >= 5: return f"[bold green]{s}/6[/]"
            if s >= 3: return f"[yellow]{s}/6[/]"
            return f"[bold red]{s}/6[/]"

        table.add_row(qid, tier, col(rp), col(fth), col(comp), tot_col(total), facts_detail[:50], status)

        results.append({
            "id":              qid,
            "tier":            tier,
            "difficulty":      q["difficulty"],
            "question":        q["question"],
            "expected_answer": q["expected_answer"],
            "key_facts":       q["key_facts"],
            "source":          q.get("source", ""),
            "chunk_hint":      q.get("chunk_hint", ""),
            "system_answer":   system_answer,
            "citations":       citations,
            "scores": {
                "retrieval_precision": rp,
                "faithfulness":        fth,
                "completeness":        comp,
                "total":               total,
            },
            "claude_scored": False,   # flag for judge_t3.py to pick up later
            "latency_s":     elapsed,
        })

        time.sleep(DELAY_BETWEEN)

    # Print table
    console.print()
    console.print(table)

    # Tier summary
    benchmarks = dataset.get("evaluation_instructions", {}).get("expected_performance_benchmarks", {})
    console.print("\n[bold]── Tier Summary ─────────────────────────────────────[/]")
    all_scores = []
    for tier in ["T1", "T2", "T3"]:
        sl = tier_scores[tier]
        if not sl:
            continue
        avg = sum(sl) / len(sl)
        bar = "█" * round(avg) + "░" * (6 - round(avg))
        target = benchmarks.get(f"{tier}_target", "N/A")
        note = ""
        if tier == "T3":
            note = "  [dim](string-match underestimates — run judge_t3.py for accurate T3 scores)[/]"
        console.print(f"  {tier}  avg={avg:.2f}/6  [{bar}]  target: {target}{note}")
        all_scores.extend(sl)

    if all_scores:
        overall = sum(all_scores) / len(all_scores)
        console.print(f"\n  Overall avg = [bold]{overall:.2f}/6[/]  (target: {benchmarks.get('overall_target', 'N/A')})")

    # Save results
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                tier: {"avg": round(sum(v)/len(v), 2), "count": len(v)}
                for tier, v in tier_scores.items() if v
            },
            "overall_avg": round(sum(all_scores)/len(all_scores), 2) if all_scores else 0,
            "results": results,
        }, f, indent=2, ensure_ascii=False)

    console.print(f"\n[green]Results saved →[/] {out_path}")
    console.print("[dim]Run  python eval/judge_t3.py  once you have your ANTHROPIC_API_KEY to re-score T3.[/]\n")


if __name__ == "__main__":
    main()
