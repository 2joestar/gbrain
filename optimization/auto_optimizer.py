"""
Auto-optimizer — Run 6 / P-RUN6-6.

Closed-loop background pass that scans recent telemetry and proposes
adjustments. Strictly opt-in: gated behind `system.auto_optimizer_enabled`
in Apple system.yaml (default False). When the flag is off, the public
``run_once()`` returns a dry-run report and never mutates state. The
``start_loop()`` background thread refuses to start unless the flag is on
at startup time.

What one pass does (when enabled):
  1. Read the performance_memory ring buffer + persistent health DB.
  2. Identify regression candidates (recent latency jump, fail-rate spike).
  3. For each candidate model:
       - extend its cooldown if it isn't already cooling down (gentle bump,
         not a hard block — preserves existing health.record_failure semantics);
       - notify circuit_breaker.note_failure (no-op when its own flag is off);
       - log a structured "auto_optimizer_action" event.
  4. Trigger a strategy_learner.promote() pass (it will only write when its
     own flag is on — this preserves the per-feature gating discipline).

Cadence: ~5 min between passes (configurable via OPTIMIZE_INTERVAL). Each
pass holds no global locks beyond the underlying module locks.

The loop is purposely conservative: every action it takes is one the system
already does on its own (cooldown bumps, strategy promotions). The
optimizer's job is just to shorten the feedback latency.
"""

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tuning ──
OPTIMIZE_INTERVAL = 300  # seconds between passes when looping
COOLDOWN_BUMP_BASE = 60  # seconds added to a regression candidate's cooldown

# ── Lazy module loaders so we stay importable in any environment ──


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
                return bool(cfg.get("system", {}).get("auto_optimizer_enabled", False))
    except Exception:
        pass
    return False


def _load_health():
    here = Path("/mnt/c/Users/PhoenixGlobal/brain/skills/provider-health/v1")
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import health as _h

        return _h
    except Exception as e:
        logger.debug("auto_optimizer: health unavailable: %s", e)
        return None


def _load_circuit_breaker():
    here = Path("/mnt/c/Users/PhoenixGlobal/brain/skills/provider-health/v1")
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import circuit_breaker as _cb

        return _cb
    except Exception:
        return None


def _load_perf_memory():
    here = Path("/mnt/c/Users/PhoenixGlobal/brain/optimization")
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import performance_memory as _pm

        return _pm
    except Exception:
        return None


def _load_strategy_learner():
    here = Path("/mnt/c/Users/PhoenixGlobal/brain/learning")
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import strategy_learner as _sl

        return _sl
    except Exception:
        return None


# ── Single pass ──


def run_once(*, dry_run: Optional[bool] = None) -> dict:
    """
    One optimization pass. ``dry_run=None`` means "use the flag" — the optimizer
    runs in dry-run mode whenever the flag is off, so it is safe to call
    from health checks or test harnesses.

    Returns a structured report describing what was looked at and what
    actions (if any) were applied.
    """
    flag = _flag_enabled()
    if dry_run is None:
        dry_run = not flag

    pm = _load_perf_memory()
    h = _load_health()
    cb = _load_circuit_breaker()
    sl = _load_strategy_learner()

    report = {
        "ts": time.time(),
        "flag_enabled": flag,
        "dry_run": dry_run,
        "candidates": [],
        "actions": [],
        "strategy_promote": None,
    }

    candidates = []
    if pm is not None:
        try:
            candidates = pm.regression_candidates()
        except Exception as e:
            logger.warning("auto_optimizer: regression scan failed: %s", e)
    report["candidates"] = candidates

    if h is not None and candidates and not dry_run:
        try:
            data = h._load()
            for c in candidates:
                section = c["section"]
                key = c["key"]
                entry = h._ensure_entry(data, section, key)
                # Gentle cooldown bump only if the model isn't already cooling
                # down. This is identical in shape to record_failure but does
                # not increment the failure counter (we don't want runaway
                # cooldowns).
                cur = entry.get("cooldown_until")
                now = time.time()
                if cur is None or cur < now:
                    entry["cooldown_until"] = now + COOLDOWN_BUMP_BASE
                    report["actions"].append(
                        {
                            "key": key,
                            "section": section,
                            "action": "cooldown_bump",
                            "until": entry["cooldown_until"],
                            "reasons": c.get("reasons", []),
                        }
                    )
                if cb is not None:
                    cb.note_failure(key, section)
            h._save(data)
        except Exception as e:
            logger.warning("auto_optimizer: action apply failed: %s", e)

    if sl is not None:
        try:
            report["strategy_promote"] = sl.promote(dry_run=dry_run)
        except Exception as e:
            logger.warning("auto_optimizer: strategy_promote failed: %s", e)
            report["strategy_promote"] = {"error": str(e)}

    return report


# ── Background loop ──

_loop_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def start_loop(interval: int = OPTIMIZE_INTERVAL) -> bool:
    """
    Start the background thread. Returns True if the thread was started,
    False if the flag is off (or it was already running).
    """
    global _loop_thread
    if not _flag_enabled():
        logger.info("auto_optimizer: flag is off — loop not started")
        return False
    if _loop_thread is not None and _loop_thread.is_alive():
        return False

    _stop_event.clear()

    def _run():
        logger.info("auto_optimizer: background loop started (interval=%ds)", interval)
        while not _stop_event.is_set():
            try:
                report = run_once()
                if report.get("actions") or (report.get("strategy_promote") or {}).get(
                    "wrote"
                ):
                    logger.info(
                        "auto_optimizer: pass applied %d actions, strategy.wrote=%s",
                        len(report.get("actions") or []),
                        (report.get("strategy_promote") or {}).get("wrote"),
                    )
            except Exception as e:
                logger.warning("auto_optimizer: pass failed: %s", e)
            _stop_event.wait(timeout=interval)

    _loop_thread = threading.Thread(target=_run, name="auto-optimizer", daemon=True)
    _loop_thread.start()
    return True


def stop_loop(timeout: float = 5.0):
    """Signal the loop to stop (best-effort, daemon thread)."""
    _stop_event.set()
    if _loop_thread is not None:
        _loop_thread.join(timeout=timeout)
