# entity-extractor v1

**Run 5 Phase 3 — C2 Auto-entity extraction into gbrain**

## Purpose
Extract structured entity triples from graduated markdown files and upsert them
into the gbrain knowledge graph. Makes implicit relationships explicit and queryable.

## Inputs
- `markdown_chunk: str` — raw markdown text (chunked at 80 lines)
- `source_file: str` — originating file path for provenance
- `llm_response: str | None` — pre-built JSON for testing (skips LLM call)

## Outputs
- `list[Triple]` — dataclass with subject, predicate, object, confidence, source_file, source_line_range

## Model
`deepseek-v3.2` — accuracy over speed; low volume at post-graduation time.
Falls back to `""` (no triples) if LLM unavailable.

## Dedup strategy
Normalize subject/object (lowercase, strip, collapse whitespace), SHA-1 hash,
upsert by hash. "Alice", "alice", "Alice " all merge to one node.

## Feature flag
`entity_extractor.enabled` in `protocols/feature-flags.yml` (default false).
When false: `run_dream_extraction()` returns 0 immediately. No LLM calls made.

## Integration point
Called by dream cycle after graduation step when flag is true:
```python
from brain.skills.entity-extractor.v1.extractor import run_dream_extraction
count = run_dream_extraction(graduated_file_path)
```
