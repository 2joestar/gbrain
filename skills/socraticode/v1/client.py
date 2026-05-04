"""
SocratiCode Integration — Codebase intelligence for all agents (PhoenixGlobal)
==============================================================================
github.com/giancarloerra/SocratiCode (1951⭐)
Provides semantic search, dependency graphs, and impact analysis.
Accessed via npx — no install needed.
Agent-agnostic — any agent can call search_codebase().
"""
import subprocess
import logging
from typing import List, Dict, Optional

log = logging.getLogger("socraticode")


def search_codebase(query: str, project_path: str = ".") -> Dict:
    """
    Search the codebase using SocratiCode.
    Returns relevant files, symbols, and dependencies.
    Falls back gracefully if SocratiCode is unavailable.
    """
    try:
        result = subprocess.run(
            ["npx", "-y", "socraticode", "search", query, "--path", project_path, "--json"],
            capture_output=True, text=True, timeout=30,
            cwd=project_path,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            return {"ok": True, "results": json.loads(result.stdout), "source": "socraticode"}
    except FileNotFoundError:
        log.info("SocratiCode not available (npx missing)")
    except subprocess.TimeoutExpired:
        log.warning("SocratiCode timed out")
    except Exception as e:
        log.warning("SocratiCode error: %s", e)

    return {"ok": False, "results": [], "source": "fallback", "error": "SocratiCode unavailable"}


def analyze_impact(file_path: str) -> Dict:
    """Analyze the impact of changes to a file."""
    try:
        result = subprocess.run(
            ["npx", "-y", "socraticode", "impact", file_path, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            return {"ok": True, "analysis": json.loads(result.stdout), "source": "socraticode"}
    except Exception as e:
        log.warning("SocratiCode impact error: %s", e)
    return {"ok": False, "analysis": {}, "source": "fallback"}


def is_available() -> bool:
    """Check if SocratiCode can be invoked."""
    try:
        result = subprocess.run(
            ["npx", "-y", "socraticode", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False
