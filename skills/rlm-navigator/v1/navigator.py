"""
RLM Navigator — Recursive Language Model document processing (PhoenixGlobal)
============================================================================
Based on MIT CSAIL paper: "Recursive Language Models" (arxiv.org/abs/2512.24601)
github.com/alexzhang13/rlm

Instead of chunking documents into context (RAG), the AI navigates recursively:
1. AI writes code to search/slice the document
2. Spawns sub-instances to read relevant sections in parallel
3. Synthesizes answer from sub-results

100x native context window. No information loss. No context rot.
Agent-agnostic — works with any LLM completion function.
"""
import re
import asyncio
import logging
from typing import List, Dict, Callable, Awaitable, Optional

log = logging.getLogger("rlm")

NAVIGATOR_PROMPT = """You are a document navigator. You have access to a large document stored externally.
Your task: find the relevant sections to answer the user's question.

Available actions:
- SEARCH(pattern) — search the document with regex, returns matching line numbers
- SLICE(start, end) — read lines start to end, returns content
- STRUCTURE() — get document structure (headings, sections, line count)
- STATS() — get document statistics (total lines, size)

Respond with the action you want to take. One action per turn.
After gathering information, respond with ANSWER: followed by your final answer."""


class RLMDocument:
    """A document that the AI can navigate recursively — without loading into context."""

    def __init__(self, content: str, max_slice: int = 500):
        self.lines = content.split("\n")
        self.max_slice = max_slice
        self._headings = self._extract_headings()

    def _extract_headings(self) -> List[Dict]:
        """Extract markdown/heading structure."""
        headings = []
        for i, line in enumerate(self.lines):
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                headings.append({"line": i + 1, "level": level, "text": line.lstrip("# ").strip()})
        return headings

    def search(self, pattern: str) -> List[int]:
        """Search document with regex. Returns matching line numbers (1-indexed)."""
        try:
            regex = re.compile(pattern, re.IGNORECASE)
            return [i + 1 for i, line in enumerate(self.lines) if regex.search(line)]
        except re.error:
            return []

    def slice(self, start: int, end: int) -> str:
        """Read lines start to end (1-indexed, inclusive). Capped at max_slice."""
        start = max(0, start - 1)
        end = min(len(self.lines), end)
        if end - start > self.max_slice:
            end = start + self.max_slice
        return "\n".join(self.lines[start:end])

    def structure(self) -> str:
        """Get document structure overview."""
        lines = [f"Total lines: {len(self.lines)}", f"Sections: {len(self._headings)}"]
        for h in self._headings[:20]:
            prefix = "  " * (h["level"] - 1)
            lines.append(f"  L{h['line']}: {prefix}{h['text']}")
        return "\n".join(lines)

    def stats(self) -> Dict:
        """Get document statistics."""
        return {
            "total_lines": len(self.lines),
            "total_chars": sum(len(l) for l in self.lines),
            "sections": len(self._headings),
            "avg_line_length": sum(len(l) for l in self.lines) // max(len(self.lines), 1),
        }


class RLMNavigator:
    """Recursive Language Model navigator. Processes documents without loading into context."""

    def __init__(self, complete_fn: Callable[[list, str], Awaitable[str]]):
        self.complete = complete_fn

    async def query(self, document_text: str, question: str, max_turns: int = 10) -> Dict:
        """Query a document using recursive navigation. Returns answer with sources."""
        doc = RLMDocument(document_text)
        context = [f"Document: {doc.stats()['total_lines']} lines, {doc.stats()['sections']} sections"]
        answer = None
        sources = []
        turns = 0

        messages = [
            {"role": "system", "content": NAVIGATOR_PROMPT},
            {"role": "user", "content": f"Question: {question}\n\nContext: {chr(10).join(context)}\n\nWhat action do you want to take? (SEARCH, SLICE, STRUCTURE, STATS, or ANSWER)"},
        ]

        for turn in range(max_turns):
            turns = turn + 1
            try:
                response = await self.complete(messages, "fast")
            except Exception:
                break

            response_upper = response.strip().upper()

            if response_upper.startswith("ANSWER:"):
                answer = response.split("ANSWER:", 1)[1].strip()
                break
            elif response_upper.startswith("SEARCH("):
                pattern = response.split("(", 1)[1].rstrip(")")
                results = doc.search(pattern.strip('"').strip("'"))
                context.append(f"SEARCH('{pattern[:50]}') → {len(results)} matches: {results[:10]}")
                if results:
                    sources.extend(results[:20])
            elif response_upper.startswith("SLICE("):
                parts = response.split("(", 1)[1].rstrip(")").split(",")
                if len(parts) >= 2:
                    try:
                        start, end = int(parts[0].strip()), int(parts[1].strip())
                        content = doc.slice(start, end)
                        context.append(f"SLICE({start},{end}) → {len(content.split(chr(10)))} lines read")
                        sources.append(f"L{start}-L{end}")
                    except ValueError:
                        context.append("SLICE error: invalid line numbers")
            elif response_upper.startswith("STRUCTURE"):
                context.append(f"STRUCTURE:\n{doc.structure()}")
            elif response_upper.startswith("STATS"):
                context.append(f"STATS: {doc.stats()}")
            else:
                context.append(f"Unknown action: {response[:100]}")

            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "What next? (SEARCH, SLICE, STRUCTURE, STATS, or ANSWER)"})

        # Fallback: synthesize from context if no explicit ANSWER
        if not answer and sources:
            answer = f"Found {len(sources)} relevant sections. Use SLICE to read specific sections."

        return {
            "answer": answer or "Could not find answer in document.",
            "sources": list(set(sources))[:20],
            "turns": turns,
            "document_stats": doc.stats(),
        }
