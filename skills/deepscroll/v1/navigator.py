"""
DeepScroll — LLM-guided navigation for 10M+ token codebases (PhoenixGlobal)
============================================================================
Handles massive codebases by recursively navigating to relevant files
instead of loading everything into context. Agent-agnostic.
"""
import os
import logging
from pathlib import Path
from typing import List, Dict, Callable, Awaitable

log = logging.getLogger("deepscroll")


class DeepScroll:
    """Recursive LLM-guided codebase navigator. Finds relevant files without loading everything."""

    def __init__(self, complete_fn: Callable[[list, str], Awaitable[str]]):
        self.complete = complete_fn

    async def find_relevant(self, query: str, codebase_root: str, max_files: int = 10) -> List[Dict]:
        """Find files relevant to a query by recursively narrowing scope."""
        root = Path(codebase_root)
        if not root.exists():
            return []

        # Phase 1: Get top-level structure
        structure = self._get_structure(root, depth=2)
        
        # Phase 2: LLM-guided narrowing
        messages = [
            {"role": "system", "content": f"You are a codebase navigator. Given this structure and query, list the exact file paths (relative) that are most relevant. Return ONLY paths, one per line.\n\nStructure:\n{structure}"},
            {"role": "user", "content": f"Query: {query}\n\nList the {max_files} most relevant files:"},
        ]
        
        try:
            response = await self.complete(messages, "fast")
            paths = [p.strip() for p in response.split("\n") if p.strip() and not p.startswith("#")]
        except Exception:
            paths = []

        # Phase 3: Read and return relevant files
        results = []
        for path in paths[:max_files]:
            full_path = root / path.lstrip("/")
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    results.append({
                        "path": path,
                        "content": content[:3000],
                        "size": len(content),
                    })
                except Exception:
                    pass

        return results

    def _get_structure(self, root: Path, depth: int = 2) -> str:
        """Get a text representation of the directory structure."""
        lines = []
        def walk(path, current_depth=0):
            if current_depth > depth:
                return
            try:
                for item in sorted(path.iterdir()):
                    if item.name.startswith(".") or item.name == "__pycache__":
                        continue
                    if item.name.endswith((".pyc", ".pyo", ".so", ".o")):
                        continue
                    prefix = "  " * current_depth
                    if item.is_dir():
                        lines.append(f"{prefix}📁 {item.name}/")
                        walk(item, current_depth + 1)
                    else:
                        size = item.stat().st_size
                        lines.append(f"{prefix}📄 {item.name} ({size//1024}KB)")
            except PermissionError:
                pass
        
        walk(root)
        return "\n".join(lines[:200])
