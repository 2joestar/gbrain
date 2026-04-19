#!/usr/bin/env python3
"""PhoenixGlobal dream cycle — staging only, no reasoning, no semantic mutations.

Walks dream/queue/, clusters candidates, drafts to dream/staged/.
Does NOT mutate memory/semantic/ or skills/ — Security by Absence.
Graduate via: python3 graduate.py <staged-file>
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
QUEUE = PHOENIX_ROOT / "dream" / "queue"
STAGED = PHOENIX_ROOT / "dream" / "staged"
REJECTED = PHOENIX_ROOT / "dream" / "rejected"
LOGS = PHOENIX_ROOT / "dream" / "logs"
WORKING = PHOENIX_ROOT / "memory" / "working"

MIN_LENGTH = 50
MAX_QUEUE_AGE_DAYS = 90


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _load_queue() -> list:
    items = []
    for f in sorted(QUEUE.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="ignore").strip()
        if len(text) >= MIN_LENGTH:
            items.append({"path": f, "text": text, "hash": _sha8(text)})
    return items


def _heuristic_check(item: dict) -> tuple:
    text = item["text"]
    if len(text) < MIN_LENGTH:
        return False, "too_short"
    low = text.lower()
    junk_patterns = [r"test\s+entry", r"lorem ipsum", r"placeholder", r"^#\s*todo\s*$"]
    for p in junk_patterns:
        if re.search(p, low):
            return False, f"junk_pattern:{p}"
    return True, "ok"


def _already_staged(item: dict) -> bool:
    return any(f.name.endswith(f"-{item['hash']}.md") for f in STAGED.glob("*.md"))


def _draft_staged(item: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = STAGED / f"candidate-{ts}-{item['hash']}.md"
    content = (
        f"# Dream Candidate — {ts}\n\n"
        f"**Source:** {item['path'].name}\n"
        f"**Hash:** {item['hash']}\n"
        f"**Status:** staged\n\n"
        f"---\n\n{item['text']}\n\n"
        f"---\n\n"
        f"*Review: `python3 brain/scripts/graduate.py` or `reject.py`*\n"
    )
    out.write_text(content, encoding="utf-8")
    return out


def _move_to_rejected(item: dict, reason: str) -> None:
    REJECTED.mkdir(parents=True, exist_ok=True)
    out = REJECTED / f"rejected-{item['hash']}.md"
    out.write_text(
        f"# Rejected — {item['path'].name}\n**Reason:** {reason}\n\n{item['text'][:200]}\n",
        encoding="utf-8",
    )
    item["path"].unlink(missing_ok=True)


def _write_review_queue(staged_paths: list, rejected: int, skipped: int) -> None:
    WORKING.mkdir(parents=True, exist_ok=True)
    report = WORKING / "REVIEW_QUEUE.md"
    lines = [
        f"# Dream Review Queue — {datetime.now().isoformat()}\n",
        f"**Staged:** {len(staged_paths)} | **Rejected:** {rejected} | **Skipped (dup):** {skipped}\n\n",
        "## Candidates to review\n",
    ]
    for p in staged_paths:
        lines.append(f"- `{p.name}`\n")
    lines += [
        "\n## Commands\n",
        "```bash\n",
        "python3 brain/scripts/graduate.py dream/staged/<file>\n",
        'python3 brain/scripts/reject.py dream/staged/<file> "reason"\n',
        "python3 brain/scripts/reopen.py dream/staged/<file>\n",
        "```\n",
    ]
    report.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    STAGED.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    items = _load_queue()
    print(f"dream: {len(items)} items in queue")

    staged, rejected, skipped = [], 0, 0
    for item in items:
        if _already_staged(item):
            skipped += 1
            continue
        ok, reason = _heuristic_check(item)
        if not ok:
            _move_to_rejected(item, reason)
            rejected += 1
            continue
        out = _draft_staged(item)
        staged.append(out)
        print(f"  staged: {out.name}")

    _write_review_queue(staged, rejected, skipped)

    log_entry = {
        "ts": datetime.now().isoformat(),
        "queued": len(items),
        "staged": len(staged),
        "rejected": rejected,
        "skipped": skipped,
    }
    log_path = LOGS / f"dream-{datetime.now().strftime('%Y%m%d')}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    print(f"dream: staged={len(staged)} rejected={rejected} skipped={skipped}")
    print("Review: memory/working/REVIEW_QUEUE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
