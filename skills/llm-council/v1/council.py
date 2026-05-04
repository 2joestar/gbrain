"""
LLM Council — Multi-Agent Deliberation Skill (PhoenixGlobal)
=============================================================
AGENT-AGNOSTIC: Works with any agent that provides an async completion function.
Claude, Qwen, Hermes, Mercury, OpenClaw, Codex — all supported.

PROTOCOL:
    council = LLMCouncil(complete_fn=your_async_completion_function)
    result = await council.deliberate("Your question here")

The completion function signature:
    async def complete(messages: list, chain: str = "reasoning") -> str

ARCHITECTURE:
    Question → 5 Advisors (parallel) → Anonymous Peer Review → Chairman Synthesis → Verdict
"""
import asyncio
import hashlib
import logging
import time
from typing import Callable, Dict, List, Optional, Awaitable

log = logging.getLogger("llm-council")

# ── 5 Advisor Personas (agent-agnostic) ─────────────────────────────────
ADVISORS = [
    {
        "name": "The Optimist",
        "prompt": "You are an optimistic advisor. Focus on upside, opportunities, best-case scenarios. Be enthusiastic but grounded. Give a score 1-10 for how promising the proposal is.",
    },
    {
        "name": "The Skeptic",
        "prompt": "You are a skeptical advisor. Focus on risks, downsides, failure modes, what could go wrong. Be critical but fair. Give a score 1-10 for risk level (10 = highest risk).",
    },
    {
        "name": "The Pragmatist",
        "prompt": "You are a pragmatic advisor. Focus on implementation, cost, timeline, practical constraints. What actually needs to happen? Give a score 1-10 for feasibility.",
    },
    {
        "name": "The Innovator",
        "prompt": "Suggest creative alternatives, novel approaches, outside-the-box solutions. Challenge assumptions. Give a score 1-10 for novelty.",
    },
    {
        "name": "The Ethicist",
        "prompt": "You are an ethics advisor. Consider fairness, privacy, societal impact, long-term consequences. What are the ethical implications? Give a score 1-10 for ethical alignment.",
    },
]

CHAIRMAN_PROMPT = """You are the Chairman of an advisory council. You have received opinions from 5 advisors on this question:

{question}

Advisor opinions:
{opinions}

Synthesize a final verdict that:
1. Summarizes the key arguments from all sides
2. Identifies points of agreement and disagreement
3. Gives a clear recommendation with reasoning
4. Suggests next steps

Be decisive but acknowledge uncertainty where it exists."""


class LLMCouncil:
    """
    Multi-agent deliberation council. Agent-agnostic.
    
    Args:
        complete_fn: Async function with signature (messages: list, chain: str) -> str
                     Works with any agent's LLM completion — Claude, Qwen, Hermes, etc.
    """

    def __init__(self, complete_fn: Callable[[list, str], Awaitable[str]]):
        self.complete = complete_fn

    async def deliberate(self, question: str) -> Dict:
        """
        Run the full council deliberation.
        Returns verdict with advisor opinions, peer reviews, and final synthesis.
        """
        t0 = time.time()
        opinions = await self._gather_opinions(question)
        reviews = await self._peer_review(question, opinions)
        verdict = await self._chairman_synthesis(question, opinions, reviews)

        return {
            "question": question,
            "advisors": opinions,
            "peer_reviews": reviews,
            "verdict": verdict,
            "elapsed_ms": round((time.time() - t0) * 1000),
        }

    async def _gather_opinions(self, question: str) -> List[Dict]:
        """Phase 1: All 5 advisors respond in parallel."""
        async def ask(advisor):
            messages = [
                {"role": "system", "content": advisor["prompt"]},
                {"role": "user", "content": f"Question: {question}\n\nProvide your analysis in 3-5 bullet points, then give your score."},
            ]
            try:
                response = await self.complete(messages, "reasoning")
                return {
                    "advisor": advisor["name"],
                    "opinion": response.strip() if response else "No response",
                    "hash": hashlib.md5(advisor["name"].encode()).hexdigest()[:8],
                }
            except Exception as e:
                return {"advisor": advisor["name"], "opinion": f"Error: {str(e)[:200]}", "hash": "error"}

        return await asyncio.gather(*[ask(a) for a in ADVISORS])

    async def _peer_review(self, question: str, opinions: List[Dict]) -> List[Dict]:
        """Phase 2: Advisors review each other's opinions anonymously."""
        async def review_one(i: int):
            reviewer = ADVISORS[i]
            other_opinions = [o for j, o in enumerate(opinions) if j != i]
            opinions_text = "\n\n".join(
                f"Advisor {o['hash'][:4]}: {o['opinion'][:300]}" for o in other_opinions
            )
            messages = [
                {"role": "system", "content": f"You are {reviewer['name']}. Review these anonymous opinions and grade them 1-10. Be fair."},
                {"role": "user", "content": f"Question: {question}\n\nAnonymous opinions:\n{opinions_text}\n\nGrade each opinion 1-10 and explain briefly."},
            ]
            try:
                response = await self.complete(messages, "fast")
                return {"reviewer": reviewer["name"], "review": response.strip()[:500]}
            except Exception as e:
                return {"reviewer": reviewer["name"], "review": f"Error: {str(e)[:200]}"}

        return await asyncio.gather(*[review_one(i) for i in range(len(ADVISORS))])

    async def _chairman_synthesis(self, question: str, opinions: List[Dict], reviews: List[Dict]) -> str:
        """Phase 3: Chairman synthesizes final verdict."""
        opinions_text = "\n\n".join(f"**{o['advisor']}**: {o['opinion'][:300]}" for o in opinions)
        reviews_text = "\n".join(f"{r['reviewer']} reviewed: {r['review'][:200]}" for r in reviews)
        prompt = CHAIRMAN_PROMPT.format(question=question, opinions=f"{opinions_text}\n\nPeer Reviews:\n{reviews_text}")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": "Deliver your final verdict."}]
        try:
            return (await self.complete(messages, "reasoning")).strip()
        except Exception as e:
            return f"Council deliberation failed: {str(e)[:200]}"
