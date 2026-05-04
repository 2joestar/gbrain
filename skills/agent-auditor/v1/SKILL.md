# Agent Auditor — 32-Point Diagnostic

**Trigger:** `/audit` or `AgentAuditor.run_audit()`
**Version:** v1
**Status:** active
**Source:** Adapted from open-source AI diagnostic tool

## Purpose
Run 32 inspections on any AI agent. Checks memory, quality, tools, routing, safety, and performance. Returns scored report with pass/warn/fail per category.

## Usage
```python
from agent_auditor import AgentAuditor
auditor = AgentAuditor(complete_fn=your_llm_call)
report = await auditor.run_audit()
```
