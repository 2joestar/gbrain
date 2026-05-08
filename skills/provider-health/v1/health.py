"""
Shared provider health state — single source of truth for both agent-select (manual)
and Apple-Mamasethu (auto). Both systems read/write to the same backing store.

Run 6 (P-RUN6-1): primary backing store is now SQLite at
  ~/.apple_memory/provider_health.db
with WAL journal mode + BEGIN IMMEDIATE atomic transactions.

Falls back to the legacy JSON file at
  ~/.apple_memory/.provider_health.json
when sqlite3 is unavailable. One-time migration copies the JSON into SQLite on
first run if the DB does not exist yet.

The public API surface is unchanged — _load / _save / _ensure_entry retain
their dict-shaped contract so AdaptiveLearner's debounced read-modify-write
(adaptive.py) keeps working without changes.

Read by:
  - agent-select → shows health scores in provider/model menus
  - Apple ModelRouter → writes success/fail on every API call
  - Apple AdaptiveLearner → reads scores for chain routing
  - Apple Observer → monitors for degraded providers
  - dynamic_chains._sort_by_model_health (Run-5 P-RUN5-2)
  - circuit_breaker (Run-6 P-RUN6-3)

Schema (logical — same shape on both backends):
{
  "providers": {
    "<provider_id>": {
      "success": int,
      "fail": int,
      "total_latency": float,
      "call_count": int,
      "cooldown_until": float|null,
      "last_used": "ISO8601"
    }
  },
  "models": {
    "<full_model_ref>": { same schema }
  },
  "updated": "ISO8601"
}
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Backing store paths ──
HEALTH_DIR = Path.home() / ".apple_memory"
HEALTH_FILE = HEALTH_DIR / ".provider_health.json"  # legacy / fallback
HEALTH_DB = HEALTH_DIR / "provider_health.db"  # primary

COOLDOWN_BASE = 60  # seconds per failure

# ── SQLite availability ──
try:
    import sqlite3  # noqa: F401

    _SQLITE_OK = True
except Exception:
    _SQLITE_OK = False

# Permit forcing JSON-only mode (tests / debugging)
if os.environ.get("APPLE_HEALTH_FORCE_JSON") == "1":
    _SQLITE_OK = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS health (
    key TEXT NOT NULL,
    section TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    fail INTEGER NOT NULL DEFAULT 0,
    total_latency REAL NOT NULL DEFAULT 0.0,
    call_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until REAL,
    last_used TEXT NOT NULL,
    PRIMARY KEY (key, section)
);
CREATE TABLE IF NOT EXISTS health_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated TEXT NOT NULL
);
"""

_SECTIONS = ("providers", "models")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# SQLite backend
# ─────────────────────────────────────────────────────────────────────────────


def _connect():
    """Open a SQLite connection with WAL + sane pragmas. Caller closes."""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    # isolation_level=None → autocommit; we manage txns explicitly via BEGIN IMMEDIATE
    conn = sqlite3.connect(str(HEALTH_DB), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=10000;"
    )
    conn.executescript(_SCHEMA)
    return conn


def _migrate_json_if_needed():
    """One-time copy from legacy JSON into SQLite when DB is fresh."""
    if HEALTH_DB.exists():
        return
    if not HEALTH_FILE.exists():
        return
    try:
        legacy = json.loads(HEALTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(legacy, dict):
        return
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        for section in _SECTIONS:
            for key, entry in (legacy.get(section) or {}).items():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO health
                        (key, section, success, fail, total_latency, call_count, cooldown_until, last_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        section,
                        int(entry.get("success", 0) or 0),
                        int(entry.get("fail", 0) or 0),
                        float(entry.get("total_latency", 0.0) or 0.0),
                        int(entry.get("call_count", 0) or 0),
                        entry.get("cooldown_until"),
                        entry.get("last_used") or _now_iso(),
                    ),
                )
        conn.execute(
            "INSERT OR REPLACE INTO health_meta (id, updated) VALUES (1, ?)",
            (legacy.get("updated") or _now_iso(),),
        )
        conn.execute("COMMIT;")
        logger.info(
            "provider_health: migrated JSON → SQLite (%d providers, %d models)",
            len(legacy.get("providers") or {}),
            len(legacy.get("models") or {}),
        )
    except Exception as e:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        logger.warning("provider_health: JSON→SQLite migration failed: %s", e)
    finally:
        conn.close()


