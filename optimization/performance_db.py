"""Lightweight SQLite performance store with WAL mode."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(os.path.expanduser("~/.apple_memory/performance.db"))


class PerformanceDB:
    """Tiny SQLite wrapper for routing, model, strategy, and autonomy history."""

    def __init__(
        self, path: str | os.PathLike[str] | None = None, *, initialize: bool = True
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS routing_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    task_type TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    model TEXT,
                    provider TEXT,
                    latency_ms REAL DEFAULT 0,
                    success INTEGER NOT NULL,
                    cost_usd REAL DEFAULT 0,
                    confidence REAL DEFAULT 0.5,
                    metadata_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS model_performance (
                    model TEXT PRIMARY KEY,
                    provider TEXT,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    total_cost_usd REAL DEFAULT 0,
                    last_updated REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_performance (
                    strategy TEXT PRIMARY KEY,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    total_cost_usd REAL DEFAULT 0,
                    score REAL DEFAULT 0,
                    last_updated REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS autonomy_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    action_type TEXT NOT NULL,
                    target TEXT,
                    reason TEXT,
                    safe INTEGER NOT NULL,
                    applied INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_routing_history_ts
                    ON routing_history(ts);
                CREATE INDEX IF NOT EXISTS idx_routing_history_model
                    ON routing_history(model);
                CREATE INDEX IF NOT EXISTS idx_autonomy_actions_ts
                    ON autonomy_actions(ts);
                """
            )

    async def record_routing_async(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.record_routing, **kwargs)

    def record_routing(
        self,
        *,
        task_type: str,
        chain: str,
        success: bool,
        model: str = "",
        provider: str = "",
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        confidence: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO routing_history
                (ts, task_type, chain, model, provider, latency_ms, success, cost_usd, confidence, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    task_type,
                    chain,
                    model,
                    provider,
                    latency_ms,
                    int(success),
                    cost_usd,
                    confidence,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
            if model:
                self.record_model_performance(
                    model=model,
                    provider=provider,
                    success=success,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    connection=connection,
                )

    def record_model_performance(
        self,
        *,
        model: str,
        success: bool,
        provider: str = "",
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        active_connection = connection or self._connect()
        try:
            active_connection.execute(
                """
                INSERT INTO model_performance
                (model, provider, success_count, failure_count, total_latency_ms, total_cost_usd, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model) DO UPDATE SET
                    provider=excluded.provider,
                    success_count=success_count + excluded.success_count,
                    failure_count=failure_count + excluded.failure_count,
                    total_latency_ms=total_latency_ms + excluded.total_latency_ms,
                    total_cost_usd=total_cost_usd + excluded.total_cost_usd,
                    last_updated=excluded.last_updated
                """,
                (
                    model,
                    provider,
                    int(success),
                    int(not success),
                    latency_ms,
                    cost_usd,
                    time.time(),
                ),
            )
        finally:
            if owns_connection:
                active_connection.close()

    def record_strategy_performance(
        self,
        *,
        strategy: str,
        success: bool,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        score: float = 0.0,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_performance
                (strategy, success_count, failure_count, total_latency_ms, total_cost_usd, score, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy) DO UPDATE SET
                    success_count=success_count + excluded.success_count,
                    failure_count=failure_count + excluded.failure_count,
                    total_latency_ms=total_latency_ms + excluded.total_latency_ms,
                    total_cost_usd=total_cost_usd + excluded.total_cost_usd,
                    score=excluded.score,
                    last_updated=excluded.last_updated
                """,
                (
                    strategy,
                    int(success),
                    int(not success),
                    latency_ms,
                    cost_usd,
                    score,
                    time.time(),
                ),
            )

    def record_autonomy_action(
        self,
        *,
        action_type: str,
        target: str = "",
        reason: str = "",
        safe: bool = True,
        applied: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO autonomy_actions
                (ts, action_type, target, reason, safe, applied, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    action_type,
                    target,
                    reason,
                    int(safe),
                    int(applied),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )

    def recent_routing(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ts, task_type, chain, model, provider, latency_ms, success, cost_usd, confidence, metadata_json
                FROM routing_history
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def model_stats(self) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM model_performance").fetchall()
        return {str(row["model"]): dict(row) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
