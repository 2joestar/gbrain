"""Bounded self-optimization loop for routing telemetry."""

from __future__ import annotations

import asyncio
import time
from statistics import mean
from typing import Any

try:
    from brain.optimization.performance_db import PerformanceDB
except Exception:  # pragma: no cover
    PerformanceDB = None  # type: ignore[assignment]


class SelfOptimizer:
    """Analyzes recent telemetry and proposes safe routing weight adjustments."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        interval_seconds: float = 300.0,
        max_adjustment: float = 0.15,
        performance_db: Any | None = None,
        initial_weights: dict[str, float] | None = None,
    ) -> None:
        self.enabled = enabled
        self.interval_seconds = min(600.0, max(300.0, interval_seconds))
        self.max_adjustment = max(0.01, min(0.5, max_adjustment))
        self.performance_db = performance_db
        self.routing_weights = {
            "latency_weight": 0.25,
            "quality_weight": 1.0,
            "cost_weight": 1.0,
            **(initial_weights or {}),
        }
        self._running = False

    def analyze_once(
        self, traces: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "actions": [],
                "weights": dict(self.routing_weights),
                "enabled": False,
            }

        recent = traces if traces is not None else self._load_recent_traces()
        actions: list[dict[str, Any]] = []
        if not recent:
            return {
                "actions": actions,
                "weights": dict(self.routing_weights),
                "enabled": True,
            }

        failures = [trace for trace in recent if not bool(trace.get("success"))]
        expensive_failures = [
            trace
            for trace in failures
            if float(trace.get("cost_usd", 0.0) or 0.0) > 0.01
        ]
        retries = [
            trace
            for trace in recent
            if int(trace.get("retries", trace.get("retry_count", 0)) or 0) > 1
        ]
        latencies = [
            float(trace.get("latency_ms", 0.0) or 0.0)
            for trace in recent
            if trace.get("latency_ms")
        ]

        if expensive_failures:
            self._adjust("cost_weight", self.max_adjustment)
            actions.append(
                {
                    "type": "adjust_weight",
                    "target": "cost_weight",
                    "delta": self.max_adjustment,
                    "reason": "expensive_failures",
                }
            )

        if failures and len(failures) / max(len(recent), 1) > 0.25:
            self._adjust("quality_weight", self.max_adjustment)
            actions.append(
                {
                    "type": "adjust_weight",
                    "target": "quality_weight",
                    "delta": self.max_adjustment,
                    "reason": "failure_rate_high",
                }
            )

        if latencies and mean(latencies) > 5000:
            self._adjust("latency_weight", self.max_adjustment)
            actions.append(
                {
                    "type": "adjust_weight",
                    "target": "latency_weight",
                    "delta": self.max_adjustment,
                    "reason": "latency_trend_high",
                }
            )

        degraded_chains = self._degraded_chains(recent)
        for chain in degraded_chains:
            actions.append(
                {
                    "type": "reduce_chain_priority",
                    "target": chain,
                    "delta": -self.max_adjustment,
                    "reason": "degraded_chain",
                }
            )

        if retries:
            actions.append(
                {
                    "type": "suggest_strategy",
                    "target": "fallback_policy",
                    "reason": "repeated_retries",
                    "count": len(retries),
                }
            )

        self._record_actions(actions)
        return {
            "actions": actions,
            "weights": dict(self.routing_weights),
            "enabled": True,
        }

    async def run_loop(self) -> None:
        self._running = True
        while self._running:
            try:
                self.analyze_once()
            except Exception:
                pass
            await asyncio.sleep(self.interval_seconds)

    def stop(self) -> None:
        self._running = False

    def _adjust(self, key: str, delta: float) -> None:
        current = float(self.routing_weights.get(key, 1.0))
        self.routing_weights[key] = max(0.001, min(10.0, current + delta))

    def _load_recent_traces(self) -> list[dict[str, Any]]:
        db = self.performance_db
        if db is None and PerformanceDB is not None:
            try:
                db = PerformanceDB()
            except Exception:
                db = None
        if db is None:
            return []
        try:
            return db.recent_routing(limit=200)
        except Exception:
            return []

    @staticmethod
    def _degraded_chains(traces: list[dict[str, Any]]) -> list[str]:
        by_chain: dict[str, list[dict[str, Any]]] = {}
        for trace in traces:
            chain = str(trace.get("chain", ""))
            if chain:
                by_chain.setdefault(chain, []).append(trace)
        degraded: list[str] = []
        for chain, rows in by_chain.items():
            if len(rows) < 3:
                continue
            success_rate = sum(1 for row in rows if bool(row.get("success"))) / len(
                rows
            )
            if success_rate < 0.5:
                degraded.append(chain)
        return degraded

    def _record_actions(self, actions: list[dict[str, Any]]) -> None:
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
            try:
                db.record_autonomy_action(
                    action_type=str(action.get("type", "")),
                    target=str(action.get("target", "")),
                    reason=str(action.get("reason", "")),
                    safe=True,
                    applied=False,
                    metadata={"ts": time.time(), **action},
                )
            except Exception:
                pass
