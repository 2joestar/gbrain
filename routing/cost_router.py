"""Cost-aware model selection."""

from __future__ import annotations

from typing import Any


class CostAwareRouter:
    """Selects cheap models first and escalates only on risk signals."""

    DEFAULT_MODEL_COSTS = {
        "deepseek/deepseek-v4-pro": 0.14,
        "deepseek/deepseek-v3.2": 0.27,
        "deepseek-r1": 0.55,
        "qwen/qwen3-coder": 0.15,
        "openai/gpt-4.1-mini": 0.15,
        "google/gemini-2.5-flash": 0.15,
        "anthropic/claude-sonnet-4": 3.00,
        "anthropic/claude-opus-4": 15.00,
        "openai/gpt-5.4": 3.75,
        "llama3.1:8b": 0.0,
        "qwen3.5:9b": 0.0,
    }

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_cost_per_request: float = 0.05,
        latency_weight: float = 0.25,
        quality_weight: float = 1.0,
        cost_weight: float = 1.0,
        low_confidence_threshold: float = 0.45,
        high_complexity_threshold: float = 0.7,
        model_costs: dict[str, float] | None = None,
    ) -> None:
        self.enabled = enabled
        self.max_cost_per_request = max_cost_per_request
        self.latency_weight = max(0.001, latency_weight)
        self.quality_weight = max(0.001, quality_weight)
        self.cost_weight = max(0.001, cost_weight)
        self.low_confidence_threshold = low_confidence_threshold
        self.high_complexity_threshold = high_complexity_threshold
        self.model_costs = {**self.DEFAULT_MODEL_COSTS, **(model_costs or {})}

    def select_model(
        self,
        *,
        task_text: str,
        candidates: list[dict[str, Any]],
        confidence: float = 0.75,
        planner_complexity: float = 0.2,
        ambiguity: bool = False,
        prior_failure: bool = False,
    ) -> dict[str, Any]:
        if not candidates:
            return {
                "recommended_model": "",
                "recommended_chain": "",
                "estimated_tokens": 0,
                "estimated_cost": 0.0,
                "escalated": False,
                "reason": "no_candidates",
            }

        estimated_tokens = self.estimate_tokens(task_text)
        enriched = [
            self._enrich_candidate(candidate, estimated_tokens)
            for candidate in candidates
        ]
        escalation = self.should_escalate(
            confidence=confidence,
            planner_complexity=planner_complexity,
            ambiguity=ambiguity,
            prior_failure=prior_failure,
        )

        healthy = [
            candidate for candidate in enriched if candidate.get("healthy", True)
        ]
        pool = healthy or enriched
        if escalation:
            selected = max(
                pool,
                key=lambda candidate: (
                    float(candidate.get("quality", 0.5)),
                    -float(candidate.get("latency", 1.0)),
                    -float(candidate.get("estimated_cost", 0.0)),
                ),
            )
            reason = "escalated_for_confidence_or_complexity"
        else:
            selected = min(
                pool,
                key=lambda candidate: (
                    candidate["estimated_cost"],
                    candidate.get("latency", 1.0),
                ),
            )
            reason = "cheap_fast_path"

        if selected["estimated_cost"] > self.max_cost_per_request:
            affordable = [
                candidate
                for candidate in pool
                if candidate["estimated_cost"] <= self.max_cost_per_request
            ]
            if affordable and not escalation:
                selected = min(
                    affordable, key=lambda candidate: candidate["estimated_cost"]
                )
                reason = "max_cost_guardrail"

        return {
            "recommended_model": selected.get("model", ""),
            "recommended_chain": selected.get("chain", ""),
            "estimated_tokens": estimated_tokens,
            "estimated_cost": round(float(selected["estimated_cost"]), 8),
            "escalated": escalation,
            "reason": reason,
        }

    def should_escalate(
        self,
        *,
        confidence: float,
        planner_complexity: float,
        ambiguity: bool,
        prior_failure: bool,
    ) -> bool:
        return (
            confidence < self.low_confidence_threshold
            or planner_complexity >= self.high_complexity_threshold
            or ambiguity
            or prior_failure
        )

    def estimate_tokens(self, task_text: str) -> int:
        words = max(1, len(task_text.split()))
        return max(64, int(words * 1.35) + 128)

    def estimate_cost(self, model: str, tokens: int) -> float:
        cost_per_million = self.get_model_cost(model)
        return (cost_per_million * tokens) / 1_000_000

    def get_model_cost(self, model: str) -> float:
        for key, cost in self.model_costs.items():
            if key in model or model in key:
                return float(cost)
        lower = model.lower()
        if "local" in lower or (":" in lower and "/" not in lower):
            return 0.0
        if any(
            value in lower
            for value in ("free", "deepseek", "cerebras", "groq", "flash", "mini")
        ):
            return 0.15
        return 1.0

    def _enrich_candidate(
        self, candidate: dict[str, Any], estimated_tokens: int
    ) -> dict[str, Any]:
        enriched = dict(candidate)
        model = str(enriched.get("model", ""))
        enriched["estimated_cost"] = self.estimate_cost(model, estimated_tokens)
        enriched.setdefault("quality", self._default_quality(model))
        enriched.setdefault("latency", 1.0)
        enriched.setdefault("healthy", True)
        return enriched

    def _quality_score(self, candidate: dict[str, Any]) -> float:
        cost = max(float(candidate.get("estimated_cost", 0.0)), 0.000001)
        latency = max(float(candidate.get("latency", 1.0)), 0.05)
        quality = float(candidate.get("quality", 0.5))
        return (quality * self.quality_weight) / (
            (latency * self.latency_weight) + (cost * self.cost_weight)
        )

    @staticmethod
    def _default_quality(model: str) -> float:
        lower = model.lower()
        if any(value in lower for value in ("opus", "gpt-5", "sonnet", "reason")):
            return 0.95
        if any(value in lower for value in ("coder", "70b", "deepseek")):
            return 0.8
        if any(value in lower for value in ("mini", "flash", "8b", "9b")):
            return 0.6
        return 0.7
