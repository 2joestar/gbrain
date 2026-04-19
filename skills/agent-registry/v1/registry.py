import logging as _logging


def _log_fb(e, d=None):
    _logging.getLogger("agents").debug("%s %s", e, d or {})


def _get_agents_fb():
    return []


try:
    from ..registry.manager import get_agents
    from ..utils.logger import log
except ImportError:
    get_agents = _get_agents_fb
    log = _log_fb


class AgentRegistry:
    """Dynamic agent registry loaded from config/agents.yaml."""

    def select(self, task: str, capabilities_needed: list = None) -> dict:
        agents = get_agents()
        if not agents:
            return {}

        task_lower = task.lower()

        def capability_hits(agent: dict) -> int:
            caps = [c.lower() for c in agent.get("capabilities", [])]
            hits = sum(1 for c in caps if c in task_lower)
            if capabilities_needed:
                hits += sum(1 for c in caps if c in capabilities_needed)
            return hits

        def score(agent: dict) -> int:
            s = capability_hits(agent) * 10
            risk_bonus = {"low": 3, "medium": 1, "high": 0}
            s += risk_bonus.get(agent.get("risk_level", "low"), 0)
            return s

        # If nothing matches by capability, prefer the "fast" role agent.
        if not any(capability_hits(a) for a in agents):
            fast = next((a for a in agents if a.get("role") == "fast"), None)
            if fast:
                log(
                    "agent_selected",
                    {"id": fast.get("id"), "task": task[:60], "fallback": True},
                )
                return fast

        scored = sorted(agents, key=score, reverse=True)
        best = scored[0] if scored else {}
        if best:
            log("agent_selected", {"id": best.get("id"), "task": task[:60]})
        return best

    def get_all(self) -> list:
        return get_agents()

    def get_by_role(self, role: str) -> list:
        return [a for a in get_agents() if a.get("role") == role]
