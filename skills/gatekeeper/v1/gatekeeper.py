import re
import time
from collections import deque

import logging as _logging


def _log_fallback(event: str, data: dict = None) -> None:
    _logging.getLogger("gatekeeper").debug("%s %s", event, data or {})


def _load_cfg_fallback(section: str) -> dict:
    return {}


try:
    from ..utils.logger import log
    from ..registry.manager import load as load_cfg
except ImportError:
    log = _log_fallback
    load_cfg = _load_cfg_fallback


class Gatekeeper:
    """Hybrid governance gate.
    Low-risk → auto-approve.
    Medium-risk → validate content.
    High-risk → require explicit approval flag.
    """

    HIGH_RISK = {
        "system_modify",
        "api_key_change",
        "tool_upgrade",
        "model_upgrade",
        "service_restart",
        "file_delete",
    }
    MEDIUM_RISK = {
        "memory_write",
        "tool_execute",
        "file_write",
        "config_change",
        "agent_spawn",
    }

    def __init__(self):
        self._cfg = {}
        self._recent = deque(maxlen=100)
        self._blocked = 0
        self._approved = 0
        self._loop_counts: dict = {}

    def _cfg_val(self, key: str, default):
        if not self._cfg:
            self._cfg = load_cfg("system").get("gatekeeper", {})
        return self._cfg.get(key, default)

    def assess_risk(self, action: dict) -> str:
        t = action.get("type", "")
        if t in self.HIGH_RISK:
            return "high"
        if t in self.MEDIUM_RISK:
            return "medium"
        return "low"

    def detect_loop(self, action: dict) -> bool:
        key = f"{action.get('type', '?')}:{str(action.get('input', ''))[:60]}"
        now = time.time()
        threshold = self._cfg_val("loop_threshold", 3)
        window = self._cfg_val("loop_detection_window", 30)

        recent_same = sum(1 for ts, k in self._recent if k == key and now - ts < window)
        self._recent.append((now, key))

        if recent_same >= threshold:
            log("gatekeeper.loop_detected", {"key": key[:80], "count": recent_same})
            return True
        return False

    def check_hallucination(self, text: str) -> bool:
        if not text:
            return True
        min_len = self._cfg_val("hallucination_min_length", 5)
        if len(text.strip()) < min_len:
            return True
        filler_patterns = [
            r"task executed successfully",
            r"i have (completed|processed|done)",
            r"successfully (processed|completed|executed)",
            r"^ok\.?$",
            r"lorem ipsum",
        ]
        t = text.lower().strip()
        for p in filler_patterns:
            if re.search(p, t):
                log("gatekeeper.hallucination", {"snippet": text[:100]})
                return True
        return False

    def validate_memory_write(self, content: str) -> bool:
        if not content or len(content.strip()) < 5:
            return False
        if len(content) > 100_000:
            log("gatekeeper.memory_oversized", {"size": len(content)})
            return False
        return True

    def validate_output(self, response: str) -> tuple:
        if self.check_hallucination(response):
            return False, "hallucination_detected"
        if len(response) > 50_000:
            return False, "response_too_large"
        return True, "ok"

    def validate(self, action: dict) -> bool:
        if self.detect_loop(action):
            self._blocked += 1
            return False

        risk = self.assess_risk(action)
        atype = action.get("type", "")

        if risk == "low":
            self._approved += 1
            return True

        if risk == "medium":
            ok = True
            if atype == "memory_write":
                ok = self.validate_memory_write(action.get("content", ""))
            if ok:
                self._approved += 1
            else:
                self._blocked += 1
            return ok

        if risk == "high":
            if action.get("approved") is True:
                log("gatekeeper.high_risk_approved", {"type": atype})
                self._approved += 1
                return True
            log("gatekeeper.high_risk_blocked", {"type": atype})
            self._blocked += 1
            return False

        return True

    def stats(self) -> dict:
        return {
            "approved": self._approved,
            "blocked": self._blocked,
            "recent_actions": len(self._recent),
        }
