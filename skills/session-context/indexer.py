#!/usr/bin/env python3
"""session-context: index past sessions, generate project docs, surface gotchas."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
EPISODIC_DIR = PHOENIX_ROOT / "memory" / "episodic"
SC_DIR = PHOENIX_ROOT / "brain" / "skills" / "session-context"


def project_slug(path: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", Path(path).name.lower()).strip("-")


def project_dir(path: str) -> Path:
    d = SC_DIR / project_slug(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_init(project_path: str) -> int:
    pdir = project_dir(project_path)
    pp = Path(project_path)
    print(f"Indexing project: {pp.name} → {pdir}")

    # sessions.jsonl — seed from episodic memory if available
    sessions_path = pdir / "sessions.jsonl"
    if not sessions_path.exists():
        sessions_path.write_text("", encoding="utf-8")
        print(
            "  Created sessions.jsonl (empty — sessions accumulate from session-end hook)"
        )

    # patterns.md
    patterns_path = pdir / "patterns.md"
    if not patterns_path.exists():
        patterns_path.write_text(
            f"# Patterns — {pp.name}\n\n"
            "_Recurring positive patterns discovered during sessions._\n\n"
            "<!-- Auto-populated by session-context end-session -->\n",
            encoding="utf-8",
        )
        print("  Created patterns.md")

    # gotchas.md
    gotchas_path = pdir / "gotchas.md"
    if not gotchas_path.exists():
        gotchas_content = f"# Gotchas — {pp.name}\n\n"
        # Seed from known Apple gotchas
        if "apple" in pp.name.lower():
            gotchas_content += """## [2026-04-19] PGLite parallel import crash
**What:** Running two `gbrain import` in parallel causes WASM abort
**Why:** PGLite only allows one writer; second process times out, corrupts lock
**Fix:** Always run gbrain imports sequentially; check postmaster.pid for stale locks

## [2026-04-19] bun not on PATH for gbrain shebang
**What:** `gbrain --version` returns `/usr/bin/env: 'bun': No such file or directory`
**Why:** System PATH lacks ~/.bun/bin; shebang can't find bun
**Fix:** Use wrapper script at bin/gbrain that hardcodes ~/.bun/bin/bun path

## [2026-04-19] git restore --staged fails on new repo
**What:** `git restore --staged <file>` fails with "pathspec did not match any file(s)"
**Why:** No HEAD yet in new repo — standard unstaging commands require at least one commit
**Fix:** Use `git update-index --force-remove <file>` for each path

## [2026-04-19] Apple pytest baseline = 144
**What:** Any extraction step that drops below 144 must be rolled back
**Why:** Hard gate from Run 2 spec — never regress
**Fix:** Run `pytest -q` after every change; rollback shim if count < 144
"""
        gotchas_path.write_text(gotchas_content, encoding="utf-8")
        print("  Created gotchas.md (seeded with known gotchas)")

    # architecture.md
    arch_path = pdir / "architecture.md"
    if not arch_path.exists():
        # Try to pull from vault if exists
        vault_arch = PHOENIX_ROOT / "vault" / "Apple-architecture-current.html"
        if vault_arch.exists() and "apple" in pp.name.lower():
            arch_path.write_text(
                f"# Architecture — {pp.name}\n\nSee also: vault/Apple-architecture-current.html\n\n"
                "_Auto-refreshed by graphify on every commit._\n",
                encoding="utf-8",
            )
        else:
            arch_path.write_text(
                f"# Architecture — {pp.name}\n\n_Run `/graphify build {project_path}` to populate._\n",
                encoding="utf-8",
            )
        print("  Created architecture.md")

    # CLAUDE.md.generated
    claude_md_path = pdir / "CLAUDE.md.generated"
    _write_claude_md(claude_md_path, pp, gotchas_path, patterns_path)
    print("  Created CLAUDE.md.generated")

    # AGENTS.md.generated
    agents_md_path = pdir / "AGENTS.md.generated"
    _write_agents_md(agents_md_path, pp)
    print("  Created AGENTS.md.generated")

    print(f"Init complete: {pdir}")
    return 0


def _write_claude_md(path: Path, project: Path, gotchas: Path, patterns: Path) -> None:
    gotcha_excerpt = ""
    if gotchas.exists():
        lines = gotchas.read_text(encoding="utf-8").splitlines()
        gotcha_excerpt = "\n".join(lines[:30])

    path.write_text(
        f"# {project.name} — Claude Code Context (auto-generated)\n\n"
        "## Quick start\n"
        f"- Project: `{project}`\n"
        "- Session context: `brain/skills/session-context/"
        + project.name.lower()
        + "/`\n"
        '- Search past sessions: `/session-context search "<keyword>"`\n\n'
        "## Known gotchas (read before every session)\n\n"
        f"{gotcha_excerpt}\n\n"
        "## Patterns\n"
        f"See: `{patterns}`\n\n"
        "_Regenerate: `/session-context gen-claude-md " + str(project) + "`_\n",
        encoding="utf-8",
    )


def _write_agents_md(path: Path, project: Path) -> None:
    path.write_text(
        f"# {project.name} — Agents Context (auto-generated)\n\n"
        "## Available agents (resolve from PhoenixGlobal)\n"
        "- `coder` — implementation, debug, refactor\n"
        "- `researcher` — research, investigate, explore\n"
        "- `reviewer` — review, audit, qa\n"
        "- `gatekeeper-supervisor` — validate, govern, approve (high-risk)\n\n"
        "## Context pre-load\n"
        "Every agent receives `gotchas.md` before starting (XHawk pattern).\n\n"
        "## Session search\n"
        '`/session-context search "<keyword>"` before any task.\n\n'
        "_Regenerate: `/session-context gen-agents-md " + str(project) + "`_\n",
        encoding="utf-8",
    )


def cmd_search(keyword: str) -> int:
    print(f"Searching past sessions for: {keyword}")
    found = 0
    for sessions_file in SC_DIR.rglob("sessions.jsonl"):
        for line in sessions_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                text = json.dumps(entry).lower()
                if keyword.lower() in text:
                    print(
                        f"  [{entry.get('ts', '?')}] {sessions_file.parent.name}: {entry.get('summary', '')[:80]}"
                    )
                    found += 1
            except json.JSONDecodeError:
                pass
    if not found:
        print("  No sessions found. Sessions accumulate as the session-end hook runs.")
    return 0


def cmd_end_session(project: str, summary: str, keywords: str = "") -> int:
    pdir = project_dir(project)
    sessions_path = pdir / "sessions.jsonl"
    entry = {
        "ts": datetime.now().isoformat(),
        "project": project,
        "summary": summary,
        "keywords": [k.strip() for k in keywords.split(",") if k.strip()],
        "outcome": "completed",
    }
    with sessions_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Session recorded: {sessions_path}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage: indexer.py <init|search|end-session|gen-claude-md|gen-agents-md> [args]"
        )
        return 1

    cmd = args[0]

    if cmd == "init" and len(args) > 1:
        return cmd_init(args[1])

    if cmd == "search" and len(args) > 1:
        return cmd_search(args[1])

    if cmd == "end-session":
        project = next(
            (args[i + 1] for i, a in enumerate(args) if a == "--project"), ""
        )
        summary = next(
            (args[i + 1] for i, a in enumerate(args) if a == "--summary"), ""
        )
        keywords = next(
            (args[i + 1] for i, a in enumerate(args) if a == "--keywords"), ""
        )
        return cmd_end_session(project, summary, keywords)

    if cmd in ("gen-claude-md", "gen-agents-md") and len(args) > 1:
        return cmd_init(args[1])  # init regenerates all docs

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
