"""
Dynamic chain generation — augments Apple's hardcoded models.yaml chains
with live-discovered models from providers.yaml.

Every 30 minutes, fetches fresh model lists from providers.yaml's "auto" sources
and merges them into the chain structure. Hardcoded models.yaml entries always
take priority (manual overrides), but newly discovered models appear as fallbacks.

Used by: ModelRouter.get_chains() — called on every routing decision.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache config ──
CHAIN_CACHE = Path.home() / ".apple_memory" / ".dynamic_chains.json"
CACHE_TTL = 1800  # 30 minutes

# Model capability classification — keywords matched against tokens of the
# lowered model id. Tokens are produced by splitting on `-:_/.`, so:
#   - short keywords (≤3 chars: "r1", "o3", "o4", "8b") need an EXACT token match;
#     this prevents "r1" hitting "tr1al" or "8b" hitting "808b3".
#   - longer keywords match any token containing the keyword as a substring;
#     so "coder" hits "qwen3-coder" but "code" no longer hits "decoder".
#
# Why "deep" was removed from reasoning: it was substring-matching every
# DeepSeek model — including DeepSeek-V3.2 and -chat, which are generalists,
# not reasoners. Real DeepSeek reasoning models carry "r1" or "thinking",
# both of which remain explicit patterns. (B-NEW-5 / P-RUN3-4)
INTENT_PATTERNS = {
    "coding": ["coder", "codestral", "starcoder", "codex", "code"],
    "reasoning": [
        "reasoning",
        "reason",
        "thinking",
        "think",
        "qwq",
        "r1",
        "o3",
        "o4",
    ],
    "fast": ["flash", "instant", "haiku", "scout", "nemo", "lite", "mini", "8b"],
    "complex": [],  # fallback: models not matching other patterns
}


def _load_yaml(path: Path) -> dict:
    """Load YAML safely, return empty dict on failure."""
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _fetch_live_models(provider_id: str, base_url: str, key: str) -> list:
    """Fetch models from an OpenAI-compatible /models endpoint."""
    try:
        import urllib.request

        url = f"{base_url.rstrip('/')}/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        models = resp.get("data", [])
        return [m["id"] for m in models if m.get("id")]
    except Exception as e:
        logger.debug("fetch_models %s failed: %s", provider_id, e)
        return []


_TOKEN_SPLIT_RE = None


def _tokenize(model_ref: str) -> list:
    """Split a model ref into lowercased tokens on `-:_/.` separators."""
    global _TOKEN_SPLIT_RE
    if _TOKEN_SPLIT_RE is None:
        import re

        _TOKEN_SPLIT_RE = re.compile(r"[\/:\-_.]")
    return [t for t in _TOKEN_SPLIT_RE.split(model_ref.lower()) if t]


def _classify_model(model_ref: str) -> str:
    """Classify a model reference (e.g. 'groq:llama-3.3-70b') into an intent.

    Token-aware matching: short keywords (≤3 chars) require an exact token
    match; longer keywords match as substrings within a single token. This
    prevents "deep" (formerly in reasoning) from accidentally bucketing
    DeepSeek generalists as reasoners.
    """
    tokens = _tokenize(model_ref)
    for intent, keywords in INTENT_PATTERNS.items():
        if intent == "complex":
            continue
        for kw in keywords:
            # Exact-token match for keywords ≤4 chars, where substring matching
            # would cross-pollute (e.g. "mini" hitting "gemini", "nemo" hitting
            # "nemotron"). Longer keywords stay as substring-within-token.
            if len(kw) <= 4:
                if kw in tokens:
                    return intent
            else:
                if any(kw in t for t in tokens):
                    return intent
    return "complex"


def generate_dynamic_chains(providers_yaml_path: Optional[str] = None) -> dict:
    """
    Generate chains by reading providers.yaml and fetching live models.
    Returns: {chain_name: [model_ref, ...]} compatible with models.yaml schema.

    Priority: hardcoded models.yaml entries first, then dynamic fallbacks.
    """
    # Load providers.yaml
    if providers_yaml_path is None:
        candidates = [
            "/mnt/c/Users/PhoenixGlobal/config/providers.yaml",
            os.path.expanduser("~/PhoenixGlobal/config/providers.yaml"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                providers_yaml_path = c
                break

    if not providers_yaml_path:
        logger.debug("providers.yaml not found, using static chains only")
        return {}

    providers_config = _load_yaml(Path(providers_yaml_path))
    providers = providers_config.get("providers", [])

    # Load credentials
    creds = {}
    try:
        creds_path = Path.home() / ".apple_memory" / ".credentials.json"
        creds = json.loads(creds_path.read_text())
    except Exception:
        pass

    # Build live model list per provider
    dynamic_models = {"coding": [], "fast": [], "reasoning": [], "complex": []}

    for p in providers:
        pid = p.get("id", "")
        base_url = p.get("base_url")

        # Skip Ollama (handled separately) and anthropic (uses SDK, not OpenAI compat)
        if pid in ("ollama", "anthropic"):
            continue

        models_config = p.get("models", [])
        if models_config == "auto" and base_url:
            # Resolve key
            cred_path = p.get("cred_path")
            keys = []
            if cred_path:
                val = creds
                for part in cred_path.split("."):
                    val = val.get(part, {}) if isinstance(val, dict) else None
                if isinstance(val, list):
                    keys = [k for k in val if isinstance(k, str) and k.strip()]
                elif isinstance(val, str) and val.strip():
                    keys = [val.strip()]

            if keys:
                live = _fetch_live_models(pid, base_url, keys[0])
                # Prepend provider prefix
                for model_id in live:
                    ref = f"{pid}:{model_id}"
                    intent = _classify_model(ref)
                    if ref not in dynamic_models[intent]:
                        dynamic_models[intent].append(ref)
        elif isinstance(models_config, list) and base_url:
            # Hardcoded models — classify and add
            for m in models_config:
                mid = m.get("id", "")
                if mid:
                    ref = f"{pid}:{mid}"
                    intent = _classify_model(ref)
                    if ref not in dynamic_models[intent]:
                        dynamic_models[intent].append(ref)

    return dynamic_models


def _load_health_module():
    """Lazy-import the provider-health module from the sibling skill dir."""
    try:
        import sys

        skill_dir = Path(__file__).parent.parent.parent
        health_dir = skill_dir / "provider-health" / "v1"
        if str(health_dir) not in sys.path:
            sys.path.insert(0, str(health_dir))
        import health as _h

        return _h
    except Exception:
        return None


def _sort_by_model_health(chains: dict) -> dict:
    """Sort each chain by per-model health score (descending).

    Run-5 / P-RUN5-2: introduced, used `health.get_score` (0-1 composite).
    Run-6 / P-RUN6-2: switched to the new latency-aware
    ``get_routing_score`` formula ``success_rate - latency_weight * avg_latency``
    when it is available. Falls back to ``get_score`` for older health modules
    so the sort still works mid-rollout.

    Reads the shared provider-health backing store (`models` section). Models
    with no recorded calls get the neutral 0.5 score so newly-introduced
    models don't get pinned to the bottom.

    Stable: when scores tie, preserves the existing order (so manual ordering
    in models.yaml is honored when telemetry is empty or uniform).
    """
    h = _load_health_module()
    if h is None:
        return chains

    score_fn = getattr(h, "get_routing_score", None) or h.get_score
    sorted_chains = {}
    for chain_name, models in chains.items():
        scored = [(i, m, score_fn(m, "models")) for i, m in enumerate(models)]
        scored.sort(key=lambda x: (-x[2], x[0]))
        sorted_chains[chain_name] = [m for _, m, _ in scored]
    return sorted_chains


def _is_health_sort_enabled() -> bool:
    """Check `chain_health_sort` flag in Apple's system.yaml. Default off."""
    try:
        candidates = [
            "/mnt/c/projects/Apple-Mamasethu/config/system.yaml",
            os.path.expanduser("~/Apple-Mamasethu/config/system.yaml"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                cfg = _load_yaml(Path(c))
                # Top-level system block → chain_health_sort flag.
                return bool(cfg.get("system", {}).get("chain_health_sort", False))
    except Exception:
        pass
    return False


def get_augmented_chains(models_yaml_path: Optional[str] = None) -> dict:
    """
    Merge hardcoded models.yaml chains with dynamic ones. Cached for 30 min.
    Returns the augmented chain dict used by ModelRouter.get_chains().
    """
    # Check cache
    if CHAIN_CACHE.exists():
        try:
            cached = json.loads(CHAIN_CACHE.read_text())
            age = time.time() - cached.get("ts", 0)
            if age < CACHE_TTL:
                chains = cached.get("chains", {})
                # Even with a cached chain set, re-sort on every call when the
                # health-sort flag is on — telemetry shifts faster than the
                # 30-min cache TTL, and re-sorting is O(N log N) of ~600 items.
                if _is_health_sort_enabled():
                    chains = _sort_by_model_health(chains)
                return chains
        except Exception:
            pass

    # Load static chains from models.yaml
    if models_yaml_path is None:
        candidates = [
            "/mnt/c/projects/Apple-Mamasethu/config/models.yaml",
            os.path.expanduser("~/Apple-Mamasethu/config/models.yaml"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                models_yaml_path = c
                break

    static = {}
    if models_yaml_path:
        static = _load_yaml(Path(models_yaml_path)).get("chains", {})

    # Generate dynamic
    dynamic = generate_dynamic_chains()

    # Merge: static entries first, then dynamic (deduplicated)
    merged = {}
    for chain in ["fast", "coding", "complex", "reasoning"]:
        seen = set()
        entries = []
        # Static first
        for m in static.get(chain, []):
            if m not in seen:
                entries.append(m)
                seen.add(m)
        # Dynamic fallback
        for m in dynamic.get(chain, []):
            if m not in seen:
                entries.append(m)
                seen.add(m)
        merged[chain] = entries

    # Write cache (pre-sort, so the cached form is the deterministic merge).
    try:
        CHAIN_CACHE.write_text(
            json.dumps({"ts": time.time(), "chains": merged}, indent=2)
        )
    except Exception:
        pass

    if _is_health_sort_enabled():
        merged = _sort_by_model_health(merged)
    return merged
