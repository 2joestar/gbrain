import time, uuid
from typing import Any, Optional

class Tracer:
    def __init__(self):
        self.request_id = str(uuid.uuid4())[:8]
        self.started_at = time.time()
        self.steps: list = []
        self.plan: Optional[dict] = None
        self.agents_used: list = []
        self.tools_used: list = []
        self.memory_used: list = []

    def set_plan(self, plan: dict):
        self.plan = {
            "intent": plan.get("intent", "")[:120],
            "complexity": plan.get("complexity", "unknown"),
            "step_count": len(plan.get("steps", [])),
        }

    def add_step(self, step_id: str, action_type: str, input_data: Any):
        self.steps.append({
            "id": step_id,
            "type": action_type,
            "input_preview": str(input_data)[:80],
            "started": round(time.time(), 3),
            "status": "running",
            "output_preview": None,
        })

    def complete_step(self, step_id: str, output: Any, status: str):
        for s in self.steps:
            if s["id"] == step_id:
                s["status"] = status
                s["output_preview"] = str(output)[:120] if output else None
                s["completed"] = round(time.time(), 3)
                break

    def add_tool(self, tool_name: str):
        if tool_name not in self.tools_used:
            self.tools_used.append(tool_name)

    def add_agent(self, agent_id: str):
        if agent_id not in self.agents_used:
            self.agents_used.append(agent_id)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "elapsed_ms": round((time.time() - self.started_at) * 1000),
            "plan": self.plan,
            "steps": self.steps,
            "agents_used": self.agents_used,
            "tools_used": self.tools_used,
            "memory_used": self.memory_used,
        }
