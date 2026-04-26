"""
C2 — Contradiction detection.
Run 6 Phase 1.

Feature flag: contradiction_detection.enabled in protocols/feature-flags.yml.
Model: deepseek-v3.2 (low volume, high stakes; false negatives worse than false positives).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
FLAGS_FILE = PHOENIX_ROOT / "protocols" / "feature-flags.yml"
CONTRADICTIONS_DIR = PHOENIX_ROOT / "dream" / "contradictions"

# Confidence penalty when a contradiction is found
CONFIDENCE_PENALTY = 0.2
CONFIDENCE_FLOOR = 0.1

DETECTION_PROMPT = """\
You are a fact-consistency checker. Determine if the following two facts about \
the same entity contradict each other.

Fact A: {fact_a}
Fact B: {fact_b}

A contradiction means they CANNOT both be true at the same time.
Differences in facet (e.g. role vs location) are NOT contradictions.

Respond ONLY with valid JSON:
{{"conflict": true|false, "reason": "<one sentence explanation>"}}
"""

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _load_flag() -> bool:
    try:
        data = yaml.safe_load(FLAGS_FILE.read_text())
        return bool(data.get("contradiction_detection", {}).get("enabled", False))
    except Exception:
        return False


def _call_llm(fact_a: str, fact_b: str) -> dict | None:
    """Call deepseek-v3.2 to check contradiction. Returns dict or None on error."""
    try:
        import asyncio
        import sys

        sys.path.insert(0, str(PHOENIX_ROOT))
        from apple.core.model_router import ModelRouter

        router = ModelRouter()
        prompt = DETECTION_PROMPT.format(fact_a=fact_a[:500], fact_b=fact_b[:500])
        result = asyncio.run(
            router.complete(
                [{"role": "user", "content": prompt}],
                chain="default",
                max_tokens=128,
            )
        )
        if not result:
            return None
        cleaned = re.sub(
            r"^```[a-z]*\n?|```$", "", result.strip(), flags=re.MULTILINE
        ).strip()
        return json.loads(cleaned)
    except Exception:
        return None


def check(
    new_fact: str,
    existing_facts: list[dict],
    *,
    llm_response: str | None = None,
) -> list[dict]:
    """
    Check new_fact against existing_facts for contradictions.

    existing_facts: list of {text: str, id: str, confidence: float}
    llm_response: inject pre-built JSON string (for testing).
      If provided, used instead of LLM for ALL existing_facts (single call).
    Returns list of conflict dicts: {conflict: bool, reason: str, existing_id: str}
    """
    conflicts = []
    for existing in existing_facts:
        if llm_response is not None:
            # Injected response for testing
            try:
                parsed = json.loads(llm_response)
            except (json.JSONDecodeError, ValueError):
                parsed = {"conflict": False, "reason": "parse error"}
        else:
            parsed = _call_llm(new_fact, existing.get("text", ""))
            if parsed is None:
                continue

        if parsed.get("conflict"):
            conflicts.append(
                {
                    "conflict": True,
                    "reason": parsed.get("reason", ""),
                    "existing_id": existing.get("id", ""),
                    "existing_confidence": existing.get("confidence", 0.5),
                }
            )
    return conflicts


def lower_confidence(path: str | Path, amount: float = CONFIDENCE_PENALTY) -> float:
    """
    Lower the confidence field in a markdown file's frontmatter.
    Returns the new confidence value. No-op if file missing.
    """
    p = Path(path)
    if not p.exists():
        return 0.5
    text = p.read_text(encoding="utf-8", errors="ignore")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return 0.5
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    old = float(fm.get("confidence", 0.5))
    new = max(round(old - amount, 4), CONFIDENCE_FLOOR)
    fm["confidence"] = new
    body = text[m.end() :]
    new_text = (
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        + "---\n"
        + body
    )
    p.write_text(new_text, encoding="utf-8")
    return new


def restore_confidence(path: str | Path, amount: float = CONFIDENCE_PENALTY) -> float:
    """
    Restore (bump up) confidence after a resolve --keep action.
    Returns new confidence value.
    """
    p = Path(path)
    if not p.exists():
        return 0.5
    text = p.read_text(encoding="utf-8", errors="ignore")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return 0.5
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    old = float(fm.get("confidence", 0.5))
    new = min(round(old + amount, 4), 1.0)
    fm["confidence"] = new
    body = text[m.end() :]
    new_text = (
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        + "---\n"
        + body
    )
    p.write_text(new_text, encoding="utf-8")
    return new


def archive_entry(path: str | Path) -> None:
    """Mark an entry as archived: true in its frontmatter."""
    p = Path(path)
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8", errors="ignore")
    m = FRONTMATTER_RE.match(text)
    fm = yaml.safe_load(m.group(1)) if m else {}
    fm["archived"] = True
    body = text[m.end() :] if m else text
    p.write_text(
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        + "---\n"
        + body,
        encoding="utf-8",
    )


def write_contradiction_record(
    new_entry_id: str,
    new_fact: str,
    existing_entry_id: str,
    existing_fact: str,
    reason: str,
    entity: str = "",
) -> Path:
    """Write a contradiction record to dream/contradictions/ and return its path."""
    CONTRADICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    entity_hash = hashlib.sha1(entity.encode()).hexdigest()[:8]
    ts_slug = ts.replace(":", "").replace(".", "")[:17]
    filename = f"{ts_slug}-{entity_hash}.md"
    path = CONTRADICTIONS_DIR / filename

    content = (
        f"---\n"
        f"entity: {entity!r}\n"
        f"new_entry_id: {new_entry_id!r}\n"
        f"existing_entry_id: {existing_entry_id!r}\n"
        f"detected_at: {ts!r}\n"
        f"resolved: false\n"
        f"---\n\n"
        f"# Contradiction: {entity}\n\n"
        f"**Entry A** (id={new_entry_id}):\n> {new_fact}\n\n"
        f"**Entry B** (id={existing_entry_id}):\n> {existing_fact}\n\n"
        f"**Reason:** {reason}\n\n"
        f"**Resolve:**\n"
        f"  phoenix resolve {filename} --keep {new_entry_id}\n"
        f"  phoenix resolve {filename} --keep {existing_entry_id}\n"
        f"  phoenix resolve {filename} --both-wrong\n"
        f"  phoenix resolve {filename} --merge\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def list_contradictions() -> list[dict]:
    """Return all unresolved contradiction records."""
    if not CONTRADICTIONS_DIR.exists():
        return []
    results = []
    for f in sorted(CONTRADICTIONS_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        m = FRONTMATTER_RE.match(text)
        fm = yaml.safe_load(m.group(1)) if m else {}
        if not fm.get("resolved", False):
            results.append({"path": str(f), "id": f.name, **fm})
    return results


def resolve_contradiction(
    contradiction_id: str,
    *,
    keep_entry_path: str | None = None,
    other_entry_path: str | None = None,
    both_wrong: bool = False,
) -> bool:
    """
    Record resolution of a contradiction.
    Returns True on success.
    """
    rec_path = CONTRADICTIONS_DIR / contradiction_id
    if not rec_path.exists():
        return False

    if both_wrong:
        if keep_entry_path:
            archive_entry(keep_entry_path)
        if other_entry_path:
            archive_entry(other_entry_path)
    elif keep_entry_path:
        restore_confidence(keep_entry_path)
        if other_entry_path:
            archive_entry(other_entry_path)

    # Mark contradiction record as resolved
    text = rec_path.read_text(encoding="utf-8", errors="ignore")
    m = FRONTMATTER_RE.match(text)
    fm = yaml.safe_load(m.group(1)) if m else {}
    fm["resolved"] = True
    fm["resolved_at"] = datetime.now(timezone.utc).isoformat()
    body = text[m.end() :] if m else text
    rec_path.write_text(
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        + "---\n"
        + body,
        encoding="utf-8",
    )
    return True


def render_search_block(results: list[dict]) -> str:
    """
    Given search results, prepend a contradiction warning block if any result
    has contradicts or contradicted_by populated. Returns formatted string.
    """
    if not _load_flag():
        return ""
    contested = [r for r in results if r.get("contradicts") or r.get("contradicted_by")]
    if not contested:
        return ""

    lines = ["⚠️  CONTRADICTION detected"]
    for r in contested[:3]:
        title = r.get("title", r.get("id", "?"))
        lines.append(f'   Entry: "{title}" (confidence={r.get("confidence", "?")})')
        if r.get("contradicts"):
            lines.append(f"   Contradicts: {r['contradicts']}")
    lines.append("   Resolve: phoenix resolve <contradiction-id> --keep <entry-id>")
    return "\n".join(lines)
