"""
C5 — Confidence + explored + last_reviewed frontmatter migration.
Run 5 Phase 5.

Adds three fields to every markdown file under memory/semantic/ and vault/:
  confidence: 0.5
  explored: false
  last_reviewed: null

Migration is idempotent — existing values are preserved.
Feature flag: confidence_frontmatter.enabled in protocols/feature-flags.yml.
  When off: schema fields are still added (migration always runs).
  CLI subcommands (--low-confidence, --stale, --unexplored) error politely when off.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
FLAGS_FILE = PHOENIX_ROOT / "protocols" / "feature-flags.yml"

# Directories to migrate
MIGRATE_DIRS = [
    PHOENIX_ROOT / "memory" / "semantic",
    PHOENIX_ROOT / "vault",
]

# Fields with defaults (in insertion order)
DEFAULT_FIELDS = {
    "confidence": 0.5,
    "explored": False,
    "last_reviewed": None,
}

# Stale threshold: 90 days (parity with claude-obsidian)
STALE_DAYS = 90

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _load_flag() -> bool:
    try:
        data = yaml.safe_load(FLAGS_FILE.read_text())
        return bool(data.get("confidence_frontmatter", {}).get("enabled", False))
    except Exception:
        return False


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text). frontmatter_dict is {} if absent."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    body = text[m.end() :]
    return fm, body


def _serialize_frontmatter(fm: dict) -> str:
    return (
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        + "---\n"
    )


def _add_defaults(fm: dict) -> bool:
    """Add missing fields with defaults. Returns True if any field was added."""
    changed = False
    for field, default in DEFAULT_FIELDS.items():
        if field not in fm:
            fm[field] = default
            changed = True
    return changed


def migrate_file(path: Path) -> bool:
    """
    Idempotently add default frontmatter fields to a markdown file.
    Returns True if the file was modified.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    fm, body = _parse_frontmatter(text)

    # If no frontmatter block exists, create one
    if not fm and not text.startswith("---"):
        fm = {}

    changed = _add_defaults(fm)
    if not changed:
        return False

    new_text = _serialize_frontmatter(fm) + body
    path.write_text(new_text, encoding="utf-8")
    return True


def migrate_all() -> dict:
    """
    Walk MIGRATE_DIRS and add defaults to all markdown files.
    Returns {scanned: N, modified: M}.
    """
    scanned = 0
    modified = 0
    for directory in MIGRATE_DIRS:
        if not directory.exists():
            continue
        for md_file in directory.rglob("*.md"):
            scanned += 1
            if migrate_file(md_file):
                modified += 1
    return {"scanned": scanned, "modified": modified}


# ── CLI review functions ──────────────────────────────────────────────────────


def _load_entries() -> list[tuple[Path, dict]]:
    """Load all markdown files with their parsed frontmatter."""
    entries = []
    for directory in MIGRATE_DIRS:
        if not directory.exists():
            continue
        for md_file in directory.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                fm, _ = _parse_frontmatter(text)
                entries.append((md_file, fm))
            except Exception:
                pass
    return entries


def review_low_confidence(threshold: float = 0.5) -> list[dict]:
    """Return entries with confidence < threshold. Requires flag on."""
    if not _load_flag():
        return [{"error": "confidence_frontmatter.enabled is false"}]
    return [
        {"path": str(p), "confidence": fm.get("confidence", 0.5)}
        for p, fm in _load_entries()
        if float(fm.get("confidence", 0.5)) < threshold
    ]


def review_stale(days: int = STALE_DAYS) -> list[dict]:
    """Return entries where last_reviewed is null or older than `days` days."""
    if not _load_flag():
        return [{"error": "confidence_frontmatter.enabled is false"}]
    cutoff = date.today().toordinal() - days
    results = []
    for p, fm in _load_entries():
        lr = fm.get("last_reviewed")
        if lr is None:
            results.append({"path": str(p), "last_reviewed": None})
        else:
            try:
                lr_date = date.fromisoformat(str(lr)) if isinstance(lr, str) else lr
                if lr_date.toordinal() < cutoff:
                    results.append({"path": str(p), "last_reviewed": str(lr)})
            except Exception:
                results.append({"path": str(p), "last_reviewed": str(lr)})
    return results


def review_unexplored() -> list[dict]:
    """Return entries where explored=false."""
    if not _load_flag():
        return [{"error": "confidence_frontmatter.enabled is false"}]
    return [
        {"path": str(p), "explored": fm.get("explored", False)}
        for p, fm in _load_entries()
        if not fm.get("explored", False)
    ]


def mark_reviewed(path: str | Path) -> bool:
    """Set last_reviewed to today on a specific file. Returns True on success."""
    p = Path(path)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8", errors="ignore")
    fm, body = _parse_frontmatter(text)
    _add_defaults(fm)
    fm["last_reviewed"] = date.today().isoformat()
    p.write_text(_serialize_frontmatter(fm) + body, encoding="utf-8")
    return True


def search_with_min_confidence(
    entries: list[tuple[Path, dict]], min_confidence: float
) -> list[dict]:
    """Filter entries by confidence >= min_confidence. No-op when flag off."""
    if not _load_flag():
        return [{"path": str(p), "fm": fm} for p, fm in entries]  # return all unchanged
    return [
        {"path": str(p), "fm": fm}
        for p, fm in entries
        if float(fm.get("confidence", 0.5)) >= min_confidence
    ]
