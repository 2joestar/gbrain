#!/usr/bin/env python3
"""model-discovery: discover, score, and propose model chain updates."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

PHOENIX_ROOT = Path("/mnt/c/Users/PhoenixGlobal")
MODELS_MANIFEST = PHOENIX_ROOT / "manifests" / "models.yml"
INTENT_PROTOCOL = PHOENIX_ROOT / "protocols" / "model-intent.yml"
DREAM_QUEUE = PHOENIX_ROOT / "dream" / "queue"

INTENTS = [
    "quick-chat",
    "deep-reasoning",
    "code-gen",
    "vision",
    "audio",
    "embed",
    "tool-use",
    "long-context",
    "cheap-bulk",
]

# Provider → (list-models endpoint, auth-header-env-var)
PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1/models", "CEREBRAS_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1/models", "GROQ_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1/models", "MISTRAL_API_KEY"),
    "anthropic": (None, "ANTHROPIC_API_KEY"),  # no list endpoint, static
}


def ollama_list() -> list:
    """Return installed Ollama models as dicts."""
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        models = []
        for line in result.stdout.strip().splitlines()[1:]:  # skip header
            parts = line.split()
            if parts:
                models.append(
                    {
                        "name": parts[0],
                        "provider": "ollama",
                        "modality": ["text"],
                        "free_tier": True,
                        "path": "D:/PhoenixGlobal/models/ollama/",
                    }
                )
        return models
    except Exception:
        return []


def discover_provider(name: str, endpoint: str, env_var: str) -> list:
    """Fetch model list from provider API."""
    import os
    import urllib.request

    key = os.environ.get(env_var, "")
    if not key or endpoint is None:
        return []
    try:
        req = urllib.request.Request(
            endpoint,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": "PhoenixGlobal/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        raw = data.get("data", data) if isinstance(data, dict) else data
        return [
            {
                "name": m.get("id", m.get("name", "")),
                "provider": name,
                "context_window": m.get("context_length", m.get("context_window", 0)),
                "modality": ["text"],
                "free_tier": m.get("pricing", {}).get("prompt", "1") in ("0", "0.0", 0),
            }
            for m in (raw if isinstance(raw, list) else [])
        ]
    except Exception:
        return []


def write_proposal(new_models: list) -> Path:
    """Write model-proposal to dream/queue/."""
    DREAM_QUEUE.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = DREAM_QUEUE / f"model-proposal-{today}.md"
    lines = [
        f"# Model Discovery Proposal — {today}\n",
        "## Newly discovered free/preview models\n",
    ]
    for m in new_models[:20]:
        ctx = m.get("context_window", "?")
        lines.append(f"- **{m['name']}** ({m['provider']}) context:{ctx}\n")
    lines += [
        "\n## Recommended chain updates\n",
        "Review and graduate via `python3 brain/scripts/graduate.py` or reject.\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")
    return path


def main(argv: list = None) -> int:
    args = argv or sys.argv[1:]
    cmd = args[0] if args else "refresh"

    if cmd == "list":
        ollama = ollama_list()
        print(f"Ollama local: {len(ollama)} models")
        for m in ollama:
            print(f"  {m['name']}")
        return 0

    if cmd == "intents":
        if INTENT_PROTOCOL.exists():
            print(INTENT_PROTOCOL.read_text())
        else:
            print("Intent mapping not yet created — run Phase 3b.1 fully.")
        return 0

    if cmd in ("for", "intent") and len(args) > 1:
        intent = args[1].strip("\"'")
        if INTENT_PROTOCOL.exists():
            content = INTENT_PROTOCOL.read_text()
            if intent in content:
                for line in content.splitlines():
                    if intent in line:
                        print(line)
            else:
                print(f"Intent '{intent}' not found in model-intent.yml")
        else:
            print("protocols/model-intent.yml not yet created.")
        return 0

    if cmd == "refresh":
        print("Discovering models...")
        all_models = ollama_list()
        for name, (endpoint, env_var) in PROVIDERS.items():
            found = discover_provider(name, endpoint, env_var)
            print(f"  {name}: {len(found)} models")
            all_models.extend(found)

        free = [m for m in all_models if m.get("free_tier", False)]
        print(f"Total: {len(all_models)} models, {len(free)} free/preview")

        if free:
            path = write_proposal(free)
            print(f"Proposal written to {path}")
        return 0

    print(f'Unknown command: {cmd}. Use: refresh | list | for "<intent>" | intents')
    return 1


if __name__ == "__main__":
    sys.exit(main())
