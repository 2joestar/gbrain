# SocratiCode — Codebase Intelligence

**Trigger:** Auto-starts on first `search_codebase()` call
**Version:** v1
**Status:** active
**Source:** github.com/giancarloerra/SocratiCode (1951 stars)

## Purpose
Semantic codebase search with embeddings, dependency graphs, and impact analysis. Auto-starts MCP server on-demand — no manual steps. All agents can call via the service wrapper.

## Usage
```python
from socraticode.service import search_codebase, ensure_running
ensure_running()  # starts server if not running
results = search_codebase("authentication flow")
```
