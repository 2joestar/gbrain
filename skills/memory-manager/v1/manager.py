import json
import time
import hashlib
from pathlib import Path
from typing import List
import logging as _logging


def _log_fallback(event, data=None):
    _logging.getLogger("memory").debug("%s %s", event, data or {})


def _load_cfg_fallback(section):
    return {}


try:
    from ..utils.logger import log
    from ..registry.manager import load as load_cfg
except ImportError:
    log = _log_fallback
    load_cfg = _load_cfg_fallback


class MemoryManager:
    """3-tier memory: working (RAM) → episodic/semantic/procedural (disk).
    All writes are validated by Gatekeeper.
    """

    def __init__(self, vault_path: str, gatekeeper=None, **_deprecated):
        # Back-compat: hermes= alias for gatekeeper= (removed in Run 5)
        if gatekeeper is None and "hermes" in _deprecated:
            gatekeeper = _deprecated["hermes"]
        self.vault = Path(vault_path)
        self.gatekeeper = gatekeeper
        self._cfg = {}
        self._working: dict = {}
        self._seen: set = set()

        for tier in ["working", "episodic", "semantic", "procedural", "candidates"]:
            (self.vault / "memory" / tier).mkdir(parents=True, exist_ok=True)

        # Hybrid memory: graph store
        try:
            from ..core.memory.graph_store import GraphStore

            self.graph = GraphStore()
        except Exception:
            self.graph = None

        self._load_seen_hashes()

    def _cfg_val(self, key, default):
        if not self._cfg:
            self._cfg = load_cfg("system").get("memory", {})
        return self._cfg.get(key, default)

    def _load_seen_hashes(self):
        """Pre-load hashes of existing memories for deduplication."""
        window = self._cfg_val("dedup_window", 1000)
        count = 0
        for tier in ["episodic", "semantic", "procedural"]:
            path = self.vault / "memory" / tier
            files = sorted(
                path.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True
            )
            for f in files[:window]:
                try:
                    data = json.loads(f.read_text())
                    h = hashlib.sha256(data.get("content", "").encode()).hexdigest()[
                        :16
                    ]
                    self._seen.add(h)
                    count += 1
                except Exception:
                    pass
        log("memory_hashes_loaded", {"count": count})

    def _is_duplicate(self, content: str) -> bool:
        h = hashlib.sha256(content.encode()).hexdigest()[:16]
        if h in self._seen:
            return True
        self._seen.add(h)
        # Keep set bounded
        if len(self._seen) > self._cfg_val("dedup_window", 1000) * 2:
            self._seen = set(list(self._seen)[-self._cfg_val("dedup_window", 1000) :])
        return False

    async def write(
        self, content: str, memory_type: str = "episodic", metadata: dict = None
    ):
        if not content or len(content.strip()) < 5:
            return
        if self._is_duplicate(content):
            return

        action = {"type": "memory_write", "content": content}
        if self.gatekeeper and not self.gatekeeper.validate(action):
            return

        entry = {
            "content": content,
            "type": memory_type,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        h = hashlib.sha256(content.encode()).hexdigest()[:8]
        filename = f"{int(time.time())}_{h}.json"
        path = self.vault / "memory" / memory_type / filename
        path.write_text(json.dumps(entry, indent=2))
        log("memory_written", {"type": memory_type, "len": len(content)})

        # Hybrid: extract and store graph triplets
        if self.graph is not None:
            triplet = extract_triplet(content)
            if triplet:
                try:
                    self.graph.add_triplet(*triplet)
                except Exception:
                    pass

    def search(self, query: str, memory_type: str = None, limit: int = 5) -> List[dict]:
        """Search memory entries for specific terms or patterns."""
        results = []
        tiers = [memory_type] if memory_type else ["episodic", "semantic", "procedural"]
        query_terms = set(query.lower().split()) if query else set()

        for tier in tiers:
            tier_path = self.vault / "memory" / tier
            files = sorted(
                tier_path.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for f in files[: limit * 3]:
                try:
                    entry = json.loads(f.read_text())
                    if query_terms:
                        content = entry.get("content", "").lower()
                        if any(t in content for t in query_terms):
                            results.append(entry)
                    else:
                        results.append(entry)
                    if len(results) >= limit:
                        return results
                except Exception:
                    pass
        return results[:limit]

    def hybrid_search(self, query: str):
        """Hybrid search with source weighting: keyword(3), graph(2), semantic(1).
        Returns [(result, source, score)] sorted by score desc, capped at 5."""

        scored = []
        seen = set()

        # 1. Keyword results — weight 3
        for res in self.recall(query, limit=10):
            key = res.get("content", "") if isinstance(res, dict) else str(res)
            if key not in seen:
                seen.add(key)
                scored.append((res, "keyword", 3))

        # 2. Graph relationships + multi-hop — weight 2
        if self.graph is not None:
            for res in self.graph.multi_hop_search(query, depth=2):
                key = res.get("content", "") if isinstance(res, dict) else str(res)
                if key not in seen:
                    seen.add(key)
                    scored.append((res, "graph", 2))

        # 3. Semantic similarity — weight 1 (placeholder)
        for res in []:  # vector_store.search(embed(query)) would go here
            key = res.get("content", "") if isinstance(res, dict) else str(res)
            if key not in seen:
                seen.add(key)
                scored.append((res, "semantic", 1))

        # sort by score desc, then trim
        scored.sort(key=lambda x: x[2], reverse=True)
        return [(r, src, s) for (r, src, s) in scored[:5]]

    def recall(
        self, query: str = None, memory_type: str = None, limit: int = 5
    ) -> List[dict]:
        results = []
        tiers = [memory_type] if memory_type else ["episodic", "semantic", "procedural"]
        query_terms = set(query.lower().split()) if query else set()

        for tier in tiers:
            tier_path = self.vault / "memory" / tier
            files = sorted(
                tier_path.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for f in files[: limit * 3]:
                try:
                    entry = json.loads(f.read_text())
                    if query_terms:
                        content = entry.get("content", "").lower()
                        if any(t in content for t in query_terms):
                            results.append(entry)
                    else:
                        results.append(entry)
                    if len(results) >= limit:
                        return results
                except Exception:
                    pass
        return results[:limit]

    def get_context(self, query: str = None, max_chars: int = 1500) -> str:
        memories = (
            self.hybrid_search(query)
            if hasattr(self, "hybrid_search")
            else self.recall(query, limit=6)
        )
        if not memories:
            return ""
        parts = []
        total = 0
        for m in memories:
            c = m.get("content", "")[:300]
            if total + len(c) > max_chars:
                break
            parts.append(c)
            total += len(c)
        return "\n---\n".join(parts)

    def write_working(self, key: str, value, ttl: int = 3600):
        self._working[key] = {"value": value, "expires": time.time() + ttl}

    def read_working(self, key: str):
        entry = self._working.get(key)
        if not entry:
            return None
        if time.time() > entry["expires"]:
            del self._working[key]
            return None
        return entry["value"]

    def expire_working(self):
        now = time.time()
        self._working = {k: v for k, v in self._working.items() if v["expires"] > now}

        # Hybrid: periodically clean graph
        if self.graph is not None:
            try:
                # remove isolated nodes
                to_remove = [n for n in self.graph.nodes if not self.graph.nodes[n]]
                for n in to_remove:
                    del self.graph.nodes[n]
            except Exception:
                pass
