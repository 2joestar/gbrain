"""
Tech Debt Analyzer — Thorough code review skill (PhoenixGlobal)
===============================================================
Inspired by @KSimback /tech-debt-skill.
Finds bugs, issues, AND things that work but could be improved.
Agent-agnostic — works with any agent's completion function.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, List, Callable, Awaitable

log = logging.getLogger("tech-debt")

ANALYSIS_PROMPT = """You are a senior code reviewer. Analyze this code for:

1. BUGS: Logic errors, race conditions, missing error handling
2. CODE SMELLS: Duplicate code, long functions, magic numbers
3. IMPROVEMENTS: Things that work but could be cleaner/faster/safer
4. SECURITY: Injection risks, exposed secrets, unsafe operations
5. PERFORMANCE: N+1 queries, blocking calls, memory leaks
6. MAINTAINABILITY: Unclear naming, missing docstrings, tight coupling

For each finding, give:
- Severity (CRITICAL/HIGH/MEDIUM/LOW)
- File + line reference if visible
- What's wrong
- How to fix it

Be thorough but practical. Don't flag style preferences as bugs."""


class TechDebtAnalyzer:
    """Analyzes code for bugs, smells, and improvements."""

    def __init__(self, complete_fn: Callable[[list, str], Awaitable[str]]):
        self.complete = complete_fn

    async def analyze(self, code_or_path: str, is_file: bool = False) -> Dict:
        """Run full tech debt analysis. Returns structured report."""
        t0 = time.time()
        
        # If it's a file path, read it
        content = code_or_path
        if is_file:
            try:
                content = Path(code_or_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = f"// Could not read file: {code_or_path}"

        # Analyze in chunks if large
        if len(content) > 4000:
            content = content[:4000] + "\n// ... (truncated)"

        messages = [
            {"role": "system", "content": ANALYSIS_PROMPT},
            {"role": "user", "content": f"Analyze this code:\n\n```\n{content}\n```"},
        ]

        try:
            response = await self.complete(messages, "reasoning")
            return {
                "analysis": response.strip(),
                "elapsed_ms": round((time.time() - t0) * 1000),
                "source_length": len(code_or_path),
            }
        except Exception as e:
            return {"analysis": f"Analysis failed: {str(e)[:200]}", "elapsed_ms": 0, "source_length": len(code_or_path)}
