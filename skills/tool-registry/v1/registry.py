import logging as _logging


def _log_fallback(event, data=None):
    _logging.getLogger("tools").debug("%s %s", event, data or {})


try:
    from ..utils.logger import log
except ImportError:
    log = _log_fallback


class ToolRegistry:
    def __init__(self):
        self._tools: dict = {}
        self._load_builtins()

    def _load_builtins(self):
        loaders = {
            "web_search": ("apple.tools.builtins.web_search", "WebSearchTool"),
            "url_fetch": ("apple.tools.builtins.url_fetch", "URLFetchTool"),
            "system_status": ("apple.tools.builtins.system_status", "SystemStatusTool"),
        }
        for tool_id, (mod_path, cls_name) in loaders.items():
            try:
                mod = __import__(mod_path, fromlist=[cls_name])
                cls = getattr(mod, cls_name)
                self._tools[tool_id] = cls()
                log("tool_loaded", {"tool": tool_id})
            except Exception as e:
                log("tool_load_failed", {"tool": tool_id, "error": str(e)})

    def register(self, tool_id: str, tool):
        self._tools[tool_id] = tool
        log("tool_registered", {"tool": tool_id})

    def get_tools(self) -> dict:
        return dict(self._tools)

    def get(self, tool_id: str):
        return self._tools.get(tool_id)
