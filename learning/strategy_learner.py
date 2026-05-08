"""
Strategy learner — Run 6 / P-RUN6-5.

Closes the open loop in protocols/learned-strategies.yml: the planner already
reads `learned:` priors, but until now nothing wrote to that file. This module
is the WRITE path. It is flag-gated (`system.strategy_write_enabled`,
default False) and append-only — pinned entries are never overwritten and
duplicates are de-duplicated on (task_signature, model).

Observation pipeline:
    observe(task_signature, model, success, latency, [intent])
        → buffered in-memory
    promote()  (called periodically or at session end)
        → aggregates buffered tuples per (task_signature, model)
        → emits an entry only when n_trials >= MIN_TRIALS
                                  AND success_rate >= MIN_SUCCESS
        → never overwrites pinned[]
        → de-duplicates against existing learned[]
        → atomic write via tmp + rename

Schema written to learned-strategies.yml:
    learned:
      - task_signature: <str>
        model: <str>
        n_trials: <int>
        success_rate: <float 0-1>
        avg_latency: <float seconds>
        promoted_at: <ISO8601>
        intent: <optional str>
    pinned: [...]   # untouched
    version: 1

Promotion thresholds (per the Run-6 spec):
    MIN_TRIALS   = 5
    MIN_SUCCESS  = 0.8
"""

import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Tuning ──
MIN_TRIALS = 5
MIN_SUCCESS = 0.8

LEARNED_PATH = Path("/mnt/c/Users/PhoenixGlobal/protocols/learned-strategies.yml")

# ── In-memory observation buffer ──
_lock = threading.Lock()
_obs: List[dict] = []


def _flag_enabled() -> bool:
    try:
        candidates = [
            "/mnt/c/projects/Apple-Mamasethu/config/system.yaml",
            os.path.expanduser("~/Apple-Mamasethu/config/system.yaml"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                import yaml

                with open(c, "r") as f:
                    cfg = yaml.safe_load(f) or {}
                return bool(cfg.get("system", {}).get("strategy_write_enabled", False))
    except Exception:
        pass
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def observe(
    task_signature: str,
    model: str,
    success: bool,
    latency: Optional[float] = None,
    intent: Optional[str] = None,
):
    """Record one (signature, model, outcome) tuple. Cheap — pure append."""
    if not task_signature or not model:
        return
    with _lock:
        _obs.append(
            {
                "task_signature": str(task_signature),
                "model": str(model),
                "success": bool(success),
                "latency": float(latency) if latency is not None else None,
                "intent": intent,
                "ts": time.time(),
            }
        )


def buffer_size() -> int:
    with _lock:
        return len(_obs)


def _load_yaml() -> dict:
    try:
        import yaml

        if not LEARNED_PATH.exists():
            return {"learned": [], "pinned": [], "version": 1}
        with open(LEARNED_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("strategy_learner: failed to load %s: %s", LEARNED_PATH, e)
        return {"learned": [], "pinned": [], "version": 1}
    data.setdefault("learned", [])
    data.setdefault("pinned", [])
    data.setdefault("version", 1)
    if not isinstance(data["learned"], list):
        data["learned"] = []
    if not isinstance(data["pinned"], list):
        data["pinned"] = []
    return data


def _save_yaml_atomic(data: dict):
    import yaml

    LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED_PATH.with_suffix(LEARNED_PATH.suffix + ".tmp")
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, LEARNED_PATH)


def _aggregate(obs: List[dict]) -> Dict[Tuple[str, str], dict]:
    """Group by (task_signature, model) and compute trial stats."""
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for o in obs:
        groups[(o["task_signature"], o["model"])].append(o)
    out: Dict[Tuple[str, str], dict] = {}
    for k, rows in groups.items():
        n = len(rows)
        succ = sum(1 for r in rows if r["success"])
        lats = [r["latency"] for r in rows if r["latency"] is not None]
        intents = [r["intent"] for r in rows if r["intent"]]
        out[k] = {
            "task_signature": k[0],
            "model": k[1],
            "n_trials": n,
            "success_rate": succ / n,
            "avg_latency": (sum(lats) / len(lats)) if lats else 0.0,
            "intent": intents[0] if intents else None,
        }
    return out


def promote(dry_run: bool = False) -> dict:
    """
    Aggregate the observation buffer and append qualifying entries to
    learned-strategies.yml. Returns a report dict with what was promoted /
    skipped / blocked.

    Safe to call when the flag is off — it returns the same report shape
    with `wrote: false` and never touches the file.
    """
    flag = _flag_enabled()
    report = {
        "wrote": False,
        "flag_enabled": flag,
        "buffered": 0,
        "promoted": [],
        "skipped_thresholds": [],
        "skipped_dup": [],
        "blocked_pinned": [],
        "dry_run": dry_run,
    }

    with _lock:
        snapshot = list(_obs)
        report["buffered"] = len(snapshot)

    if not snapshot:
        return report

    aggregated = _aggregate(snapshot)
    yaml_data = _load_yaml()
    learned = yaml_data["learned"]
    pinned = yaml_data["pinned"]

    pinned_keys = {
        (e.get("task_signature"), e.get("model")) for e in pinned if isinstance(e, dict)
    }
    learned_keys = {
        (e.get("task_signature"), e.get("model"))
        for e in learned
        if isinstance(e, dict)
    }

    new_entries: List[dict] = []
    for k, agg in aggregated.items():
        if agg["n_trials"] < MIN_TRIALS or agg["success_rate"] < MIN_SUCCESS:
            report["skipped_thresholds"].append(
                {
                    "task_signature": agg["task_signature"],
                    "model": agg["model"],
                    "n_trials": agg["n_trials"],
                    "success_rate": agg["success_rate"],
                }
            )
            continue
        if k in pinned_keys:
            report["blocked_pinned"].append(
                {
                    "task_signature": agg["task_signature"],
                    "model": agg["model"],
                }
            )
            continue
        if k in learned_keys:
            report["skipped_dup"].append(
                {
                    "task_signature": agg["task_signature"],
                    "model": agg["model"],
                }
            )
            continue
        entry = {
            "task_signature": agg["task_signature"],
            "model": agg["model"],
            "n_trials": int(agg["n_trials"]),
            "success_rate": round(agg["success_rate"], 4),
            "avg_latency": round(agg["avg_latency"], 4),
            "promoted_at": _now_iso(),
        }
        if agg["intent"]:
            entry["intent"] = agg["intent"]
        new_entries.append(entry)
        report["promoted"].append(
            {
                "task_signature": entry["task_signature"],
                "model": entry["model"],
                "n_trials": entry["n_trials"],
                "success_rate": entry["success_rate"],
            }
        )

    if new_entries and flag and not dry_run:
        learned.extend(new_entries)
        yaml_data["learned"] = learned
        try:
            _save_yaml_atomic(yaml_data)
            report["wrote"] = True
            logger.info(
                "strategy_learner: promoted %d entries to learned-strategies.yml",
                len(new_entries),
            )
        except Exception as e:
            logger.warning("strategy_learner: write failed: %s", e)
            report["wrote"] = False

    if report["wrote"]:
        with _lock:
            _obs.clear()

    return report


def clear_buffer():
    """For tests / manual reset."""
    with _lock:
        _obs.clear()
