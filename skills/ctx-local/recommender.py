#!/usr/bin/env python3
"""ctx-local: context-aware skill & agent recommender for PhoenixGlobal."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
SKILLS_MANIFEST = PHOENIX_ROOT / "manifests" / "skills.yml"
AGENTS_MANIFEST = PHOENIX_ROOT / "manifests" / "agents.yml"
WORKING_DIR = PHOENIX_ROOT / "memory" / "working"
BUDGET_LIMIT = int(os.environ.get("PHOENIX_MAX_SKILLS", "15"))
BUDGET_WARN = 10


def load_yaml_simple(path: Path) -> list:
    if not path.exists():
        return []
    items = []
    current = None
    list_field = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            list_field = None
            continue
        if line.startswith("- name:"):
            if current:
                items.append(current)
            current = {
                "name": line.split(":", 1)[1].strip(),
                "triggers": [],
                "tags": [],
            }
            list_field = None
        elif current is None:
            continue
        elif ":" in line and not line.strip().startswith("-"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            list_field = None
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                current[key] = [
                    t.strip().strip("'\"") for t in inner.split(",") if t.strip()
                ]
            elif val:
                current[key] = val
            else:
                list_field = key
                current[key] = []
        elif line.strip().startswith("- ") and list_field:
            current[list_field].append(line.strip()[2:].strip().strip("'\""))

    if current:
        items.append(current)
    return items


def tokenize(text):
    return set(re.findall(r"\w+", text.lower()))


def score_item(item, context_tokens, file_tokens):
    score = 0
    status = item.get("status", "")
    if status in ("absorbed", "active"):
        score += 2
    elif status in ("seeded", "authored"):
        score += 1
    elif status in ("stub", "blocked", "removed"):
        return 0

    for t in item.get("triggers", []):
        t_low = t.lower().lstrip("/")
        if t_low in context_tokens or t_low in file_tokens:
            score += 3

    for tag in item.get("tags", []):
        if tag.lower() in context_tokens or tag.lower() in file_tokens:
            score += 1

    name_tokens = tokenize(item.get("name", ""))
    score += len(name_tokens & context_tokens)
    return score


def _reason(item, ctx, files):
    hits = []
    for t in item.get("triggers", []):
        if t.lower().lstrip("/") in ctx | files:
            hits.append(f"trigger:{t}")
    for tag in item.get("tags", []):
        if tag.lower() in ctx | files:
            hits.append(f"tag:{tag}")
    return ", ".join(hits) if hits else "name-match"


def recommend(context, files="", top_k=10, manifest_type="both"):
    context_tokens = tokenize(context)
    file_tokens = tokenize(files)

    items = []
    if manifest_type in ("both", "skills"):
        for item in load_yaml_simple(SKILLS_MANIFEST):
            item["_type"] = "skill"
            items.append(item)
    if manifest_type in ("both", "agents"):
        for item in load_yaml_simple(AGENTS_MANIFEST):
            item["_type"] = "agent"
            items.append(item)

    scored = []
    for item in items:
        s = score_item(item, context_tokens, file_tokens)
        if s > 0:
            scored.append(
                {
                    "name": item.get("name", ""),
                    "type": item.get("_type", "skill"),
                    "score": s,
                    "triggers": item.get("triggers", []),
                    "tags": item.get("tags", []),
                    "reason": _reason(item, context_tokens, file_tokens),
                    "status": item.get("status", ""),
                }
            )

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_k]
    used = len(top)
    return {
        "recommended": top,
        "budget": {
            "used": used,
            "limit": BUDGET_LIMIT,
            "ok": used <= BUDGET_LIMIT,
            "warn": used > BUDGET_WARN,
        },
        "context_tokens": len(context_tokens),
    }


def session_start_mode(project):
    context_parts = ["session start"]
    if project:
        p = Path(project)
        context_parts.append(p.name)
        if p.exists():
            for f in list(p.iterdir())[:20]:
                context_parts.append(f.name)

    result = recommend(" ".join(context_parts), top_k=min(15, BUDGET_LIMIT))

    WORKING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WORKING_DIR / "ctx-recommendations.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if not result["budget"]["ok"]:
        print(
            f"ERROR: {result['budget']['used']} skills exceeds budget {BUDGET_LIMIT}",
            file=sys.stderr,
        )
        return 1

    print(
        f"ctx-local: {result['budget']['used']} skills recommended (budget: {BUDGET_LIMIT})"
    )
    for item in result["recommended"][:5]:
        print(f"  [{item['score']:2d}] {item['name']} ({item['reason']})")
    if result["budget"]["warn"]:
        print(
            f"WARNING: {result['budget']['used']} approaching budget limit {BUDGET_LIMIT}"
        )
    return 0


def budget_check_mode():
    out_path = WORKING_DIR / "ctx-recommendations.json"
    if not out_path.exists():
        print("ctx-local: no cache — run --session-start first")
        return 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    used = data.get("budget", {}).get("used", 0)
    ok = data.get("budget", {}).get("ok", True)
    if not ok:
        print(f"BUDGET EXCEEDED: {used} > {BUDGET_LIMIT}", file=sys.stderr)
        return 1
    print(f"Budget OK: {used}/{BUDGET_LIMIT}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="ctx-local: skill/agent recommender")
    parser.add_argument("--context", default="")
    parser.add_argument("--files", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--session-start", action="store_true")
    parser.add_argument("--budget-check", action="store_true")
    parser.add_argument("--project", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.budget_check:
        return budget_check_mode()
    if args.session_start:
        return session_start_mode(args.project or None)

    result = recommend(args.context, args.files, args.top_k)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Top {len(result['recommended'])} recommendations (budget {result['budget']['used']}/{BUDGET_LIMIT}):"
        )
        for item in result["recommended"]:
            print(
                f"  [{item['score']:2d}] {item['type']:6s} {item['name']:<30s}  {item['reason']}"
            )
        if not result["budget"]["ok"]:
            print("WARNING: over budget", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
