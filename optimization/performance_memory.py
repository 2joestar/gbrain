"""
Performance memory — Run 6 / P-RUN6-7.

A bounded in-process ring buffer of recent routing decisions and their outcomes.
Used by the auto_optimizer to detect regressions (e.g. a model that used to
clear in 1s now averaging 5s, or a chain whose tail is being hit unusually
often) without re-querying the SQLite store on every hot path.

Persistence: NONE on purpose. The shared health DB owns durable state; this
buffer is a fast-look window. On restart it starts empty and refills as new
decisions land.

Sizing: bounded by `system.performance_memory_size` in Apple system.yaml
(default 500). Capped at 5000 to keep memory bounded even if misconfigured.

Thread-safety: a single lock guards the deque. Append + iterate cost is O(1)
and O(N) respectively; both are negligible at our call rates.
"""

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

DEFAULT_SIZE = 500
HARD_CAP = 5000


def _read_size_from_yaml() -> int:
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
                size = int(
                    cfg.get("system", {}).get("performance_memory_size", DEFAULT_SIZE)
                )
                return max(10, min(size, HARD_CAP))
    except Exception:
        pass
    return DEFAULT_SIZE


_SIZE = _read_size_from_yaml()
_buf: Deque[dict] = deque(maxlen=_SIZE)
_lock = threading.Lock()


def record(
    *,
    key: str,
    section: str = "models",
    success: bool,
    latency: Optional[float] = None,
    chain: Optional[str] = None,
    intent: Optional[str] = None,
    extra: Optional[dict] = None,
):
    """Append a single decision/outcome record."""
    item = {
        "ts": time.time(),
        "key": key,
        "section": section,
        "success": bool(success),
        "latency": float(latency) if latency is not None else None,
        "chain": chain,
        "intent": intent,
    }
    if extra:
        item["extra"] = extra
    with _lock:
        _buf.append(item)


def recent(n: Optional[int] = None) -> List[dict]:
    """Return the most recent N records (default = all)."""
    with _lock:
        if n is None or n >= len(_buf):
            return list(_buf)
        return list(_buf)[-n:]


def by_key(key: str, section: str = "models", n: Optional[int] = None) -> List[dict]:
    """Records for a single key (most-recent-first up to n)."""
    out: List[dict] = []
    with _lock:
        for item in reversed(_buf):
            if item["key"] == key and item["section"] == section:
                out.append(item)
                if n is not None and len(out) >= n:
                    break
    return out


def stats_for(key: str, section: str = "models", window: int = 50) -> dict:
    """Compute success-rate and latency for the last `window` records of a key."""
    rows = by_key(key, section, n=window)
    if not rows:
        return {"count": 0, "success_rate": None, "avg_latency": None}
    succ = sum(1 for r in rows if r["success"])
    lats = [r["latency"] for r in rows if r["latency"] is not None]
    return {
        "count": len(rows),
        "success_rate": succ / len(rows),
        "avg_latency": (sum(lats) / len(lats)) if lats else None,
    }


def regression_candidates(
    min_count: int = 10, lat_jump: float = 2.0, fail_rate: float = 0.4
) -> List[dict]:
    """
    Lightweight regression detector — used by the auto_optimizer.
    Returns keys whose recent latency jumped >= ``lat_jump`` x vs the global
    average for that key, OR whose recent failure rate >= ``fail_rate``.
    """
    by: Dict[tuple, List[dict]] = {}
    with _lock:
        for item in _buf:
            by.setdefault((item["section"], item["key"]), []).append(item)

    out: List[dict] = []
    for (section, key), rows in by.items():
        if len(rows) < min_count:
            continue
        lats = [r["latency"] for r in rows if r["latency"] is not None]
        if not lats:
            continue
        recent_n = min(10, len(lats))
        recent_lat = sum(lats[-recent_n:]) / recent_n
        global_lat = sum(lats) / len(lats)
        succ = sum(1 for r in rows if r["success"]) / len(rows)
        fail_pct = 1.0 - succ
        flagged = False
        reason = []
        if global_lat > 0 and recent_lat / global_lat >= lat_jump:
            flagged = True
            reason.append(f"latency x{recent_lat / global_lat:.1f}")
        if fail_pct >= fail_rate:
            flagged = True
            reason.append(f"fail_rate {fail_pct:.0%}")
        if flagged:
            out.append(
                {
                    "key": key,
                    "section": section,
                    "count": len(rows),
                    "recent_latency": recent_lat,
                    "global_latency": global_lat,
                    "fail_rate": fail_pct,
                    "reasons": reason,
                }
            )
    return out


def snapshot() -> dict:
    with _lock:
        return {
            "size": len(_buf),
            "max": _buf.maxlen,
            "oldest_ts": _buf[0]["ts"] if _buf else None,
            "newest_ts": _buf[-1]["ts"] if _buf else None,
        }


def clear():
    """For tests."""
    with _lock:
        _buf.clear()
