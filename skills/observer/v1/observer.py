import asyncio
import hashlib
import json
import time
import os
from collections import deque
from datetime import datetime, timezone
import logging as _logging


def _log_fb(e, d=None):
    _logging.getLogger("observer").debug("%s %s", e, d or {})


def _snap_fb(*a, **kw):
    pass


def _load_cfg_fb(s):
    return {}


try:
    from ..utils.logger import log
    from ..utils.snapshot import take_snapshot
    from ..registry.manager import load as load_cfg
except ImportError:
    log = _log_fb
    take_snapshot = _snap_fb
    load_cfg = _load_cfg_fb

VAULT_PATH = os.getenv("VAULT_PATH", os.path.expanduser("~/.apple_memory"))
PHOENIX_ROOT = os.getenv("PHOENIX_ROOT", "/mnt/c/Users/PhoenixGlobal")
STRATEGY_QUEUE = os.path.join(PHOENIX_ROOT, "dream", "strategies", "queue")
MEMORY_TUNE_QUEUE = os.path.join(PHOENIX_ROOT, "dream", "memory-tuning", "queue")


class Observer:
    """Passive system monitor. Detects issues, queues tasks for Gatekeeper.
    Does NOT fix anything directly. All actions go through Gatekeeper.
    """

    def __init__(self, gatekeeper=None, orchestrator=None, **_deprecated):
        # Back-compat: hermes= alias for gatekeeper= (removed in Run 5)
        if gatekeeper is None and "hermes" in _deprecated:
            gatekeeper = _deprecated["hermes"]
        self.gatekeeper = gatekeeper
        self.orchestrator = orchestrator
        self._queue: asyncio.Queue = None
        self._running = False
        self._issue_log: deque = deque(maxlen=200)

    async def start(self):
        self._queue = asyncio.Queue(maxsize=100)
        self._running = True
        asyncio.create_task(self._monitor_loop(), name="observer_monitor")
        asyncio.create_task(self._task_worker(), name="observer_worker")
        log("observer_started", {})

    async def stop(self):
        self._running = False
        log("observer_stopped", {})

    async def _monitor_loop(self):
        cfg = load_cfg("system").get("observer", {})
        interval = cfg.get("check_interval", 60)
        snap_interval = (
            load_cfg("system").get("vault", {}).get("snapshot_interval", 3600)
        )
        last_snap = 0

        while self._running:
            await asyncio.sleep(interval)
            try:
                await self._check_gatekeeper_health()
                await self._check_disk()
                await self._check_provider_health()
                # Periodic snapshot
                if time.time() - last_snap > snap_interval:
                    asyncio.create_task(self._do_snapshot())
                    last_snap = time.time()
            except Exception as e:
                log("observer_check_error", {"error": str(e)})

    async def _check_gatekeeper_health(self):
        stats = self.gatekeeper.stats()
        if stats.get("blocked", 0) > 50:
            await self._queue_issue("gatekeeper_high_block_rate", stats)

    async def _check_disk(self):
        try:
            import shutil

            usage = shutil.disk_usage(VAULT_PATH)
            pct = usage.used / usage.total
            if pct > 0.85:
                await self._queue_issue("vault_disk_high", {"pct": round(pct * 100, 1)})
        except Exception:
            pass

    async def _do_snapshot(self):
        cfg = load_cfg("system").get("vault", {})
        take_snapshot(VAULT_PATH, max_age_days=cfg.get("max_snapshot_age", 7))

    async def _queue_issue(self, issue_type: str, details: dict):
        action = {"type": "system", "issue": issue_type, "details": details}
        if self.gatekeeper.validate(action):
            try:
                self._queue.put_nowait(action)
                log("observer_issue_queued", {"type": issue_type})
            except asyncio.QueueFull:
                pass
        self._issue_log.append(
            {"ts": time.time(), "type": issue_type, "details": details}
        )

    async def _task_worker(self):
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=5)
                log("observer_task_executing", {"task": task.get("issue")})
                await self._heal(task)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log("observer_worker_error", {"error": str(e)})

    async def _heal(self, task: dict):
        """Dispatch healing actions. All mutations go through Gatekeeper."""
        issue = task.get("issue")
        details = task.get("details", {})

        if issue == "vault_disk_high":
            pct = details.get("pct", 0)
            # Healing: prune snapshots older than 3 days to recover space.
            try:
                asyncio.create_task(asyncio.to_thread(take_snapshot, VAULT_PATH, 3))
                log("observer_healing", {"action": "snapshot_prune", "max_age_days": 3})
            except Exception as e:
                log(
                    "observer_heal_failed",
                    {"action": "snapshot_prune", "error": str(e)},
                )
            await self._notify(
                f"[Observer] Vault disk at {pct}% — pruning snapshots older than 3 days."
            )

        elif issue in (
            "gatekeeper_high_block_rate",
            "hermes_high_block_rate",
        ):  # hermes_ alias: Run 4 back-compat
            blocked = details.get("blocked", 0)
            # Healing: clear the loop-detection window so blocked actions can retry.
            try:
                self.gatekeeper._recent.clear()
                log(
                    "observer_healing",
                    {"action": "gatekeeper_loop_window_cleared", "blocked": blocked},
                )
            except Exception as e:
                log(
                    "observer_heal_failed",
                    {"action": "gatekeeper_clear", "error": str(e)},
                )
            await self._notify(
                f"[Observer] Gatekeeper blocked {blocked} actions — loop detection window reset."
            )

        elif issue == "provider_chain_degraded":
            chain = details.get("chain", "unknown")
            failures = details.get("failures", {})
            # Healing: reset cooldown timers so degraded models are retried sooner.
            router = getattr(getattr(self, "orchestrator", None), "router", None)
            if router is not None:
                for model in failures:
                    try:
                        router._fail_times.pop(model, None)
                    except Exception:
                        pass
                log(
                    "observer_healing",
                    {
                        "action": "provider_cooldown_reset",
                        "models": list(failures.keys()),
                    },
                )
            log("observer_healing", {"action": "provider_alert", "chain": chain})
            await self._notify(
                f"[Observer] Chain '{chain}' degraded — cooldown reset for {list(failures.keys())}; will retry next request."
            )

        else:
            log("observer_unhandled_issue", {"issue": issue})

    def emit_strategy_tuple(
        self,
        task_signature: str,
        skill_path: str,
        model: str,
        outcome: str,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        notes: str = "",
    ) -> None:
        """Write a strategy execution record to dream/strategies/queue/.

        Gated by observer.strategy_lane_enabled (default: True).
        When disabled, this is a silent no-op.
        """
        cfg = load_cfg("system").get("observer", {})
        if not cfg.get("strategy_lane_enabled", True):
            return

        ts = datetime.now(timezone.utc).isoformat()
        sig_hash = hashlib.sha1(task_signature.encode()).hexdigest()[:8]
        ts_slug = ts.replace(":", "").replace(".", "")[:17]
        filename = f"{ts_slug}-{sig_hash}.json"

        record = {
            "ts": ts,
            "task_signature": task_signature,
            "skill_path": skill_path,
            "model": model,
            "outcome": outcome,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "notes": notes,
        }

        os.makedirs(STRATEGY_QUEUE, exist_ok=True)
        path = os.path.join(STRATEGY_QUEUE, filename)
        try:
            with open(path, "w") as f:
                json.dump(record, f)
            log(
                "strategy_tuple_emitted",
                {"signature": task_signature, "outcome": outcome},
            )
        except Exception as e:
            log("strategy_tuple_write_failed", {"error": str(e)})

    def emit_retrieval(
        self,
        query_hash: str,
        edges_traversed: list,
        success: bool | None = None,
    ) -> None:
        """Write a retrieval observation to dream/memory-tuning/queue/.

        Gated by feature flag memory_tuning.enabled in protocols/feature-flags.yml.
        When disabled, this is a silent no-op.
        """
        try:
            import yaml
            from pathlib import Path as _Path

            flags_file = _Path(PHOENIX_ROOT) / "protocols" / "feature-flags.yml"
            flags = (
                yaml.safe_load(flags_file.read_text()) if flags_file.exists() else {}
            )
            if not flags.get("memory_tuning", {}).get("enabled", False):
                return
        except Exception:
            return

        ts = datetime.now(timezone.utc).isoformat()
        h = hashlib.sha1(query_hash.encode()).hexdigest()[:8]
        ts_slug = ts.replace(":", "").replace(".", "")[:17]
        filename = f"{ts_slug}-{h}.json"

        record = {
            "ts": ts,
            "query_hash": query_hash,
            "edges_traversed": edges_traversed,
            "success": success,
        }

        os.makedirs(MEMORY_TUNE_QUEUE, exist_ok=True)
        path = os.path.join(MEMORY_TUNE_QUEUE, filename)
        try:
            with open(path, "w") as f:
                json.dump(record, f)
            log(
                "retrieval_tuple_emitted",
                {"query_hash": query_hash, "success": success},
            )
        except Exception as e:
            log("retrieval_tuple_write_failed", {"error": str(e)})

    async def _notify(self, message: str):
        """Push a system notification through the orchestrator if available."""
        if self.orchestrator and hasattr(self.orchestrator, "handle"):
            try:
                await asyncio.wait_for(
                    self.orchestrator.handle(message, user_id=None, is_admin=True),
                    timeout=10,
                )
            except Exception as e:
                log("observer_notify_failed", {"error": str(e)[:120]})

    async def _check_provider_health(self):
        """Check ModelRouter failure counts and queue alert if a chain is degraded."""
        if not self.orchestrator:
            return
        router = getattr(self.orchestrator, "router", None)
        if not router or not hasattr(router, "health_report"):
            return
        report = router.health_report()
        failures = report.get("failures", {})
        if failures:
            chains = load_cfg("models").get("chains", {})
            for chain_name, models in chains.items():
                degraded = {m: failures[m] for m in models if m in failures}
                # Alert if the primary model is failing (≥3 hits) or majority degraded
                primary_failing = models and failures.get(models[0], 0) >= 3
                majority_failing = len(degraded) >= max(1, len(models) // 2)
                if primary_failing or majority_failing:
                    await self._queue_issue(
                        "provider_chain_degraded",
                        {"chain": chain_name, "failures": degraded},
                    )
