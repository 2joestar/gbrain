#!/usr/bin/env python3
"""Graduate a dream/staged candidate into memory/semantic/."""

import shutil
import sys
from datetime import datetime
from pathlib import Path

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
STAGED = PHOENIX_ROOT / "dream" / "staged"
GRADUATED = PHOENIX_ROOT / "dream" / "graduated"
SEMANTIC = PHOENIX_ROOT / "memory" / "semantic"
LOGS = PHOENIX_ROOT / "dream" / "logs"


def graduate(candidate_path: str, rationale: str) -> int:
    src = Path(candidate_path)
    if not src.exists():
        print(f"ERROR: {src} not found")
        return 1
    if not rationale.strip():
        print(
            "ERROR: rationale required — rubber-stamped graduations are the failure mode"
        )
        return 1

    text = src.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = src.stem.replace("candidate-", "lesson-")

    # Write to semantic memory
    SEMANTIC.mkdir(parents=True, exist_ok=True)
    dst = SEMANTIC / f"{slug}.md"
    dst.write_text(
        f"# Graduated Lesson — {ts}\n\n**Rationale:** {rationale}\n\n---\n\n{text}\n",
        encoding="utf-8",
    )

    # Archive to graduated/
    GRADUATED.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, GRADUATED / src.name)
    src.unlink()

    # Log
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "graduations.jsonl").open("a") as f:
        import json

        f.write(json.dumps({"ts": ts, "file": src.name, "rationale": rationale}) + "\n")

    print(f"Graduated: {dst}")
    print(f"Archived:  {GRADUATED / src.name}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('Usage: pg_graduate.py <staged-file> "<rationale>"')
        sys.exit(1)
    sys.exit(graduate(sys.argv[1], sys.argv[2]))
