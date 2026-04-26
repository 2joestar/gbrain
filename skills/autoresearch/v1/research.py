"""
C1 — phoenix research multi-round bounded research loop.
Run 6 Phase 2.

Feature flag: autoresearch.enabled in protocols/feature-flags.yml.
Budget defaults: 3 rounds / $0.50 USD / 30K tokens.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable

import yaml

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
FLAGS_FILE = PHOENIX_ROOT / "protocols" / "feature-flags.yml"
RESEARCH_DIR = PHOENIX_ROOT / "vault" / "research"

# Narrow fetch allowlist (extend per-call via --allow)
DEFAULT_ALLOWLIST = [
    "arxiv.org",
    "wikipedia.org",
    "github.com",
    "docs.python.org",
]

# Convergence: if new facts in a round < this fraction of previous round, stop early.
CONVERGENCE_RATIO = 0.2


def _load_flag() -> bool:
    try:
        data = yaml.safe_load(FLAGS_FILE.read_text())
        return bool(data.get("autoresearch", {}).get("enabled", False))
    except Exception:
        return False


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:40].strip("-")


def _compute_confidence(sources: list[dict], contradiction_count: int) -> float:
    """mean(source_reputation) * (1 - contradiction_fraction)."""
    if not sources:
        return 0.5
    rep_scores = {"pinned": 0.9, "dream-inferred": 0.5, "web": 0.6, "search": 0.7}
    reps = [rep_scores.get(s.get("reputation", "search"), 0.5) for s in sources]
    mean_rep = sum(reps) / len(reps)
    total = len(sources) + contradiction_count
    c_frac = contradiction_count / total if total > 0 else 0
    return round(mean_rep * (1 - c_frac), 4)


def _generate_queries(original: str, findings: list[dict], round_n: int) -> list[str]:
    """Generate search queries for this round based on findings so far."""
    queries = [original]
    if findings and round_n > 0:
        # Add entity-specific follow-up queries from prior findings
        subjects = list({f.get("subject", "") for f in findings if f.get("subject")})[
            :2
        ]
        for subj in subjects:
            queries.append(f"{subj} {original}")
    return queries[:3]


def _local_search(query: str, k: int = 5) -> list[dict]:
    """Run phoenix search in-process and return result dicts."""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "phoenix_search",
            PHOENIX_ROOT / "bin" / "phoenix-search.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        results = []
        for surface_fn in (mod.search_vault, mod.search_semantic):
            results.extend(surface_fn(query, k))
        return results[:k]
    except Exception:
        return []


def _fetch_web(query: str, allowlist: list[str]) -> list[dict]:
    """Stub for web fetch — returns empty in offline mode."""
    # Real implementation would use urllib/requests against allowlist.
    # Offline mode: no-op to keep CI deterministic.
    return []


def _extract_facts(results: list[dict]) -> list[dict]:
    """Extract candidate facts from search results using entity extractor."""
    facts = []
    for r in results:
        snippet = r.get("snippet", r.get("text", ""))
        if snippet:
            facts.append(
                {
                    "text": snippet,
                    "source_id": r.get("id", ""),
                    "source_title": r.get("title", ""),
                    "subject": r.get("title", ""),
                    "reputation": "search",
                }
            )
    return facts


def _check_contradictions(facts: list[dict], existing: list[dict]) -> list[dict]:
    """Run contradiction detection on candidate facts. Returns list of flagged facts."""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "detector",
            PHOENIX_ROOT
            / "brain"
            / "skills"
            / "contradiction-detector"
            / "v1"
            / "detector.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not mod._load_flag():
            return []
        flagged = []
        for fact in facts:
            conflicts = mod.check(fact["text"], existing)
            if conflicts:
                fact["contradicts"] = [c["existing_id"] for c in conflicts]
                fact["conflict_reasons"] = [c["reason"] for c in conflicts]
                flagged.append(fact)
        return flagged
    except Exception:
        return []


def _write_output(
    query: str,
    findings: list[dict],
    sources: list[dict],
    contradictions: list[dict],
    trail: list[dict],
    budget_summary: dict,
    output_path: Path | None = None,
) -> Path:
    """Write research output to vault/research/<date>-<slug>.md."""
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    slug = _slug(query)
    out = output_path or (RESEARCH_DIR / f"{today}-{slug}.md")

    conf = _compute_confidence(sources, len(contradictions))

    answer_parts = []
    for f in findings[:10]:
        answer_parts.append(f"- {f.get('text', '')[:200]}")

    source_lines = []
    for s in sources[:10]:
        title = s.get("source_title", s.get("title", "?"))
        sid = s.get("source_id", "")
        snippet = s.get("text", "")[:120]
        source_lines.append(f'- [{title}]({sid}) — accessed {today}\n  > "{snippet}"')

    contradiction_lines = []
    for c in contradictions[:5]:
        contradiction_lines.append(
            f'⚠️  "{c.get("text", "")[:100]}" contradicts {c.get("contradicts", [])}'
        )

    trail_lines = []
    for t in trail:
        trail_lines.append(
            f"- round {t['round']}: queries={t['queries']}, facts={t['facts_found']}"
        )

    content = (
        f"---\n"
        f"title: {query!r}\n"
        f"date: {today!r}\n"
        f"explored: true\n"
        f"confidence: {conf}\n"
        f"source_count: {len(sources)}\n"
        f"contradictions: {len(contradictions)}\n"
        f"contradicts: []\n"
        f"contradicted_by: []\n"
        f"research_rounds: {budget_summary.get('rounds_used', 0)}\n"
        f"research_tokens: {budget_summary.get('spent_tokens', 0)}\n"
        f"research_usd: {budget_summary.get('spent_usd', 0.0)}\n"
        f"---\n\n"
        f"# {query}\n\n"
        f"## Synthesized answer\n\n"
        + ("\n".join(answer_parts) or "_No findings._")
        + "\n\n"
        "## Sources\n\n" + ("\n".join(source_lines) or "_None._") + "\n\n"
        "## Contradictions detected\n\n"
        + ("\n".join(contradiction_lines) or "_None._")
        + "\n\n"
        "## Research trail\n\n" + "\n".join(trail_lines) + "\n"
    )
    out.write_text(content, encoding="utf-8")
    return out


def run_research(
    query: str,
    *,
    budget_rounds: int = 3,
    budget_usd: float = 0.50,
    budget_tokens: int = 30000,
    allow: list[str] | None = None,
    output_path: Path | None = None,
    # Injection points for testing
    _search_fn: Callable | None = None,
    _fetch_fn: Callable | None = None,
) -> Path:
    """
    Main research loop. Returns path to output file.
    Raises RuntimeError if autoresearch.enabled = false.
    """
    if not _load_flag():
        raise RuntimeError(
            "autoresearch.enabled = false. Set it to true in protocols/feature-flags.yml first."
        )

    import importlib.util as _ilu

    _bspec = _ilu.spec_from_file_location(
        "autoresearch_budget", Path(__file__).parent / "budget.py"
    )
    _bmod = _ilu.module_from_spec(_bspec)
    _bspec.loader.exec_module(_bmod)
    Budget = _bmod.Budget
    BudgetExceeded = _bmod.BudgetExceeded

    budget = Budget(budget_rounds, budget_usd, budget_tokens)
    allowlist = DEFAULT_ALLOWLIST + (allow or [])
    search_fn = _search_fn or _local_search
    fetch_fn = _fetch_fn or _fetch_web

    findings: list[dict] = []
    sources: list[dict] = []
    contradictions: list[dict] = []
    trail: list[dict] = []
    prev_fact_count = 0

    seen_source_ids: set[str] = set()

    for round_n in range(budget_rounds):
        queries = _generate_queries(query, findings, round_n)
        round_results = []
        for q in queries:
            round_results.extend(search_fn(q))
            round_results.extend(fetch_fn(q, allowlist))

        candidate_facts = _extract_facts(round_results)
        new_contradictions = _check_contradictions(candidate_facts, findings)
        contradictions.extend(new_contradictions)
        findings.extend(candidate_facts)
        sources.extend(round_results)

        # Count only unique new source IDs for convergence (prevents multi-query inflation).
        unique_new_count = 0
        for f in candidate_facts:
            sid = f.get("source_id") or str(id(f))
            if sid not in seen_source_ids:
                seen_source_ids.add(sid)
                unique_new_count += 1

        trail.append(
            {
                "round": round_n + 1,
                "queries": queries,
                "facts_found": unique_new_count,
            }
        )

        # Estimate tokens (100 tokens per result, rough)
        round_tokens = len(round_results) * 100
        round_usd = 0.0  # local search is free

        try:
            budget.check_round(round_usd, round_tokens)
        except BudgetExceeded:
            break  # hard stop at cap

        # Convergence check on unique new facts
        if round_n > 0 and prev_fact_count > 0:
            if unique_new_count < prev_fact_count * CONVERGENCE_RATIO:
                break  # converged
        prev_fact_count = unique_new_count

    return _write_output(
        query, findings, sources, contradictions, trail, budget.summary(), output_path
    )
