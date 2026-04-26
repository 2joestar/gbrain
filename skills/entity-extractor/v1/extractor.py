"""
C2 — Auto-entity extraction into gbrain.
Run 5 Phase 3.

Feature flag: entity_extractor.enabled (default false in protocols/feature-flags.yml).
Model: deepseek-v3.2 (accuracy over speed; low volume at post-graduation time).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Generator

import yaml

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
FLAGS_FILE = PHOENIX_ROOT / "protocols" / "feature-flags.yml"

# Chunk size in lines to avoid oversized LLM prompts
CHUNK_LINES = 80

EXTRACTION_PROMPT = """\
Extract all entity relationships from the following markdown text.
Return ONLY a JSON array of triples. Each triple must have:
  subject (string), predicate (string), object (string), confidence (float 0..1)

Example output:
[
  {{"subject": "Alice", "predicate": "reports_to", "object": "Bob", "confidence": 0.95}},
  {{"subject": "Bob", "predicate": "leads", "object": "Team-Alpha", "confidence": 0.9}}
]

If no relationships found, return an empty array [].

Text:
{text}
"""


class Triple:
    """Immutable entity triple with provenance."""

    __slots__ = (
        "subject",
        "predicate",
        "object",
        "confidence",
        "source_file",
        "source_line_range",
    )

    def __init__(
        self,
        subject: str,
        predicate: str,
        object: str,
        confidence: float,
        source_file: str,
        source_line_range: tuple,
    ) -> None:
        self.subject = subject
        self.predicate = predicate
        self.object = object
        self.confidence = confidence
        self.source_file = source_file
        self.source_line_range = source_line_range


def _load_flag() -> bool:
    try:
        data = yaml.safe_load(FLAGS_FILE.read_text())
        return bool(data.get("entity_extractor", {}).get("enabled", False))
    except Exception:
        return False


def _normalize(s: str) -> str:
    """Lowercase, strip, collapse whitespace for dedup."""
    return re.sub(r"\s+", " ", s.strip().lower())


def _triple_hash(t: Triple) -> str:
    key = f"{_normalize(t.subject)}|{_normalize(t.predicate)}|{_normalize(t.object)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _chunk_text(text: str) -> Generator[tuple[str, int, int], None, None]:
    """Yield (chunk_text, start_line, end_line) in CHUNK_LINES windows."""
    lines = text.splitlines()
    for i in range(0, len(lines), CHUNK_LINES):
        chunk_lines = lines[i : i + CHUNK_LINES]
        yield "\n".join(chunk_lines), i + 1, i + len(chunk_lines)


def extract(
    markdown_chunk: str,
    source_file: str = "",
    source_line_start: int = 1,
    *,
    llm_response: str | None = None,
) -> list[Triple]:
    """
    Extract triples from markdown_chunk.

    llm_response: inject a pre-built JSON string (for testing without live LLM).
    When None and no live LLM available, returns [].
    """
    if llm_response is None:
        llm_response = _call_llm(markdown_chunk)
    if not llm_response:
        return []
    try:
        # Strip markdown code fences if present
        cleaned = re.sub(
            r"^```[a-z]*\n?|```$", "", llm_response.strip(), flags=re.MULTILINE
        ).strip()
        raw = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []

    triples: list[Triple] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        subj = str(item.get("subject", "")).strip()
        pred = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()
        if not subj or not pred or not obj:
            continue
        conf = float(item.get("confidence", 0.5))
        line_end = source_line_start + markdown_chunk.count("\n")
        triples.append(
            Triple(
                subject=subj,
                predicate=pred,
                object=obj,
                confidence=conf,
                source_file=source_file,
                source_line_range=(source_line_start, line_end),
            )
        )
    return triples


def extract_file(path: str | Path) -> list[Triple]:
    """Extract all triples from a markdown file (chunked)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    triples: list[Triple] = []
    for chunk, start, end in _chunk_text(text):
        chunk_triples = extract(chunk, source_file=str(p), source_line_start=start)
        triples.extend(chunk_triples)
    return triples


def _call_llm(text: str) -> str:
    """Call deepseek-v3.2 via Apple ModelRouter. Returns raw string or ''."""
    try:
        import sys

        sys.path.insert(0, str(PHOENIX_ROOT))
        from apple.core.model_router import ModelRouter

        router = ModelRouter()
        prompt = EXTRACTION_PROMPT.format(text=text[:4000])
        result = asyncio.run(
            router.complete(
                [{"role": "user", "content": prompt}],
                chain="default",
                max_tokens=1024,
            )
        )
        return result or ""
    except Exception:
        return ""


def upsert_triples(triples: list[Triple], gbrain_path: str | Path | None = None) -> int:
    """
    Upsert triples into gbrain graph.json with dedup by normalized hash.
    Returns count of new triples added.
    """
    if not triples:
        return 0

    if gbrain_path is None:
        gbrain_path = PHOENIX_ROOT / "graph" / "graphify" / "graph.json"
    gbrain_path = Path(gbrain_path)

    # Load existing graph
    if gbrain_path.exists():
        try:
            graph = json.loads(gbrain_path.read_text(encoding="utf-8"))
        except Exception:
            graph = {"nodes": [], "edges": []}
    else:
        gbrain_path.parent.mkdir(parents=True, exist_ok=True)
        graph = {"nodes": [], "edges": []}

    # Build existing edge hash set for dedup
    existing_hashes: set[str] = {
        e.get("hash", "") for e in graph.get("edges", []) if "hash" in e
    }
    node_ids: set[str] = {_normalize(n.get("id", "")) for n in graph.get("nodes", [])}

    added = 0
    for t in triples:
        h = _triple_hash(t)
        if h in existing_hashes:
            continue  # duplicate — skip

        # Ensure nodes exist
        for entity in (_normalize(t.subject), _normalize(t.object)):
            if entity not in node_ids:
                graph.setdefault("nodes", []).append(
                    {"id": entity, "type": "entity", "source": t.source_file}
                )
                node_ids.add(entity)

        graph.setdefault("edges", []).append(
            {
                "hash": h,
                "source": _normalize(t.subject),
                "target": _normalize(t.object),
                "predicate": t.predicate,
                "confidence": t.confidence,
                "provenance": t.source_file,
                "line_range": list(t.source_line_range),
            }
        )
        existing_hashes.add(h)
        added += 1

    if added > 0:
        gbrain_path.write_text(
            json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return added


def run_dream_extraction(
    markdown_path: str | Path, gbrain_path: str | Path | None = None
) -> int:
    """
    Called by dream cycle after graduation when entity_extractor.enabled.
    Returns count of new triples upserted.
    """
    if not _load_flag():
        return 0
    triples = extract_file(markdown_path)
    return upsert_triples(triples, gbrain_path)
