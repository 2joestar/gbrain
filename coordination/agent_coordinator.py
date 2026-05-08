"""State-aware, confidence-aware agent coordination.

This module is intentionally standalone. Existing orchestrators can import it
behind a feature flag, and tests can exercise it without provider calls.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass(slots=True)
class StepRecord:
    step_id: str
    agent_id: str
    output: str = ""
    confidence: float = 0.5
    status: str = "completed"
    ts: float = field(default_factory=time.time)


@dataclass(slots=True)
class TaskState:
    task_id: str
    current_agent: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    failed_attempts: dict[str, int] = field(default_factory=dict)
    confidence_score: float = 0.5
    selected_agents: dict[str, list[str]] = field(default_factory=dict)
    loop_count: int = 0
    confidence_history: list[float] = field(default_factory=list)
    rolling_memory: deque[StepRecord] = field(default_factory=lambda: deque(maxlen=30))


class AgentCoordinator:
    """Selects the minimal safe agent set for a task step.

    The default behavior is conservative: one best-fit agent per step unless
    collaboration is explicitly requested. Repeated calls for the same step
    return the original decision, preventing duplicate activation.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_recursive_loops: int = 3,
        memory_size: int = 30,
        low_confidence_threshold: float = 0.45,
        high_confidence_threshold: float = 0.75,
    ) -> None:
        self.enabled = enabled
        self.max_recursive_loops = max_recursive_loops
        self.low_confidence_threshold = low_confidence_threshold
        self.high_confidence_threshold = high_confidence_threshold
        self._memory_size = memory_size
        self._tasks: dict[str, TaskState] = {}
        self._lock = RLock()

    def start_task(
        self,
        task_id: str,
        *,
        current_agent: str | None = None,
        confidence: float = 0.5,
    ) -> TaskState:
        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                state = TaskState(
                    task_id=task_id,
                    current_agent=current_agent,
                    confidence_score=self._clamp(confidence),
                    rolling_memory=deque(maxlen=self._memory_size),
                )
                self._tasks[task_id] = state
            else:
                state.current_agent = current_agent or state.current_agent
                state.confidence_score = self._clamp(confidence)
            state.confidence_history.append(state.confidence_score)
            return state

    def get_state(self, task_id: str) -> TaskState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def select_agents(
        self,
        task_id: str,
        step_id: str,
        candidates: list[dict[str, Any]],
        *,
        task_text: str = "",
        collaboration_required: bool = False,
        max_agents: int = 3,
    ) -> list[dict[str, Any]]:
        """Return selected agent dictionaries for a step.

        If the coordinator is disabled, the first candidate is returned to
        preserve existing caller behavior. Enabled mode scores candidates by
        task fit, confidence, prior failures, and duplicate avoidance.
        """

        if not candidates:
            return []

        with self._lock:
            state = self._tasks.setdefault(
                task_id,
                TaskState(
                    task_id=task_id, rolling_memory=deque(maxlen=self._memory_size)
                ),
            )

            prior_selection = state.selected_agents.get(step_id)
            if prior_selection:
                selected_ids = set(prior_selection)
                return [
                    candidate
                    for candidate in candidates
                    if self._agent_id(candidate) in selected_ids
                ]

            if not self.enabled:
                selected = [candidates[0]]
                state.selected_agents[step_id] = [self._agent_id(candidates[0])]
                return selected

            if state.loop_count >= self.max_recursive_loops:
                return []

            scored = sorted(
                candidates,
                key=lambda candidate: self._score_candidate(
                    candidate, state, task_text
                ),
                reverse=True,
            )
            if collaboration_required:
                selected = scored[: max(1, min(max_agents, len(scored)))]
            else:
                selected = scored[:1]

            state.selected_agents[step_id] = [
                self._agent_id(candidate) for candidate in selected
            ]
            state.current_agent = (
                self._agent_id(selected[0]) if selected else state.current_agent
            )
            return selected

    def record_step(
        self,
        task_id: str,
        step_id: str,
        agent_id: str,
        *,
        output: str = "",
        confidence: float = 0.5,
    ) -> None:
        with self._lock:
            state = self._tasks.setdefault(
                task_id,
                TaskState(
                    task_id=task_id, rolling_memory=deque(maxlen=self._memory_size)
                ),
            )
            if step_id not in state.completed_steps:
                state.completed_steps.append(step_id)
            state.current_agent = agent_id
            state.confidence_score = self._clamp(confidence)
            state.confidence_history.append(state.confidence_score)
            state.rolling_memory.append(
                StepRecord(
                    step_id=step_id,
                    agent_id=agent_id,
                    output=output[-1200:],
                    confidence=state.confidence_score,
                )
            )

    def record_failure(
        self,
        task_id: str,
        step_id: str,
        agent_id: str,
        *,
        confidence: float | None = None,
    ) -> None:
        with self._lock:
            state = self._tasks.setdefault(
                task_id,
                TaskState(
                    task_id=task_id, rolling_memory=deque(maxlen=self._memory_size)
                ),
            )
            failure_key = f"{step_id}:{agent_id}"
            state.failed_attempts[failure_key] = (
                state.failed_attempts.get(failure_key, 0) + 1
            )
            state.loop_count += 1
            if confidence is not None:
                state.confidence_score = self._clamp(confidence)
                state.confidence_history.append(state.confidence_score)
            state.rolling_memory.append(
                StepRecord(
                    step_id=step_id,
                    agent_id=agent_id,
                    confidence=state.confidence_score,
                    status="failed",
                )
            )

    def model_scale_for_confidence(self, confidence: float | None = None) -> str:
        value = self._clamp(confidence if confidence is not None else 0.5)
        if value >= self.high_confidence_threshold:
            return "small_fast"
        if value <= self.low_confidence_threshold:
            return "strong_reasoning"
        return "balanced"

    def should_escalate(self, task_id: str, *, confidence: float | None = None) -> bool:
        state = self.get_state(task_id)
        value = (
            confidence
            if confidence is not None
            else (state.confidence_score if state else 0.5)
        )
        return self.model_scale_for_confidence(value) == "strong_reasoning"

    def recent_memory(self, task_id: str, limit: int = 10) -> list[dict[str, Any]]:
        state = self.get_state(task_id)
        if state is None:
            return []
        return [
            {
                "step_id": record.step_id,
                "agent_id": record.agent_id,
                "output": record.output,
                "confidence": record.confidence,
                "status": record.status,
                "ts": record.ts,
            }
            for record in list(state.rolling_memory)[-limit:]
        ]

    def _score_candidate(
        self,
        candidate: dict[str, Any],
        state: TaskState,
        task_text: str,
    ) -> float:
        agent_id = self._agent_id(candidate)
        confidence = float(candidate.get("confidence", state.confidence_score))
        capabilities = [
            str(value).lower() for value in candidate.get("capabilities", [])
        ]
        task_words = set(task_text.lower().split())
        capability_hits = sum(
            1 for capability in capabilities if capability in task_words
        )
        failure_penalty = sum(
            attempts
            for key, attempts in state.failed_attempts.items()
            if key.endswith(f":{agent_id}")
        )
        repeat_penalty = 0.2 if agent_id == state.current_agent else 0.0
        availability = float(candidate.get("availability", 1.0))
        return (
            (capability_hits * 2.0)
            + confidence
            + availability
            - failure_penalty
            - repeat_penalty
        )

    @staticmethod
    def _agent_id(candidate: dict[str, Any]) -> str:
        return str(
            candidate.get("id")
            or candidate.get("name")
            or candidate.get("role")
            or "agent"
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
