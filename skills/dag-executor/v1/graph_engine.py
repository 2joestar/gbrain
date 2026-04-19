import asyncio
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging as _logging
import importlib.util as _ilu
from pathlib import Path as _Path


def _log_fb(e, d=None):
    _logging.getLogger("dag").debug("%s %s", e, d or {})


def _load_tracer():
    _f = _Path("/mnt/c/Users/PhoenixGlobal/brain/skills/tracer/v1/tracer.py")
    _s = _ilu.spec_from_file_location("phoenix_tracer", _f)
    _m = _ilu.module_from_spec(_s)
    _s.loader.exec_module(_m)
    return _m.Tracer


try:
    from ..utils.logger import log
    from ..utils.tracer import Tracer
except ImportError:
    log = _log_fb
    Tracer = _load_tracer()


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ExecNode:
    id: str
    action_type: str
    action: str
    input: Any
    deps: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    output: Any = None
    error: Optional[str] = None
    retries: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0


class ExecutionGraph:
    """Deterministic state-machine execution graph.
    Each request is a directed acyclic graph of ExecNodes.
    Dependency ordering is respected; retries are automatic.
    """

    MAX_RETRIES = 2
    RETRY_DELAY = 0.5  # seconds, multiplied by attempt number

    def __init__(self, model_router, tools: dict, memory, hermes):
        self.router = model_router
        self.tools = tools
        self.memory = memory
        self.hermes = hermes

        self._handlers = {
            "search": self._exec_search,
            "generate": self._exec_generate,
            "respond": self._exec_generate,
            "analyze": self._exec_analyze,
            "code": self._exec_code,
            "remember": self._exec_remember,
            "tool": self._exec_tool,
        }

    async def execute(self, plan: dict, tracer: Tracer) -> dict:
        nodes: Dict[str, ExecNode] = {
            s["id"]: ExecNode(
                id=s["id"],
                action_type=s["type"],
                action=s["action"],
                input=s.get("input", ""),
                deps=s.get("deps", []),
            )
            for s in plan.get("steps", [])
        }

        if not nodes:
            return {"output": None, "nodes": {}, "success": False}

        state: Dict[str, Any] = {}
        completed: set = set()
        failed: set = set()
        max_iter = len(nodes) * (self.MAX_RETRIES + 2)

        for _ in range(max_iter):
            if len(completed) + len(failed) >= len(nodes):
                break

            made_progress = False
            for nid, node in nodes.items():
                if nid in completed or nid in failed:
                    continue
                # Skip if any dep failed
                if any(d in failed for d in node.deps):
                    node.status = NodeStatus.SKIPPED
                    failed.add(nid)
                    continue
                # Wait for all deps
                if not all(d in completed for d in node.deps):
                    continue

                made_progress = True
                dep_outputs = "\n".join(
                    str(state[d])[:400] for d in node.deps if d in state and state[d]
                )

                node.status = NodeStatus.RUNNING
                node.started_at = time.time()
                tracer.add_step(nid, node.action_type, node.input)

                success = False
                for attempt in range(self.MAX_RETRIES + 1):
                    try:
                        handler = self._handlers.get(
                            node.action_type, self._exec_generate
                        )
                        node.output = await handler(node.input, dep_outputs, state)
                        node.status = NodeStatus.SUCCESS
                        node.retries = attempt
                        success = True
                        break
                    except Exception as ex:
                        node.error = str(ex)[:200]
                        log(
                            "node_error",
                            {"node": nid, "attempt": attempt, "error": node.error},
                        )
                        if attempt < self.MAX_RETRIES:
                            await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))

                node.completed_at = time.time()
                if success:
                    state[nid] = node.output
                    completed.add(nid)
                    tracer.complete_step(nid, node.output, "success")
                else:
                    node.status = NodeStatus.FAILED
                    failed.add(nid)
                    tracer.complete_step(nid, node.error, "failed")

            if not made_progress:
                break

        # Find best final output (last successful node output)
        final_output = None
        for nid in reversed(list(nodes.keys())):
            nd = nodes[nid]
            if nd.status == NodeStatus.SUCCESS and nd.output:
                final_output = nd.output
                break

        return {
            "output": final_output,
            "nodes": {
                k: {"status": v.status.value, "output": v.output, "error": v.error}
                for k, v in nodes.items()
            },
            "success": bool(completed),
            "failed_nodes": list(failed),
        }

    # ── Handlers ─────────────────────────────────────────────────────────

    async def _exec_generate(self, inp: str, context: str, state: dict) -> str:
        messages = []
        if context:
            messages.append({"role": "system", "content": f"Prior context:\n{context}"})
        messages.append({"role": "user", "content": inp})
        return await self.router.complete(messages, chain="fast", max_tokens=2048)

    async def _exec_code(self, inp: str, context: str, state: dict) -> str:
        messages = [
            {
                "role": "system",
                "content": "You are an expert programmer. "
                "Provide clean, working code with brief explanation.",
            },
        ]
        if context:
            messages.append({"role": "system", "content": f"Context:\n{context}"})
        messages.append({"role": "user", "content": inp})
        return await self.router.complete(messages, chain="coding", max_tokens=3000)

    async def _exec_search(self, inp: str, context: str, state: dict) -> str:
        # Try real web search tool first
        if "web_search" in self.tools:
            try:
                tracer_hint = f"search:{inp[:40]}"
                result = await self.tools["web_search"].execute({"query": inp})
                if result and len(result) > 20:
                    return result
            except Exception as e:
                log("search_tool_failed", {"error": str(e)})
        # Fallback: LLM from knowledge
        return await self.router.complete(
            [
                {
                    "role": "user",
                    "content": f"Answer from your knowledge (be factual, note if uncertain): {inp}",
                }
            ],
            chain="fast",
            max_tokens=800,
        )

    async def _exec_analyze(self, inp: str, context: str, state: dict) -> str:
        messages = [
            {
                "role": "system",
                "content": "You are an analytical assistant. Be precise, structured, and evidence-based.",
            },
        ]
        if context:
            messages.append({"role": "system", "content": f"Context:\n{context}"})
        messages.append({"role": "user", "content": f"Analyze: {inp}"})
        return await self.router.complete(messages, chain="complex", max_tokens=1500)

    async def _exec_remember(self, inp: str, context: str, state: dict) -> str:
        await self.memory.write(inp, memory_type="episodic")
        return "Stored in memory."

    async def _exec_tool(self, inp: str, context: str, state: dict) -> str:
        # Parse "tool_name: args" or just use first available tool
        parts = inp.split(":", 1)
        tool_name = parts[0].strip().lower().replace(" ", "_")
        tool_args = parts[1].strip() if len(parts) > 1 else inp
        tool = self.tools.get(tool_name)
        if tool:
            result = await tool.execute({"input": tool_args, "query": tool_args})
            return str(result)
        return f"Tool '{tool_name}' not found. Available: {list(self.tools.keys())}"
