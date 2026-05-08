"""Safe autonomy supervision layer.

The manager observes health snapshots and returns bounded mitigation actions.
It never rewrites architecture, deletes files, or self-updates code.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from brain.optimization.performance_db import PerformanceDB
except Exception:  # pragma: no cover
    PerformanceDB = None  # type: ignore[assignment]


@dataclass(slots=True)
class AutonomyAction:
    action_type: str
    target: str
    reason: str
    safe: bool = True
    applied: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "reason": self.reason,
            "safe": self.safe,
            "applied": self.applied,
            "metadata": self.metadata,
        }


class AutonomyManager:
    """Coordinates observer/runtime/router/learner signals into safe actions."""

    FORBIDDEN_ACTIONS = {
        "modify_architecture",
        "rewrite_files",
        "delete_config",
        "self_update_code",
    }

    def __init__(
        self,
        *,
        enabled: bool = False,
        degraded_success_threshold: float = 0.5,
        queue_pressure_threshold: int = 50,
        max_actions_per_cycle: int = 5,
        action_cooldown_seconds: float = 60.0,
        performance_db: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.degraded_success_threshold = degraded_success_threshold
        self.queue_pressure_threshold = queue_pressure_threshold
        self.max_actions_per_cycle = max_actions_per_cycle
        self.action_cooldown_seconds = action_cooldown_seconds
        self.performance_db = performance_db
        self._last_action_at: dict[str, float] = {}

    async def monitor_once(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        actions = self.evaluate(snapshot)
        await self._record_actions(actions)
        return [action.as_dict() for action in actions]

    def evaluate(self, snapshot: dict[str, Any]) -> list[AutonomyAction]:
        if not self.enabled:
            return []

        actions: list[AutonomyAction] = []
        actions.extend(self._provider_actions(snapshot.get("providers", {})))
        actions.extend(self._routing_actions(snapshot.get("routing", {})))
        actions.extend(self._queue_actions(snapshot.get("runtime", {})))
        actions.extend(self._failure_actions(snapshot.get("failures", {})))

        filtered: list[AutonomyAction] = []
        for action in actions:
            if len(filtered) >= self.max_actions_per_cycle:
                break
            if self._is_allowed(action) and not self._cooling_down(action):
                self._last_action_at[self._action_key(action)] = time.time()
                filtered.append(action)
        return filtered

    async def apply_safe_actions(
        self,
        actions: list[dict[str, Any]],
        *,
        router: Any | None = None,
        observer: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Apply only bounded runtime mitigations.

        Current supported mutations are in-memory only: provider cooldown reset
        and observer issue notification. File/config/code mutation is rejected.
        """

        applied: list[dict[str, Any]] = []
        for action in actions:
            action_type = str(action.get("action_type", ""))
            if action_type in self.FORBIDDEN_ACTIONS or not action.get("safe", True):
                continue
            if action_type == "provider_cooldown" and router is not None:
                target = str(action.get("target", ""))
                if hasattr(router, "_fail_times"):
                    router._fail_times.pop(target, None)
                    action["applied"] = True
            elif (
                action_type == "observer_notify"
                and observer is not None
                and hasattr(observer, "_queue_issue")
            ):
                await observer._queue_issue("autonomy_mitigation", dict(action))
                action["applied"] = True
            applied.append(action)
        await self._record_action_dicts(applied)
        return applied

    def _provider_actions(self, providers: dict[str, Any]) -> list[AutonomyAction]:
        actions: list[AutonomyAction] = []
        for provider, health in providers.items():
            success_rate = float(health.get("success_rate", 1.0))
            degraded = (
                bool(health.get("degraded"))
                or success_rate < self.degraded_success_threshold
            )
            if degraded:
                actions.append(
                    AutonomyAction(
                        action_type="provider_cooldown",
                        target=str(provider),
                        reason="provider_degraded",
                        metadata={"success_rate": success_rate},
                    )
                )
                actions.append(
                    AutonomyAction(
                        action_type="routing_deprioritize",
                        target=str(provider),
                        reason="provider_degraded",
                        metadata={"success_rate": success_rate},
                    )
                )
        return actions

    def _routing_actions(self, routing: dict[str, Any]) -> list[AutonomyAction]:
        actions: list[AutonomyAction] = []
        for chain, stats in routing.get("chains", {}).items():
            success_rate = float(stats.get("success_rate", 1.0))
            retry_rate = float(stats.get("retry_rate", 0.0))
            if success_rate < self.degraded_success_threshold or retry_rate > 0.4:
                actions.append(
                    AutonomyAction(
                        action_type="reorder_chain",
                        target=str(chain),
                        reason="routing_quality_degraded",
                        metadata={
                            "success_rate": success_rate,
                            "retry_rate": retry_rate,
                        },
                    )
                )
        return actions

    def _queue_actions(self, runtime: dict[str, Any]) -> list[AutonomyAction]:
        queue_depth = int(runtime.get("queue_depth", 0) or 0)
        if queue_depth <= self.queue_pressure_threshold:
            return []
        return [
            AutonomyAction(
                action_type="reduce_background_work",
                target="runtime",
                reason="queue_pressure_high",
                metadata={"queue_depth": queue_depth},
            )
        ]

    def _failure_actions(self, failures: dict[str, Any]) -> list[AutonomyAction]:
        repeated = int(failures.get("repeated_retries", 0) or 0)
        if repeated <= 0:
            return []
        return [
            AutonomyAction(
                action_type="trigger_retry",
                target=str(failures.get("target", "unknown")),
                reason="repeated_retry_detected",
                metadata={"repeated_retries": repeated},
            )
        ]

    def _is_allowed(self, action: AutonomyAction) -> bool:
        return action.safe and action.action_type not in self.FORBIDDEN_ACTIONS

    def _cooling_down(self, action: AutonomyAction) -> bool:
        key = self._action_key(action)
        last = self._last_action_at.get(key, 0.0)
        return time.time() - last < self.action_cooldown_seconds

    @staticmethod
    def _action_key(action: AutonomyAction) -> str:
        return f"{action.action_type}:{action.target}"

    async def _record_actions(self, actions: list[AutonomyAction]) -> None:
        await self._record_action_dicts([action.as_dict() for action in actions])

    async def _record_action_dicts(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        db = self.performance_db
        if db is None and PerformanceDB is not None:
            try:
                db = PerformanceDB()
            except Exception:
                db = None
        if db is None:
            return
        for action in actions:
            await asyncio.to_thread(
                db.record_autonomy_action,
                action_type=str(action.get("action_type", "")),
                target=str(action.get("target", "")),
                reason=str(action.get("reason", "")),
                safe=bool(action.get("safe", True)),
                applied=bool(action.get("applied", False)),
                metadata=dict(action.get("metadata", {})),
            )
