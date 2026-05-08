import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brain.autonomy.autonomy_manager import AutonomyManager
from brain.coordination.agent_coordinator import AgentCoordinator
from brain.optimization.performance_db import PerformanceDB
from brain.optimization.self_optimizer import SelfOptimizer
from brain.routing.cost_router import CostAwareRouter
from brain.routing.predictive_router import PredictiveRouter


def test_agent_coordinator_prevents_duplicate_activation():
    coordinator = AgentCoordinator(enabled=True)
    candidates = [
        {"id": "fast", "capabilities": ["chat"], "confidence": 0.8},
        {"id": "planner", "capabilities": ["planning"], "confidence": 0.7},
    ]

    first = coordinator.select_agents(
        "task-1", "step-1", candidates, task_text="chat now"
    )
    second = coordinator.select_agents(
        "task-1", "step-1", candidates, task_text="chat now"
    )

    assert [agent["id"] for agent in first] == ["fast"]
    assert [agent["id"] for agent in second] == ["fast"]
    assert len(second) == 1


def test_agent_coordinator_confidence_escalation():
    coordinator = AgentCoordinator(enabled=True, low_confidence_threshold=0.45)
    coordinator.start_task("task-2", confidence=0.3)

    assert coordinator.should_escalate("task-2")
    assert coordinator.model_scale_for_confidence(0.9) == "small_fast"


def test_predictive_router_scores_degraded_model_lower_and_fallbacks():
    router = PredictiveRouter(enabled=True)
    result = router.predict(
        task_type="quick-chat",
        task_complexity=0.2,
        chains={
            "fast": ["fast-model"],
            "reasoning": ["reason-model"],
        },
        provider_health={
            "fast-model": {"success_rate": 0.2, "latency": 4.0, "degraded": True},
            "reason-model": {"success_rate": 0.9, "latency": 1.0},
        },
        model_costs={"fast-model": 0.0001, "reason-model": 0.0002},
    )

    assert result["recommended_chain"] == "reasoning"
    assert result["fallback_chain"] == ["fast"]
    assert result["predicted_success"] > 0.5


def test_cost_router_cheap_task_uses_cheap_model():
    router = CostAwareRouter(enabled=True)
    result = router.select_model(
        task_text="say hi",
        confidence=0.9,
        planner_complexity=0.1,
        candidates=[
            {
                "model": "anthropic/claude-opus-4",
                "chain": "reasoning",
                "quality": 0.99,
                "latency": 2.0,
            },
            {"model": "llama3.1:8b", "chain": "fast", "quality": 0.6, "latency": 0.2},
        ],
    )

    assert result["recommended_model"] == "llama3.1:8b"
    assert not result["escalated"]


def test_cost_router_escalates_on_low_confidence():
    router = CostAwareRouter(enabled=True)
    result = router.select_model(
        task_text="debug a complex distributed routing failure",
        confidence=0.2,
        planner_complexity=0.8,
        candidates=[
            {"model": "llama3.1:8b", "chain": "fast", "quality": 0.4, "latency": 0.2},
            {
                "model": "anthropic/claude-sonnet-4",
                "chain": "reasoning",
                "quality": 0.95,
                "latency": 1.5,
            },
        ],
    )

    assert result["recommended_model"] == "anthropic/claude-sonnet-4"
    assert result["escalated"]


def test_autonomy_manager_detects_degraded_provider():
    manager = AutonomyManager(enabled=True, action_cooldown_seconds=0)
    actions = manager.evaluate(
        {
            "providers": {
                "groq": {"success_rate": 0.2, "degraded": True},
            }
        }
    )

    action_types = {action.action_type for action in actions}
    assert "provider_cooldown" in action_types
    assert "routing_deprioritize" in action_types
    assert all(action.safe for action in actions)


def test_self_optimizer_adjusts_scores_safely(tmp_path):
    db = PerformanceDB(tmp_path / "performance.db")
    optimizer = SelfOptimizer(enabled=True, performance_db=db, max_adjustment=0.1)
    result = optimizer.analyze_once(
        [
            {
                "chain": "fast",
                "success": False,
                "latency_ms": 7000,
                "cost_usd": 0.02,
                "retries": 2,
            },
            {
                "chain": "fast",
                "success": False,
                "latency_ms": 8000,
                "cost_usd": 0.02,
                "retries": 2,
            },
            {
                "chain": "fast",
                "success": False,
                "latency_ms": 9000,
                "cost_usd": 0.02,
                "retries": 2,
            },
            {
                "chain": "reasoning",
                "success": True,
                "latency_ms": 2000,
                "cost_usd": 0.01,
            },
        ]
    )

    assert result["weights"]["cost_weight"] <= 1.1
    assert result["weights"]["quality_weight"] <= 1.1
    assert any(
        action["type"] == "reduce_chain_priority" for action in result["actions"]
    )


def test_performance_db_uses_wal_and_tables(tmp_path):
    db = PerformanceDB(tmp_path / "performance.db")
    db.record_routing(task_type="chat", chain="fast", model="llama3.1:8b", success=True)

    stats = db.model_stats()
    rows = db.recent_routing(limit=5)

    assert "llama3.1:8b" in stats
    assert rows[0]["chain"] == "fast"


def test_concurrent_requests_do_not_deadlock(tmp_path):
    db = PerformanceDB(tmp_path / "performance.db")
    coordinator = AgentCoordinator(enabled=True)
    autonomy = AutonomyManager(
        enabled=True, performance_db=db, action_cooldown_seconds=0
    )

    async def run_one(index: int):
        task_id = f"task-{index}"
        selected = coordinator.select_agents(
            task_id,
            "step",
            [{"id": "fast", "capabilities": ["chat"], "confidence": 0.9}],
            task_text="chat",
        )
        await db.record_routing_async(
            task_type="chat",
            chain="fast",
            model="llama3.1:8b",
            success=True,
        )
        actions = await autonomy.monitor_once(
            {
                "providers": {"groq": {"success_rate": 0.9}},
                "runtime": {"queue_depth": 1},
            }
        )
        return selected, actions

    async def run_all():
        return await asyncio.wait_for(
            asyncio.gather(*(run_one(index) for index in range(20))),
            timeout=5,
        )

    results = asyncio.run(run_all())
    assert len(results) == 20
    assert all(result[0][0]["id"] == "fast" for result in results)
