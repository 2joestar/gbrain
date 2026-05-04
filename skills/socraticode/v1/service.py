"""
SocratiCode Auto-Start Service (PhoenixGlobal)
==============================================
Starts SocratiCode MCP server on-demand. Agents call this — no manual steps.
The server starts on first request, stays running, auto-restarts if killed.
"""
import subprocess
import logging
import time
import json
import os
from pathlib import Path

log = logging.getLogger("socraticode-service")

PORT = 8765  # SocratiCode default MCP port
HEALTH_URL = f"http://localhost:{PORT}/health"
PROCESS = None


def _is_running() -> bool:
    """Check if SocratiCode is already running."""
    global PROCESS
    if PROCESS and PROCESS.poll() is None:
        return True
    try:
        import urllib.request
        urllib.request.urlopen(HEALTH_URL, timeout=2)
        return True
    except Exception:
        return False


def _start_server():
    """Start SocratiCode MCP server in background."""
    global PROCESS
    log.info("Starting SocratiCode MCP server...")
    PROCESS = subprocess.Popen(
        ["npx", "-y", "socraticode", "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Wait for it to be ready
    for _ in range(15):
        time.sleep(1)
        if _is_running():
            log.info("SocratiCode MCP server ready on port %d", PORT)
            return True
    log.warning("SocratiCode start timed out")
    return False


def ensure_running() -> bool:
    """Ensure SocratiCode is running. Starts it if not. Returns True if ready."""
    if _is_running():
        return True
    return _start_server()


def search_codebase(query: str, project_path: str = ".") -> dict:
    """Search codebase using SocratiCode. Auto-starts server if needed."""
    if not ensure_running():
        return {"ok": False, "error": "SocratiCode unavailable", "results": []}
    try:
        import urllib.request
        data = json.dumps({"query": query, "path": str(project_path)}).encode()
        req = urllib.request.Request(
            f"http://localhost:{PORT}/search",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"ok": True, "results": json.loads(resp.read())}
    except Exception as e:
        log.warning("search_codebase error: %s", e)
        return {"ok": False, "error": str(e), "results": []}


def stop_server():
    """Stop the SocratiCode server."""
    global PROCESS
    if PROCESS:
        PROCESS.terminate()
        PROCESS = None
