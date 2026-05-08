"""Predictive routing engine.

Predicts a chain before execution using historical traces, provider health,
latency, cost, task type, complexity, and prior success rate.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

try:
    from brain.optimization.performance_db import PerformanceDB
except Exception:  # pragma: no cover - standalone fallback
    PerformanceDB = None  # type: ignore[assignment]


class PredictiveRouter:
    """Scores chains without mutating existing router state."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        quality_weight: float = 1.0,
        latency_weight: float = 0.25,
        cost_weight: float = 1.0,
        confidence_bonus_weight: float = 0.2,
        performance_db: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.quality_weight = max(0.001, quality_weight)
        self.latency_weight = max(0.001, latency_weight)
        self.cost_weight = max(0.001, cost_weight)
        self.confidence_bonus_weight = confidence_bonus_weight
        self.performance_db = performance_db

    def predict(
        self,
        *,
        task_type: str,
        task_complexity: float,
        chains: dict[str, list[str]],
        provider_health: dict[str, dict[str, Any]] | None = None,
        recent_latency: dict[str, float] | None = None,
        success_history: dict[str, float] | None = None,
        model_costs: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if not chains:
            return {
                "recommended_chain": "",
                "predicted_latency": 0.0,
                "predicted_success": 0.0,
                "fallback_chain": [],
            }

        provider_health = provider_health or {}
        recent_latency = recent_latency or {}
        success_history = success_history or {}
        model_costs = model_costs or {}

        historical = self._historical_chain_stats()
        scored: list[dict[str, Any]] = []
        for chain_name, models in chains.items():
            if not models:
                continue
            success_rate = self._chain_success_rate(
                chain_name, models, provider_health, success_history, historical
            )
            latency = self._chain_latency(
                chain_name, models, provider_health, recent_latency, historical
            )
            cost = self._chain_cost(models, model_costs)
            confidence_bonus = self._confidence_bonus(
                task_type, task_complexity, chain_name
            )
            degradation_penalty = self._degradation_penalty(models, provider_health)
            score = (
                self._score(success_rate, latency, cost, confidence_bonus)
                - degradation_penalty
            )
            scored.append(
                {
                    "chain": chain_name,
                    "score": score,
                    "latency": latency,
                    "success": max(0.0, min(1.0, success_rate - degradation_penalty)),
                }
            )

        if not scored:
            first_chain = next(iter(chains))
            return {
                "recommended_chain": first_chain,
                "predicted_latency": 0.0,
                "predicted_success": 0.5,
                "fallback_chain": [],
            }

        ranked = sorted(scored, key=lambda item: item["score"], reverse=True)
        recommended = ranked[0]
        return {
            "recommended_chain": recommended["chain"],
            "predicted_latency": round(float(recommended["latency"]), 3),
            "predicted_success": round(float(recommended["success"]), 4),
            "fallback_chain": [item["chain"] for item in ranked[1:]],
        }

    def _score(
        self, success_rate: float, latency: float, cost: float, confidence_bonus: float
    ) -> float:
        denominator = (max(latency, 0.001) * self.latency_weight) + (
            max(cost, 0.000001) * self.cost_weight
        )
        return ((success_rate * self.quality_weight) / denominator) + confidence_bonus

    def _historical_chain_stats(self) -> dict[str, dict[str, float]]:
        db = self.performance_db
        if db is None and PerformanceDB is not None:
            try:
                db = PerformanceDB(initialize=True)
            except Exception:
                db = None
        if db is None:
            return {}
        try:
            rows = db.recent_routing(limit=300)
        except Exception:
            return {}

        grouped: dict[str, dict[str, list[float]]] = {}
        for row in rows:
            chain = str(row.get("chain") or "")
            if not chain:
                continue
            bucket = grouped.setdefault(
                chain, {"success": [], "latency": [], "cost": []}
            )
            bucket["success"].append(float(row.get("success") or 0))
            bucket["latency"].append(float(row.get("latency_ms") or 0) / 1000.0)
            bucket["cost"].append(float(row.get("cost_usd") or 0))

        return {
            chain: {
                "success_rate": mean(values["success"]) if values["success"] else 0.5,
                "latency": mean([value for value in values["latency"] if value > 0])
                if values["latency"]
                else 1.0,
                "cost": mean(values["cost"]) if values["cost"] else 0.001,
            }
            for chain, values in grouped.items()
        }

    def _chain_success_rate(
        self,
        chain_name: str,
        models: list[str],
        provider_health: dict[str, dict[str, Any]],
        success_history: dict[str, float],
        historical: dict[str, dict[str, float]],
    ) -> float:
        values: list[float] = []
        if chain_name in success_history:
            values.append(float(success_history[chain_name]))
        if chain_name in historical:
            values.append(float(historical[chain_name].get("success_rate", 0.5)))
        for model in models:
            health = (
                provider_health.get(model)
                or provider_health.get(self._provider_name(model))
                or {}
            )
            if "success_rate" in health:
                values.append(float(health["success_rate"]))
            elif health.get("degraded"):
                values.append(0.25)
        return max(0.05, min(0.99, mean(values) if values else 0.65))

    def _chain_latency(
        self,
        chain_name: str,
        models: list[str],
        provider_health: dict[str, dict[str, Any]],
        recent_latency: dict[str, float],
        historical: dict[str, dict[str, float]],
    ) -> float:
        values: list[float] = []
        if chain_name in recent_latency:
            values.append(float(recent_latency[chain_name]))
        if chain_name in historical:
            values.append(float(historical[chain_name].get("latency", 1.0)))
        for model in models:
            health = (
                provider_health.get(model)
                or provider_health.get(self._provider_name(model))
                or {}
            )
            latency = health.get("latency") or health.get("avg_latency")
            if latency is not None:
                values.append(float(latency))
        return max(0.05, mean(values) if values else 1.0)

    def _chain_cost(self, models: list[str], model_costs: dict[str, float]) -> float:
        costs = [
            float(model_costs.get(model, self._default_cost(model))) for model in models
        ]
        return max(0.000001, mean(costs) if costs else 0.001)

    def _confidence_bonus(
        self, task_type: str, task_complexity: float, chain_name: str
    ) -> float:
        complexity = max(0.0, min(1.0, float(task_complexity)))
        chain = chain_name.lower()
        if complexity >= 0.7 and any(
            value in chain for value in ("reason", "complex", "coding")
        ):
            return self.confidence_bonus_weight
        if complexity <= 0.35 and any(
            value in chain for value in ("fast", "cheap", "bulk")
        ):
            return self.confidence_bonus_weight
        if task_type.lower() in chain:
            return self.confidence_bonus_weight / 2
        return 0.0

    @staticmethod
    def _degradation_penalty(
        models: list[str], provider_health: dict[str, dict[str, Any]]
    ) -> float:
        penalty = 0.0
        for model in models:
            health = (
                provider_health.get(model)
                or provider_health.get(PredictiveRouter._provider_name(model))
                or {}
            )
            if health.get("degraded"):
                penalty += 0.35
            failures = float(health.get("failures", 0) or 0)
            penalty += min(0.3, failures * 0.05)
        return penalty

    @staticmethod
    def _default_cost(model: str) -> float:
        lower = model.lower()
        if "local" in lower or lower.startswith(("llama", "qwen")) and ":" in lower:
            return 0.000001
        if any(
            value in lower
            for value in ("mini", "flash", "deepseek", "cerebras", "groq")
        ):
            return 0.0001
        if any(value in lower for value in ("opus", "gpt-5", "sonnet")):
            return 0.003
        return 0.001

    @staticmethod
    def _provider_name(model: str) -> str:
        if ":" in model:
            return model.split(":", 1)[0]
        if "/" in model:
            return model.split("/", 1)[0]
        return model
