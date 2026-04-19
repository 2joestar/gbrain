# session-context — Past-Session Search & Project Doc Generator (XHawk-pattern)

**Trigger:** `/session-context` | session-end hook | weekly Sunday 5 AM
**Version:** v1
**Status:** active

## Purpose
Agents don't start from zero. Index every past session + codebase + architecture; search by
keyword; auto-generate `CLAUDE.md` and `AGENTS.md` per project with patterns, negative
knowledge (gotchas), and architectural summaries. New projects, new subagents, new
collaborators get the right context immediately.

## Usage

```bash
# Index a project (generates sessions.jsonl, patterns.md, gotchas.md, architecture.md)
/session-context init /mnt/c/Projects/apple-mamasethu

# Search past sessions
/session-context search "modal router rollback"
/session-context search "telegram bot startup"

# Generate/refresh Claude Code + Agents docs
/session-context gen-claude-md /mnt/c/Projects/apple-mamasethu
/session-context gen-agents-md /mnt/c/Projects/apple-mamasethu

# Append current session summary (called by session-end hook)
/session-context end-session --project /mnt/c/Projects/apple-mamasethu \
  --summary "fixed X, learned Y, gotcha: Z"
```

## Storage layout per project
```
brain/skills/session-context/<project-slug>/
  sessions.jsonl        # every session: ts, keywords, outcome, summary
  patterns.md           # recurring positive patterns
  gotchas.md            # negative knowledge — mistakes + correct approach
  architecture.md       # auto-extracted from graphify graph
  CLAUDE.md.generated   # drop-in for Claude Code
  AGENTS.md.generated   # drop-in for multi-agent setups
```

## The XHawk feature: gotchas.md
- Entries: what went wrong, why, and the correct approach
- Format:
  ```
  ## [2026-04-19] PGLite parallel import crash
  **What:** Running two `gbrain import` in parallel causes WASM abort
  **Why:** PGLite only allows one writer; second process times out, corrupts lock
  **Fix:** Always run gbrain imports sequentially; check postmaster.pid for stale locks
  ```
- Used by: every new subagent gets this file as context before starting

## How it works
1. **Data source:** `memory/episodic/` + session logs from `hooks/on_session_end.sh`
2. **Graph queries:** walks graphify's knowledge graph for entity relationships
3. **GitHub integration:** hooks into `git commit` for incremental index refresh
4. **Context compounds:** every commit, every session makes future agents smarter

## Relationship to ctx and graphify
- `ctx-local` = recommends which *skills/agents* to load
- `graphify` = builds/queries the *knowledge graph* over files
- `session-context` = searches past *sessions*, writes project docs, preserves gotchas
Three complementary surfaces, no overlap.

## Implementation
- `brain/skills/session-context/indexer.py` — init/search/gen commands
- `brain/skills/session-context/<project-slug>/` — per-project data
