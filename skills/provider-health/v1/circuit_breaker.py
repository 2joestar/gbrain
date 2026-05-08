"""
Circuit breaker — Run 6 / P-RUN6-3.

Sits on top of the existing two-tier health module. Flag-gated behind
`system.circuit_breaker_enabled` (default False), so importing this module is
always safe — every public function returns the no-op answer when the flag is
off.

Three states:
  - closed:    normal traffic flowing.
  - open:      excluded for `OPEN_DURATION` seconds; ``is_open()`` returns True.
  - half-open: probe window after the cooldown elapses. The first call that
               records a success transitions back to closed; the first failure
               sends it back to open with a longer duration.

Decisions are derived from the same per-key health row that adaptive.py
already maintains, so there is no separate persistence path. State is
ephemeral (in-process) — restarts naturally fall back to plain
`health.is_healthy`.

Public API:
    is_open(key, section)            → bool
    should_route(key, section)       → bool   (inverse, for routers)
    note_success(key, section)
    note_failure(key, section)
    state_of(key, section)           → "closed"|"open"|"half_open"
    snapshot()                       → dict   (debug / live_state)

Decision policy (only consulted when flag is on):
    Tripping condition:
        (call_count >= MIN_CALLS) AND
        (fail / call_count >= FAIL_RATIO) AND
        (recent_failures_in_window >= STREAK)
"""

import os
import sys
import time
import threading
from pathlib import Path
from typing import Dict, Tuple

# ── Tuning knobs ──
MIN_CALLS = 6  # don't trip on tiny samples
FAIL_RATIO = 0.5  # >= 50% failure rate triggers the rate test
STREAK = 3  # plus 3 consecutive failures observed via note_failure
OPEN_DURATION_BASE = 90  # seconds; doubled (capped) on each re-open
OPEN_DURATION_MAX = 1800  # 30 min ceiling

# ── In-process state ──
_lock = threading.Lock()
_streak: Dict[Tuple[str, str], int] = {}  # (section, key) → consecutive failures
_open_until: Dict[Tuple[str, str], float] = {}
_open_count: Dict[
    Tuple[str, str], int
] = {}  # how many times we've opened — for backoff


def _flag_enabled() -> bool:
    """Read `system.circuit_breaker_enabled` from Apple system.yaml."""
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
                return bool(cfg.get("system", {}).get("circuit_breaker_enabled", False))
    except Exception:
        pass
    return False


def _get_health():
    """Lazy-import the colocated health module."""
    here = Path(__file__).parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import health as _h

        return _h
    except Exception:
        return None


def _trip_test(h, key: str, section: str) -> bool:
    """Decide whether this key should be opened based on persistent telemetry."""
    try:
        data = h._load()
    except Exception:
        return False
    entry = data.get(section, {}).get(key)
    if not entry:
        return False
    call_count = int(entry.get("call_count", 0) or 0)
    fail = int(entry.get("fail", 0) or 0)
    if call_count < MIN_CALLS:
        return False
    if fail / max(call_count, 1) < FAIL_RATIO:
        return False
    return True


def _open(key: str, section: str):
    sk = (section, key)
    n = _open_count.get(sk, 0) + 1
    _open_count[sk] = n
    duration = min(OPEN_DURATION_BASE * (2 ** (n - 1)), OPEN_DURATION_MAX)
    _open_until[sk] = time.time() + duration
    _streak[sk] = 0


def state_of(key: str, section: str = "models") -> str:
    if not _flag_enabled():
        return "closed"
    sk = (section, key)
    with _lock:
        until = _open_until.get(sk)
        if until is None:
            return "closed"
        if time.time() >= until:
            return "half_open"
        return "open"


def is_open(key: str, section: str = "models") -> bool:
    return state_of(key, section) == "open"


def should_route(key: str, section: str = "models") -> bool:
    """Inverse of is_open — convenience for routers."""
    return state_of(key, section) != "open"


def note_success(key: str, section: str = "models"):
    if not _flag_enabled():
        return
    sk = (section, key)
    with _lock:
        _streak[sk] = 0
        if sk in _open_until and time.time() >= _open_until[sk]:
            # half-open probe succeeded → close.
            _open_until.pop(sk, None)
            _open_count[sk] = 0


def note_failure(key: str, section: str = "models"):
    if not _flag_enabled():
        return
    sk = (section, key)
    with _lock:
        _streak[sk] = _streak.get(sk, 0) + 1
        if sk in _open_until and time.time() >= _open_until[sk]:
            # half-open probe failed → re-open with backoff.
            _open(key, section)
            return
        if _streak[sk] >= STREAK:
            h = _get_health()
            if h is None:
                return
            if _trip_test(h, key, section):
                _open(key, section)


def snapshot() -> dict:
    """Debug introspection — returned to live_state when flag is on."""
    now = time.time()
    out = {"enabled": _flag_enabled(), "open": [], "half_open": [], "streaks": {}}
    if not out["enabled"]:
        return out
    with _lock:
        for (section, key), until in list(_open_until.items()):
            bucket = "half_open" if now >= until else "open"
            out[bucket].append(
                {
                    "key": key,
                    "section": section,
                    "until": until,
                    "remaining": max(0, until - now),
                }
            )
        out["streaks"] = {
            f"{section}:{key}": n for (section, key), n in _streak.items() if n > 0
        }
    return out
