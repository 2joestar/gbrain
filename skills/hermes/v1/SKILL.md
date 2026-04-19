# hermes — Hybrid Governance Supervisor

**Version:** v1
**Status:** absorbed
**Source:** apple-mamasethu/apple/governance/hermes.py
**Extracted:** 2026-04-20 (Run 2, Phase 5 step 1)

## Purpose
Governance layer for all agent actions. Low-risk → auto-approve. Medium-risk → validate
content. High-risk → require explicit `approved: True` flag. Also handles loop detection
and hallucination heuristics.

## Public API
```python
from phoenix_skills.hermes.v1.hermes import Hermes

h = Hermes()
h.validate({"type": "memory_write", "content": "..."})  # True/False
h.validate({"type": "system_modify", "approved": True}) # high-risk w/ approval
h.assess_risk({"type": "file_delete"})                  # "high"
h.detect_loop({"type": "tool_execute", "input": "..."}) # True if looping
h.check_hallucination("response text")                  # True if filler/hallucination
h.validate_output("response text")                      # (bool, reason)
h.stats()                                               # {"approved":N, "blocked":N}
```

## Risk levels
- **low:** auto-approve — any action not in medium/high sets
- **medium:** `memory_write, tool_execute, file_write, config_change, agent_spawn`
- **high:** `system_modify, api_key_change, tool_upgrade, model_upgrade, service_restart, file_delete`

## Loop detection
Sliding window (default 30s): same action key appearing ≥3 times → blocked.
Config keys: `hermes.loop_threshold`, `hermes.loop_detection_window` in system.yaml.

## Notes
- `_task_worker` Hermes-remediation gap (from Master Plan) to be closed at Phase 5 step 7 (Observer).
