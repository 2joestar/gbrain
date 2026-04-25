# gatekeeper — Hybrid Governance Gate

**Version:** v1
**Status:** active
**Renamed from:** hermes (Run 4 Phase 2)

## Purpose
Governance layer for all agent actions. Low-risk → auto-approve. Medium-risk → validate
content. High-risk → require explicit `approved: True` flag. Also handles loop detection
and hallucination heuristics.

## Public API
```python
from phoenix_skills.gatekeeper.v1.gatekeeper import Gatekeeper

g = Gatekeeper()
g.validate({"type": "memory_write", "content": "..."})  # True/False
g.validate({"type": "system_modify", "approved": True}) # high-risk w/ approval
g.assess_risk({"type": "file_delete"})                  # "high"
g.detect_loop({"type": "tool_execute", "input": "..."}) # True if looping
g.check_hallucination("response text")                  # True if filler/hallucination
g.validate_output("response text")                      # (bool, reason)
g.stats()                                               # {"approved":N, "blocked":N}
```

## Risk levels
- **low:** auto-approve — any action not in medium/high sets
- **medium:** `memory_write, tool_execute, file_write, config_change, agent_spawn`
- **high:** `system_modify, api_key_change, tool_upgrade, model_upgrade, service_restart, file_delete`

## Loop detection
Sliding window (default 30s): same action key appearing ≥3 times → blocked.
Config keys: `gatekeeper.loop_threshold`, `gatekeeper.loop_detection_window` in system.yaml.

## Notes
- Renamed from `hermes` in Run 4. Old path (`brain/skills/hermes/v1/`) kept as deprecation shim until Run 5.
