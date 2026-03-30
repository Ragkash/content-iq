"""
ContentIQ — T3 Claude Judge
============================
Reads saved results.json from eval_runner.py and re-scores T3 questions
using Claude as an independent judge. Updates scores in-place.

Run this AFTER you have an ANTHROPIC_API_KEY:
    pip install anthropic
    # add ANTHROPIC_API_KEY=sk-ant-... to backend/.env
    cd backend
    python eval/judge_t3.py
    python eval/judge_t3.py --results eval/results.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv()
console = Console()

DEFAULT_RESULTS = "eval/results.json"

JUDGE_PROMPT = """You are an expert evaluator for a RAG (Retrieval-Augmented Generation) pipeline tested on financial documents (IndiGo Q1 FY26 earnings).

You will receive a question, the expected answer with required key facts, and the system's actual response.

Score on THREE dimensions (integer 0, 1, or 2 only):

retrieval_precision:
  2 = Response clearly draws from the correct source chunks (matching chunk_hint)
  1 = Related content retrieved but not the most precise chunk
  0 = Wrong content retrieved or retrieval clearly failed

faithfulness:
  2 = Fully grounded in retrieved content, no hallucination
  1 = Minor extrapolation but mostly grounded
  0 = Contains hallucinated facts or contradicts the source

completeness:
  2 = Captures ALL key facts listed
  1 = Captures at least half the key facts AND makes the key analytical connection
  0 = Misses most key facts or gives a surface-level answer without synthesis

For T3 questions, completeness=2 requires both the facts AND the analytical reasoning/framing.

Respond ONLY with valid JSON, no markdown, no explanation outside JSON:
{"retrieval_precision": <int>, "faithfulness": <int>, "completeness": <int>, "justification": "<one concise sentence>"}
"""


def load_anthropic():
    try:
        import anthropic
    except ImportError:
        console.print("[bold red]anthropic package not installed.[/]  Run:  pip install anthropic")
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[bold red]ANTHROPIC_API_KEY not set.[/]  Add it to backend/.env")
        sys.exit(1)

    return anthropic.Anthropic(api_key=api_key)


def judge_one(client, entry: dict) -> dict:
    import anthropic

    user_msg = f"""QUESTION: {entry['question']}

EXPECTED ANSWER: {entry['expected_answer']}

KEY FACTS REQUIRED: {json.dumps(entry['key_facts'])}

CHUNK HINT (what the retriever should have found): {entry.get('chunk_hint', 'N/A')}

SYSTEM RESPONSE:
{entry['system_answer']}"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[
                {"role": "user", "content": JUDGE_PROMPT + "\n\n" + user_msg}
            ],
        )
        raw = message.content[0].text.strip()
        scores = json.loads(raw)
        scores["total"] = scores["retrieval_precision"] + scores["faithfulness"] + scores["completeness"]
        return scores
    except json.JSONDecodeError:
        console.print(f"  [yellow]Parse error on judge response[/]")
        return {"retrieval_precision": 0, "faithfulness": 0, "completeness": 0,
                "total": 0, "justification": "Judge parse error"}
    except Exception as e:
        console.print(f"  [red]Judge error:[/] {e}")
        return {"retrieval_precision": 0, "faithfulness": 0, "completeness": 0,
                "total": 0, "justification": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Re-score T3 questions with Claude")
    parser.add_argument("--results", default=DEFAULT_RESULTS, help="Path to results.json from eval_runner.py")
    args = parser.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        console.print(f"[bold red]Results file not found:[/] {results_path}")
        console.print("Run eval_runner.py first.")
        sys.exit(1)

    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)

    t3_questions = [r for r in data["results"] if r["tier"] == "T3"]
    already_done = [r for r in t3_questions if r.get("claude_scored")]

    console.print(f"\n[bold cyan]ContentIQ T3 Claude Judge[/]")
    console.print(f"Found {len(t3_questions)} T3 questions ({len(already_done)} already Claude-scored)\n")

    to_score = [r for r in t3_questions if not r.get("claude_scored")]
    if not to_score:
        console.print("[green]All T3 questions already scored by Claude. Nothing to do.[/]")
        sys.exit(0)

    client = load_anthropic()
    console.print(f"Scoring {len(to_score)} T3 questions with Claude Haiku...\n")

    table = Table("ID", "Old score", "New score", "RP", "FTH", "COMP", "Justification",
                  box=box.SIMPLE_HEAD, title="T3 Re-scoring (Claude judge)")

    for entry in to_score:
        qid = entry["id"]
        old_total = entry["scores"]["total"]

        console.print(f"  [{qid}] scoring...", end="")
        scores = judge_one(client, entry)
        console.print(" done")

        # Update entry in-place
        entry["scores"] = scores
        entry["claude_scored"] = True

        new_total = scores["total"]

        def col(v): return ["[red]0[/]", "[yellow]1[/]", "[green]2[/]"][v]
        def tot(s):
            if s >= 5: return f"[bold green]{s}/6[/]"
            if s >= 3: return f"[yellow]{s}/6[/]"
            return f"[bold red]{s}/6[/]"

        delta = new_total - old_total
        delta_str = f"(+{delta})" if delta > 0 else f"({delta})" if delta < 0 else "(=)"
        table.add_row(
            qid,
            f"{old_total}/6",
            f"{tot(new_total)} {delta_str}",
            col(scores["retrieval_precision"]),
            col(scores["faithfulness"]),
            col(scores["completeness"]),
            scores.get("justification", "")[:60],
        )

    console.print()
    console.print(table)

    # Recalculate summary
    tier_avgs = {}
    for tier in ["T1", "T2", "T3"]:
        tier_results = [r for r in data["results"] if r["tier"] == tier]
        if tier_results:
            avg = sum(r["scores"]["total"] for r in tier_results) / len(tier_results)
            tier_avgs[tier] = round(avg, 2)
            data["summary"][tier] = {"avg": round(avg, 2), "count": len(tier_results)}

    all_totals = [r["scores"]["total"] for r in data["results"]]
    data["overall_avg"] = round(sum(all_totals) / len(all_totals), 2)

    # Save updated results
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    console.print(f"\n[bold]── Updated Summary ──────────────────────────────────[/]")
    for tier, avg in tier_avgs.items():
        bar = "█" * round(avg) + "░" * (6 - round(avg))
        note = " [dim](Claude-scored)[/]" if tier == "T3" else ""
        console.print(f"  {tier}  avg={avg:.2f}/6  [{bar}]{note}")
    console.print(f"\n  Overall avg = [bold]{data['overall_avg']:.2f}/6[/]")

    console.print(f"\n[green]Updated results saved →[/] {results_path}\n")


if __name__ == "__main__":
    main()
