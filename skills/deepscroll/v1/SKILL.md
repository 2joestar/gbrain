# DeepScroll — LLM-Guided Codebase Navigation

**Trigger:** `/deepscroll <query>` or `DeepScroll.find_relevant()`
**Version:** v1
**Status:** active
**Source:** Inspired by @Erick's DeepScroll concept

## Purpose
Navigate 10M+ token codebases by recursively narrowing scope. LLM guides the search to relevant files without loading everything into context.

## Usage
```python
from deepscroll import DeepScroll
ds = DeepScroll(complete_fn=your_llm_call)
files = await ds.find_relevant("authentication flow", "/path/to/codebase")
```
