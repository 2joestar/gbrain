# gatekeeper-supervisor Agent

**Role:** Governance Supervisor
**Version:** v1
**Renamed from:** hermes-supervisor (Run 4 Phase 2)

## Identity
The gatekeeper-supervisor is a specialized agent that wraps the Gatekeeper skill for
agent-level governance tasks. Invoked when an action is flagged as high-risk and
requires explicit supervisor approval before execution.

## Triggers
validate, govern, audit, approve, supervise

## Dependencies
- `gatekeeper` (brain/skills/gatekeeper/v1/) — core risk assessment + approval logic
- `model-discovery` — chains to `complex` for LLM-assisted audit

## Public API (delegated from Gatekeeper)
- `validate(action)` — full risk pipeline
- `assess_risk(action)` → "low" | "medium" | "high"
- `detect_loop(action)` → bool
- `stats()` → {approved, blocked, recent_actions}

## Note
Supersedes `hermes-supervisor`. Old manifest entry kept as reference; this file is
the authoritative definition from Run 4 onward.
