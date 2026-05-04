# RLM Navigator — Recursive Document Understanding

**Trigger:** `/rlm <question>` or programmatic `RLMNavigator.query()`
**Version:** v1
**Status:** active
**Source:** MIT CSAIL paper arxiv.org/abs/2512.24601

## Purpose
Process massive documents (10M+ tokens) without loading into context. AI navigates recursively — writes code to search, slice, and read only relevant sections. 100x native context window. No information loss. No context rot.

## Usage
```python
from rlm_navigator import RLMNavigator
nav = RLMNavigator(complete_fn=your_llm_call)
result = await nav.query(document_text, "What is the key finding?")
```

## Architecture
Document → AI writes search/slice code → spawns sub-readers → synthesizes answer
Replaces chunked RAG for large document processing.
