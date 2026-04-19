# ctx-local — Context-Aware Skill & Agent Recommender

**Trigger:** `/ctx` | auto-runs on session-start, file-save, pre-commit, session-end hooks
**Version:** v1
**Status:** active

## Purpose
Walk `manifests/skills.yml` and `manifests/agents.yml`, match the current session context (open files, active stack signals, keywords) against each item's `triggers` and `tags` fields, and return the top-k recommendations. Enforces the ≤15-skill context budget from `protocols/context-budget.md`.

## Usage

```bash
# Recommend skills for current context
python3 /mnt/c/Users/PhoenixGlobal/brain/skills/ctx-local/recommender.py --context "your task description"

# Recommend with open-file signals
python3 recommender.py --context "fix tests" --files "test_*.py apple/core/"

# Check budget (returns 0 if OK, 1 if over-budget)
python3 recommender.py --budget-check

# Session-start mode (top-5, structured output)
python3 recommender.py --session-start --project /mnt/c/Projects/apple-mamasethu
```

## Budget rule
- Hard cap: **15 skills** loaded per session (from `protocols/context-budget.md`)
- Hook rejects sessions that would load >15 skills
- Warning at >10 skills loaded

## How it works
1. Loads `manifests/skills.yml` + `manifests/agents.yml`
2. Tokenizes context (task description + open file paths)
3. Scores each item: trigger keyword match × 3 + tag match × 1 + status weight
4. Returns top-k sorted by score, filtered to active/seeded/absorbed status
5. On session-start: writes recommendation to `memory/working/ctx-recommendations.json`

## Outputs
- Structured JSON: `{recommended: [{name, score, triggers, tags, reason}], budget: {used, limit, ok}}`
- Human-readable summary to stdout

## Integration points
- `hooks/on_session_start.sh` — calls `--session-start` before loading skills
- `hooks/on_session_end.sh` — records which skills were actually used
- Pre-commit hook — verifies budget before commit
- `protocols/context-budget.md` — source of truth for limits
