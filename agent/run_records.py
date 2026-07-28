from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from agent.core.logging_utils import _trace_safe


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    project_id: str
    batch_id: str
    session_id: str
    job_id: str | None
    model: str
    status: str
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    error: str | None
    reply_summary: str | None
    debug: bool


class RunRecorder:
    """Persist safe, queryable Agent run and step summaries."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._started: dict[str, float] = {}
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    job_id TEXT,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    error TEXT,
                    reply_summary TEXT,
                    debug INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_run_steps (
                    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    tool_name TEXT,
                    status TEXT,
                    duration_ms INTEGER,
                    token_json TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    error TEXT,
                    artifacts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs (run_id)
                )
                """
            )

    def start(
        self,
        *,
        run_id: str,
        project_id: str,
        batch_id: str,
        session_id: str,
        model: str,
        job_id: str | None = None,
        debug: bool = False,
    ) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            self._started[run_id] = monotonic()
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, project_id, batch_id, session_id, job_id,
                    model, status, started_at, debug
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    batch_id,
                    session_id,
                    job_id,
                    model,
                    started_at,
                    int(debug),
                ),
            )

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        reply: str | None = None,
        usage: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        usage = usage or {}
        with self._lock:
            started = self._started.pop(run_id, None)
        duration_ms = (
            round((monotonic() - started) * 1000) if started is not None else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_runs SET
                    status = ?, finished_at = ?, duration_ms = ?,
                    input_tokens = ?, output_tokens = ?, total_tokens = ?,
                    error = ?, reply_summary = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    duration_ms,
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                    self._summary(error),
                    self._summary(reply),
                    run_id,
                ),
            )
    def write(self, event: dict[str, Any]) -> None:
        safe = _trace_safe(event)
        run_id = safe.get("run_id")
        if not run_id or safe.get("event") in {"turn_start", "turn_end"}:
            return
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                return
            connection.execute(
                """
                INSERT INTO agent_run_steps (
                    run_id, event, tool_name, status, duration_ms,
                    token_json, args_json, error, artifacts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(safe.get("event", "event")),
                    safe.get("tool_name"),
                    safe.get("status"),
                    safe.get("duration_ms"),
                    json.dumps(safe.get("usage", {}), ensure_ascii=False),
                    json.dumps(safe.get("args", {}), ensure_ascii=False),
                    self._summary(safe.get("error")),
                    json.dumps(safe.get("artifacts", []), ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if safe.get("job_id"):
                connection.execute(
                    "UPDATE agent_runs SET job_id = ? WHERE run_id = ?",
                    (str(safe["job_id"]), run_id),
                )

    def list(
        self, project_id: str, batch_id: str, *, limit: int = 100
    ) -> list[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, project_id, batch_id, session_id, job_id,
                       model, status, started_at, finished_at, duration_ms,
                       input_tokens, output_tokens, total_tokens, error,
                       reply_summary, debug
                FROM agent_runs
                WHERE project_id = ? AND batch_id = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (project_id, batch_id, max(1, min(limit, 500))),
            ).fetchall()
        return [RunRecord(*row[:-1], bool(row[-1])) for row in rows]

    def get(
        self, project_id: str, batch_id: str, run_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            record_row = connection.execute(
                """
                SELECT run_id, project_id, batch_id, session_id, job_id,
                       model, status, started_at, finished_at, duration_ms,
                       input_tokens, output_tokens, total_tokens, error,
                       reply_summary, debug
                FROM agent_runs
                WHERE project_id = ? AND batch_id = ? AND run_id = ?
                """,
                (project_id, batch_id, run_id),
            ).fetchone()
            if record_row is None:
                return None
            rows = connection.execute(
                """
                SELECT step_id, event, tool_name, status, duration_ms,
                       token_json, args_json, error, artifacts_json, created_at
                FROM agent_run_steps
                WHERE run_id = ? ORDER BY step_id
                """,
                (run_id,),
            ).fetchall()
        steps = [
            {
                "step_id": row[0],
                "event": row[1],
                "tool_name": row[2],
                "status": row[3],
                "duration_ms": row[4],
                "usage": json.loads(row[5]),
                "args": json.loads(row[6]),
                "error": row[7],
                "artifacts": json.loads(row[8]),
                "created_at": row[9],
            }
            for row in rows
        ]
        record = RunRecord(*record_row[:-1], bool(record_row[-1]))
        return {**asdict(record), "steps": steps}

    @staticmethod
    def _summary(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if len(text) <= 2000 else text[:2000] + "...<truncated>"

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)