def _sqlite_load() -> dict:
    _migrate_json_if_needed()
    out = {"providers": {}, "models": {}, "updated": _now_iso()}
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT key, section, success, fail, total_latency, call_count, cooldown_until, last_used FROM health"
        )
        for row in cur.fetchall():
            section = row["section"]
            if section not in out:
                continue
            out[section][row["key"]] = {
                "success": int(row["success"]),
                "fail": int(row["fail"]),
                "total_latency": float(row["total_latency"]),
                "call_count": int(row["call_count"]),
                "cooldown_until": row["cooldown_until"],
                "last_used": row["last_used"],
            }
        meta = conn.execute("SELECT updated FROM health_meta WHERE id = 1").fetchone()
        if meta:
            out["updated"] = meta["updated"]
    finally:
        conn.close()
    return out


def _sqlite_save(data: dict):
    """UPSERT every entry from the in-memory dict. Uses BEGIN IMMEDIATE for atomicity."""
    data["updated"] = _now_iso()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        # Collect current keys per section so we can detect deletions (rare, but safe).
        existing = {section: set() for section in _SECTIONS}
        for row in conn.execute("SELECT key, section FROM health").fetchall():
            sec = row["section"]
            if sec in existing:
                existing[sec].add(row["key"])

        for section in _SECTIONS:
            new_keys = set()
            for key, entry in (data.get(section) or {}).items():
                new_keys.add(key)
                conn.execute(
                    """
                    INSERT INTO health
                        (key, section, success, fail, total_latency, call_count, cooldown_until, last_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key, section) DO UPDATE SET
                        success = excluded.success,
                        fail = excluded.fail,
                        total_latency = excluded.total_latency,
                        call_count = excluded.call_count,
                        cooldown_until = excluded.cooldown_until,
                        last_used = excluded.last_used
                    """,
                    (
                        key,
                        section,
                        int(entry.get("success", 0) or 0),
                        int(entry.get("fail", 0) or 0),
                        float(entry.get("total_latency", 0.0) or 0.0),
                        int(entry.get("call_count", 0) or 0),
                        entry.get("cooldown_until"),
                        entry.get("last_used") or _now_iso(),
                    ),
                )
            removed = existing[section] - new_keys
            for key in removed:
                conn.execute(
                    "DELETE FROM health WHERE key = ? AND section = ?", (key, section)
                )

        conn.execute(
            "INSERT INTO health_meta (id, updated) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated = excluded.updated",
            (data["updated"],),
        )
        conn.execute("COMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# JSON fallback (legacy path — preserved verbatim semantics)
# ─────────────────────────────────────────────────────────────────────────────


def _json_load() -> dict:
    try:
        return json.loads(HEALTH_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"providers": {}, "models": {}, "updated": _now_iso()}


def _json_save(data: dict):
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now_iso()
    tmp = HEALTH_FILE.with_suffix(HEALTH_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, HEALTH_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# Public load / save / ensure — backend-agnostic
# ─────────────────────────────────────────────────────────────────────────────


def _load() -> dict:
    if _SQLITE_OK:
        try:
            return _sqlite_load()
        except Exception as e:
            logger.warning(
                "provider_health: SQLite load failed (%s) — falling back to JSON", e
            )
    return _json_load()


def _save(data: dict):
    if _SQLITE_OK:
        try:
            _sqlite_save(data)
            return
        except Exception as e:
            logger.warning(
                "provider_health: SQLite save failed (%s) — falling back to JSON", e
            )
    _json_save(data)


def _ensure_entry(data: dict, section: str, key: str) -> dict:
    if section not in data:
        data[section] = {}
    if key not in data[section]:
        data[section][key] = {
            "success": 0,
            "fail": 0,
            "total_latency": 0.0,
            "call_count": 0,
            "cooldown_until": None,
            "last_used": _now_iso(),
        }
    return data[section][key]


# ── Write operations (called by agent-select after user picks, and by ModelRouter after API calls) ──


def record_success(
    provider_or_model: str, latency: float = None, section: str = "providers"
):
    """Record a successful call. provider_or_model can be a provider ID or full model ref (e.g. 'groq:llama-3.3')"""
    data = _load()
    entry = _ensure_entry(data, section, provider_or_model)
    entry["success"] += 1
    entry["call_count"] += 1
    entry["cooldown_until"] = None  # clear cooldown on success
    entry["last_used"] = _now_iso()
    if latency is not None:
        entry["total_latency"] += latency
    _save(data)


def record_failure(
    provider_or_model: str, latency: float = None, section: str = "providers"
):
    """Record a failed call. Sets cooldown proportional to failure count."""
    data = _load()
    entry = _ensure_entry(data, section, provider_or_model)
    entry["fail"] += 1
    entry["call_count"] += 1
    if latency is not None:
        entry["total_latency"] += latency

    # Progressive cooldown: min(60 * failures, 600) seconds
    failures = entry["fail"]
    cooldown_seconds = min(COOLDOWN_BASE * failures, 600)
    entry["cooldown_until"] = time.time() + cooldown_seconds
    entry["last_used"] = _now_iso()
    _save(data)


# ── Read operations (called by agent-select menus and Apple's chain routing) ──


def is_healthy(provider_or_model: str, section: str = "providers") -> bool:
    """Check if provider/model is currently healthy (not cooling down)."""
    data = _load()
    entry = data[section].get(provider_or_model)
    if not entry:
        return True  # unknown = assume healthy
    cooldown = entry.get("cooldown_until")
    if cooldown is None:
        return True
    return time.time() > cooldown


def get_score(provider_or_model: str, section: str = "providers") -> float:
    """
    Composite score 0.0–1.0 based on success rate + latency.
    Used to sort providers/models in menus and chain routing.
    """
    data = _load()
    entry = data[section].get(provider_or_model)
    if not entry or entry.get("call_count", 0) == 0:
        return 0.5  # neutral for untested

    total = entry["success"] + entry["fail"]
    if total == 0:
        return 0.5

    success_rate = entry["success"] / total
    avg_latency = (
        entry["total_latency"] / max(entry["call_count"], 1)
        if entry["total_latency"] > 0
        else 0.5
    )

    # Weighted composite: 50% success rate, 30% inverse latency, 20% recency
    latency_score = 1.0 / max(avg_latency, 0.001)
    # Cap latency_score at 2.0 to prevent ultra-low-latency from dominating
    latency_score = min(latency_score, 2.0)

    return (success_rate * 0.5) + (latency_score / 2.0 * 0.3) + 0.2


def get_routing_score(
    provider_or_model: str, section: str = "models", latency_weight: float = 0.1
) -> float:
    """
    Run-6 P-RUN6-2 routing-aware score: ``success_rate - latency_weight * avg_latency``.

    Used by chain reorderers and the auto_optimizer for ranking decisions where
    we want raw signal without the menu-friendly 0.5 floor of get_score.
    Untested entries return 0.5 (neutral) so they get a fair shake.
    """
    data = _load()
    entry = data[section].get(provider_or_model)
    if not entry or entry.get("call_count", 0) == 0:
        return 0.5
    total = entry["success"] + entry["fail"]
    if total == 0:
        return 0.5
    success_rate = entry["success"] / total
    avg_latency = (
        entry["total_latency"] / max(entry["call_count"], 1)
        if entry["total_latency"] > 0
        else 0.0
    )
    return success_rate - (latency_weight * avg_latency)


def get_health_icon(provider_or_model: str, section: str = "providers") -> str:
    """Return a display icon for menu use: 🟢 🟡 🔴 ⚪"""
    data = _load()
    entry = data[section].get(provider_or_model)
    if not entry or entry.get("call_count", 0) == 0:
        return "⚪"  # untested

    if not is_healthy(provider_or_model, section):
        return "🔴"  # cooldown

    score = get_score(provider_or_model, section)
    if score >= 0.7:
        return "🟢"
    elif score >= 0.4:
        return "🟡"
    return "🔴"


def get_latency_str(provider_or_model: str, section: str = "providers") -> str:
    """Human-readable latency string like '0.8s'"""
    data = _load()
    entry = data[section].get(provider_or_model)
    if not entry or entry.get("call_count", 0) == 0:
        return "-"
    avg = entry["total_latency"] / max(entry["call_count"], 1)
    if avg < 0.001:
        return f"{avg * 1000:.1f}ms"
    return f"{avg:.1f}s"


def get_all_scores(section: str = "providers") -> dict:
    """Return {key: score} dict for all entries in a section. For sorting menus."""
    data = _load()
    return {k: get_score(k, section) for k in data[section]}


def get_summary() -> dict:
    """Full health report for observer/health checks."""
    data = _load()
    return {
        "providers_total": len(data["providers"]),
        "models_total": len(data["models"]),
        "healthy_providers": [
            k for k in data["providers"] if is_healthy(k, "providers")
        ],
        "degraded_providers": [
            k for k in data["providers"] if not is_healthy(k, "providers")
        ],
        "updated": data.get("updated"),
        "backend": "sqlite" if _SQLITE_OK else "json",
    }
