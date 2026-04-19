import json
import re
from typing import Optional
import logging as _logging


def _log_fb(e, d=None):
    _logging.getLogger("planner").debug("%s %s", e, d or {})


def _load_cfg_fb(s):
    return {}


try:
    from ..utils.logger import log
    from ..registry.manager import load as load_cfg
except ImportError:
    log = _log_fb
    load_cfg = _load_cfg_fb

PLAN_PROMPT = """You are a task decomposer for an AI assistant.
Given the user task, return ONLY valid JSON — no explanation, no markdown.

JSON format:
{
  "intent": "brief description of what the user wants",
  "complexity": "simple|medium|complex",
  "steps": [
{
  "id": "s1",
  "type": "search|generate|analyze|remember|tool|code|respond",
  "action": "what to do",
  "input": "what to pass as input",
  "deps": []
}
  ]
}

Rules:
- simple (1 step): greetings, yes/no questions, simple facts
- medium (2-3 steps): questions needing lookup + explanation
- complex (4+ steps): multi-part tasks, research, code projects
- deps = list of step IDs that must complete before this step
- Use "search" for web lookups, "code" for coding tasks, "generate" for text generation
- NEVER return empty steps array

User task: {task}
Available context summary: {context_summary}
"""


def _deterministic_plan(task: str) -> dict:
    """Rule-based fallback planner."""
    t = task.lower()
    if any(
        w in t
        for w in ["hi", "hello", "hey", "how are", "good morning", "good evening"]
    ):
        steps = [
            {
                "id": "s1",
                "type": "respond",
                "action": "greet",
                "input": task,
                "deps": [],
            }
        ]
        complexity = "simple"
    elif any(
        w in t
        for w in [
            "code",
            "write",
            "implement",
            "fix",
            "debug",
            "function",
            "class",
            "script",
            "program",
            "refactor",
        ]
    ):
        steps = [
            {
                "id": "s1",
                "type": "code",
                "action": "generate_code",
                "input": task,
                "deps": [],
            }
        ]
        complexity = "medium"
    elif any(
        w in t
        for w in [
            "search",
            "find",
            "look up",
            "what is",
            "who is",
            "when did",
            "where",
            "how much",
            "latest",
            "news",
        ]
    ):
        steps = [
            {
                "id": "s1",
                "type": "search",
                "action": "web_search",
                "input": task,
                "deps": [],
            },
            {
                "id": "s2",
                "type": "generate",
                "action": "summarize_search",
                "input": task,
                "deps": ["s1"],
            },
        ]
        complexity = "medium"
    elif any(
        w in t
        for w in ["analyze", "review", "explain", "compare", "evaluate", "assess"]
    ):
        steps = [
            {
                "id": "s1",
                "type": "analyze",
                "action": "analyze_input",
                "input": task,
                "deps": [],
            },
            {
                "id": "s2",
                "type": "generate",
                "action": "present_analysis",
                "input": task,
                "deps": ["s1"],
            },
        ]
        complexity = "medium"
    elif any(w in t for w in ["remember", "save", "store", "note", "journal"]):
        steps = [
            {
                "id": "s1",
                "type": "remember",
                "action": "store_memory",
                "input": task,
                "deps": [],
            }
        ]
        complexity = "simple"
    else:
        steps = [
            {
                "id": "s1",
                "type": "generate",
                "action": "respond",
                "input": task,
                "deps": [],
            }
        ]
        complexity = "simple"

    return {"intent": task[:100], "complexity": complexity, "steps": steps}


class Planner:
    def __init__(self, model_router):
        self.router = model_router

    async def plan(self, task: str, context: str = "") -> dict:
        cfg = load_cfg("system").get("planner", {})
        chain = cfg.get("planning_chain", "fast")
        context_summary = context[:300] if context else "none"

        try:
            prompt = PLAN_PROMPT.format(task=task, context_summary=context_summary)
            response = await self.router.complete(
                [{"role": "user", "content": prompt}],
                chain=chain,
                max_tokens=600,
            )
            # Extract JSON from response (handles markdown code blocks too)
            json_str = response.strip()
            if "```" in json_str:
                json_str = re.sub(r"```(?:json)?", "", json_str).strip()
            match = re.search(r"\{.*\}", json_str, re.DOTALL)
            if match:
                try:
                    plan = json.loads(match.group())
                except json.JSONDecodeError:
                    # Try json_repair if available
                    try:
                        import json_repair

                        plan = json_repair.loads(match.group())
                    except ImportError:
                        # Fallback: try to extract first { ... } block with regex cleanup
                        cleaned = _extract_json_block(json_str)
                        if cleaned:
                            plan = json.loads(cleaned)
                        else:
                            raise

                if plan.get("steps"):
                    max_steps = cfg.get("max_steps", 8)
                    plan["steps"] = plan["steps"][:max_steps]
                    log(
                        "plan_llm",
                        {
                            "intent": plan.get("intent", "")[:60],
                            "steps": len(plan["steps"]),
                        },
                    )
                    return plan
        except Exception as e:
            log("planner_llm_failed", {"error": str(e)[:120]})

        if cfg.get("fallback_to_deterministic", True):
            plan = _deterministic_plan(task)
            log("plan_deterministic", {"task": task[:60]})
            return plan

        try:
            from apple.core.reasoning.symbolic_planner import SymbolicPlanner

            sp = SymbolicPlanner()
            steps = sp.plan(task)
            log("plan_symbolic", {"task": task[:60], "steps": len(steps)})
            return {"intent": task, "complexity": "medium", "steps": steps}
        except Exception:
            pass

        return {
            "intent": task,
            "complexity": "simple",
            "steps": [
                {
                    "id": "s1",
                    "type": "respond",
                    "action": "respond",
                    "input": task,
                    "deps": [],
                }
            ],
        }


def _extract_json_block(text: str) -> Optional[str]:
    """Extract the first complete { ... } block from text using brace counting."""
    start = text.find("{")
    if start == -1:
        return None

    brace_count = 0
    in_string = False
    string_char = None
    escape_next = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if in_string:
            if ch == string_char:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            continue

        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                return text[start : i + 1]

    return None
