"""
C4 — Session-scoped query context.
Stores per-session query history and rewrites pronouns using recent entities.
Feature flag: phoenix_search.session_context.enabled (default false).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
FLAGS_FILE = PHOENIX_ROOT / "protocols" / "feature-flags.yml"

# Cap: last 5 queries or 2000 tokens of history, whichever comes first.
MAX_HISTORY_QUERIES = 5
MAX_HISTORY_TOKENS = 2000


def _load_flag() -> bool:
    """Return True when session context is enabled in feature-flags.yml."""
    try:
        data = yaml.safe_load(FLAGS_FILE.read_text())
        return bool(
            data.get("phoenix_search", {})
            .get("session_context", {})
            .get("enabled", False)
        )
    except Exception:
        return False


def _session_dir(project_root: str, session_id: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    d = root / ".phoenix-local" / "session" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _history_file(project_root: str, session_id: str) -> Path:
    return _session_dir(project_root, session_id) / "query-history.jsonl"


def append_history(
    project_root: str, session_id: str, query: str, top_entities: list[str]
) -> None:
    """Append a query + its top_entities to the session history log."""
    hf = _history_file(project_root, session_id)
    entry = {"ts": time.time(), "query": query, "top_entities": top_entities}
    with hf.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_history(project_root: str, session_id: str) -> list[dict]:
    """Return the capped session history (last N queries, ≤2000 tokens)."""
    hf = _history_file(project_root, session_id)
    if not hf.exists():
        return []
    lines = hf.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    # Keep last MAX_HISTORY_QUERIES, then trim by token budget
    entries = entries[-MAX_HISTORY_QUERIES:]
    token_budget = MAX_HISTORY_TOKENS
    trimmed = []
    for e in reversed(entries):
        approx_tokens = len(e.get("query", "").split()) + len(
            " ".join(e.get("top_entities", [])).split()
        )
        if token_budget <= 0:
            break
        trimmed.insert(0, e)
        token_budget -= approx_tokens
    return trimmed


def _recent_entities(history: list[dict]) -> list[str]:
    """Flatten top_entities from most recent history entries, deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in reversed(history):
        for e in entry.get("top_entities", []):
            if e.lower() not in seen:
                seen.add(e.lower())
                out.append(e)
    return out


# Simple pronoun resolution without LLM (fast, free, deterministic for tests)
_PRONOUNS = re.compile(
    r"\b(she|her|he|his|him|they|their|them|it|its)\b", re.IGNORECASE
)


def _cheap_pronoun_resolve(query: str, entities: list[str]) -> str:
    """Replace the first pronoun in query with the most recent entity."""
    if not entities or not _PRONOUNS.search(query):
        return query
    candidate = entities[0]  # Most-recently-seen entity
    return _PRONOUNS.sub(candidate, query, count=1)


def rewrite_query(
    project_root: str, session_id: str, query: str, *, use_llm: bool = False
) -> str:
    """
    Rewrite query by resolving pronouns using session history entities.

    When use_llm=False (default for tests / offline use): cheap regex substitution.
    When use_llm=True: call cerebras-llama-3.3-70b via model-intent 'fast' chain.
    Flag must be on; otherwise returns query unchanged.
    """
    if not _load_flag():
        return query

    history = load_history(project_root, session_id)
    if not history:
        return query

    entities = _recent_entities(history)
    if not entities:
        return query

    if use_llm:
        return _llm_rewrite(query, entities)
    return _cheap_pronoun_resolve(query, entities)


def _llm_rewrite(query: str, entities: list[str]) -> str:
    """LLM-based pronoun resolution via Apple orchestrator router."""
    try:
        import sys

        sys.path.insert(0, str(PHOENIX_ROOT))
        from apple.core.model_router import ModelRouter

        router = ModelRouter()
        entity_list = ", ".join(entities[:5])
        prompt = (
            f"Rewrite the query by replacing any pronouns with the correct entity "
            f"from this context list: [{entity_list}].\n\n"
            f'Query: "{query}"\n\n'
            f"Return ONLY the rewritten query, no explanation."
        )

        import asyncio

        result = asyncio.run(
            router.complete(
                [{"role": "user", "content": prompt}],
                chain="fast",
                max_tokens=64,
            )
        )
        return result.strip().strip('"') if result else query
    except Exception:
        # Fallback to cheap resolve if LLM unavailable
        return _cheap_pronoun_resolve(query, entities)
