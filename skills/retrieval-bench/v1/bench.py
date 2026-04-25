"""Retrieval benchmark runner — Run 5 Phase 1.

Usage:
  python bench.py --queries queries.yml --output /path/to/output.md [--top-k 5]

Calls phoenix search (in-process) against the .bench corpus in vault/.bench/.
Evaluates: expected_answer present in top-K results (string match, case-insensitive).
Outputs a markdown table with per-depth success rates.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

PHOENIX_ROOT = Path(os.getenv("PHOENIX_ROOT", "/mnt/c/Users/PhoenixGlobal"))
BENCH_CORPUS = PHOENIX_ROOT / "vault" / ".bench"
PHOENIX_SEARCH_PY = PHOENIX_ROOT / "bin" / "phoenix-search.py"


def _load_search_module():
    """Load phoenix-search.py for in-process search."""
    spec = importlib.util.spec_from_file_location("phoenix_search", PHOENIX_SEARCH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _search_bench_corpus(query: str, k: int = 5) -> list[dict]:
    """Search the .bench corpus directly (file scan, no vector)."""
    tokens = set(re.findall(r"\w+", query.lower()))
    results = []

    for md_file in BENCH_CORPUS.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
            hits = sum(1 for t in tokens if t in text.lower())
            if hits > 0:
                snippet = next(
                    (
                        line.strip()
                        for line in text.splitlines()
                        if any(t in line.lower() for t in tokens)
                    ),
                    text[:80].replace("\n", " "),
                )
                results.append(
                    {
                        "id": str(md_file.relative_to(PHOENIX_ROOT)),
                        "title": md_file.stem,
                        "snippet": snippet[:200],
                        "score": hits,
                        "text": text,
                    }
                )
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def _answer_in_results(expected: str, results: list[dict]) -> bool:
    """Check if expected_answer appears in any result's text (case-insensitive)."""
    expected_lower = expected.lower().strip()
    for r in results:
        combined = (r.get("snippet", "") + " " + r.get("text", "")).lower()
        if expected_lower in combined:
            return True
    return False


def run_benchmark(queries: list[dict], top_k: int = 5) -> dict:
    """Run all queries and return results grouped by hop depth."""
    results_by_depth: dict[int, dict] = {}

    for q in queries:
        depth = q.get("ground_truth_hops", 1)
        if depth not in results_by_depth:
            results_by_depth[depth] = {"total": 0, "success": 0, "latencies": []}

        t0 = time.time()
        search_results = _search_bench_corpus(q["query"], k=top_k)
        latency_ms = int((time.time() - t0) * 1000)

        success = _answer_in_results(q["expected_answer"], search_results)
        results_by_depth[depth]["total"] += 1
        if success:
            results_by_depth[depth]["success"] += 1
        results_by_depth[depth]["latencies"].append(latency_ms)

    # Build summary
    summary = {}
    for depth, stats in results_by_depth.items():
        total = stats["total"]
        successes = stats["success"]
        lats = stats["latencies"]
        summary[depth] = {
            "total": total,
            "success": successes,
            "success_rate": round(successes / total, 4) if total > 0 else 0.0,
            "mean_latency_ms": round(sum(lats) / len(lats)) if lats else 0,
        }

    return summary


def _write_markdown_report(summary: dict, output_path: Path, query_count: int) -> None:
    """Write a markdown table report."""
    ts = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# Retrieval Benchmark — {ts}",
        "",
        f"Corpus: `vault/.bench/` ({query_count} queries)",
        "",
        "## Results by hop depth",
        "",
        "| Depth | Queries | Success | success_rate | mean_latency_ms |",
        "|-------|---------|---------|--------------|-----------------|",
    ]

    for depth in sorted(summary.keys()):
        s = summary[depth]
        lines.append(
            f"| {depth} | {s['total']} | {s['success']} | "
            f"{s['success_rate'] * 100:.1f}% | {s['mean_latency_ms']} ms |"
        )

    # Add overall
    total_q = sum(s["total"] for s in summary.values())
    total_s = sum(s["success"] for s in summary.values())
    overall_rate = round(total_s / total_q, 4) if total_q > 0 else 0.0
    lines += [
        "",
        f"**Overall:** {total_s}/{total_q} = {overall_rate * 100:.1f}%",
        "",
        "## S5 gate check",
        "",
        f"success@1-hop: {summary.get(1, {}).get('success_rate', 0) * 100:.1f}%",
        "S5 threshold: 70%",
        f"S5 PASS: {'YES' if summary.get(1, {}).get('success_rate', 0) >= 0.7 else 'NO — STOP RUN 5'}",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")
    print(f"Report written to: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PhoenixGlobal retrieval benchmark")
    parser.add_argument("--queries", default=str(Path(__file__).parent / "queries.yml"))
    parser.add_argument("--output", default="")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    with open(args.queries) as f:
        queries = yaml.safe_load(f)

    if not queries:
        print("ERROR: No queries loaded", file=sys.stderr)
        return 1

    print(f"Running benchmark: {len(queries)} queries, top_k={args.top_k}")
    summary = run_benchmark(queries, top_k=args.top_k)

    if not args.output:
        ts_slug = datetime.now(timezone.utc).strftime("%Y%m%d")
        args.output = str(
            PHOENIX_ROOT / "dream" / "logs" / f"retrieval-bench-{ts_slug}.md"
        )

    _write_markdown_report(summary, Path(args.output), len(queries))

    # Print summary to stdout
    for depth in sorted(summary.keys()):
        s = summary[depth]
        print(
            f"  hop={depth}: {s['success']}/{s['total']} = {s['success_rate'] * 100:.1f}%"
        )

    success_1hop = summary.get(1, {}).get("success_rate", 0)
    print(f"\nsuccess@1-hop: {success_1hop * 100:.1f}%")
    if success_1hop < 0.7:
        print("ERROR: S5 HARD GATE — success@1-hop < 70%. STOP RUN 5.", file=sys.stderr)
        return 2

    print("S5 gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
