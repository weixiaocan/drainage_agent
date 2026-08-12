from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

Decision = Literal["allow", "ask", "deny"]
FINAL_STATUSES = {"succeeded", "failed", "timed_out"}


class InvalidExecutionTransition(ValueError):
    pass


@dataclass(frozen=True)
class PythonExecutionRequest:
    request_id: str
    project_id: str
    batch_id: str
    session_id: str
    run_id: str
    purpose: str
    code: str
    code_sha256: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    overwrite: bool
    policy_decision: str
    policy_reasons: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    approved_capabilities: tuple[str, ...]
    affected_paths: tuple[str, ...]
    status: str
    created_at: str
    expires_at: str | None
    approved_at: str | None
    started_at: str | None
    finished_at: str | None
    stdout: str
    stderr: str
    exit_code: int | None
    error: str | None
    artifacts: tuple[dict[str, object], ...]
    input_snapshot_id: str | None
    sandbox_image_digest: str | None


class PythonExecutionRequestRepository:
    """Persist approval-bound requests and enforce their state machine."""

    def __init__(self, database: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS python_execution_requests (
                    request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    purpose TEXT NOT NULL, code TEXT NOT NULL, code_sha256 TEXT NOT NULL,
                    inputs_json TEXT NOT NULL DEFAULT '[]', outputs_json TEXT NOT NULL DEFAULT '[]',
                    overwrite INTEGER NOT NULL DEFAULT 0,
                    policy_decision TEXT NOT NULL, policy_reasons_json TEXT NOT NULL,
                    requested_capabilities_json TEXT NOT NULL,
                    approved_capabilities_json TEXT NOT NULL, affected_paths_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT,
                    approved_at TEXT, started_at TEXT, finished_at TEXT,
                    stdout TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
                    exit_code INTEGER, error TEXT, artifacts_json TEXT NOT NULL DEFAULT '[]',
                    input_snapshot_id TEXT, sandbox_image_digest TEXT
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(python_execution_requests)")}
            for name, declaration in (
                ("inputs_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("outputs_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("overwrite", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE python_execution_requests ADD COLUMN {name} {declaration}")
            connection.execute("""CREATE INDEX IF NOT EXISTS idx_python_execution_scope
                ON python_execution_requests(project_id, batch_id, session_id)""")

    def create(self, *, project_id: str, batch_id: str, session_id: str, run_id: str,
               purpose: str, code: str, policy_decision: Decision,
               inputs: tuple[str, ...] | list[str] = (),
               outputs: tuple[str, ...] | list[str] = (),
               overwrite: bool = False,
               policy_reasons: tuple[str, ...] | list[str] = (),
               requested_capabilities: tuple[str, ...] | list[str] = (),
               affected_paths: tuple[str, ...] | list[str] = (),
               approval_ttl: timedelta = timedelta(minutes=15),
               request_id: str | None = None) -> PythonExecutionRequest:
        if policy_decision not in {"allow", "ask", "deny"}:
            raise ValueError("unknown policy decision")
        now = self._now()
        status = {"allow": "approved_automatically", "ask": "awaiting_approval", "deny": "denied"}[policy_decision]
        expires = now + approval_ttl if policy_decision == "ask" else None
        approved = requested_capabilities if policy_decision == "allow" else []
        request_id = request_id or uuid.uuid4().hex
        values = (request_id, project_id, batch_id, session_id, run_id, purpose, code,
                  self.hash_code(code), policy_decision, self._json(policy_reasons),
                  self._json(requested_capabilities), self._json(approved),
                  self._json(affected_paths), self._json(inputs), self._json(outputs),
                  int(overwrite), status, now.isoformat(),
                  expires.isoformat() if expires else None)
        with self._connect() as connection:
            connection.execute("""INSERT INTO python_execution_requests (
                request_id,project_id,batch_id,session_id,run_id,purpose,code,code_sha256,
                policy_decision,policy_reasons_json,requested_capabilities_json,
                approved_capabilities_json,affected_paths_json,inputs_json,outputs_json,overwrite,
                status,created_at,expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        return self.required(request_id)

    def approve(self, request_id: str, *, project_id: str, batch_id: str, session_id: str,
                code_sha256: str,
                approved_capabilities: tuple[str, ...] | list[str]) -> PythonExecutionRequest:
        request = self.required(request_id)
        self._assert_binding(request, project_id, batch_id, session_id, code_sha256)
        if request.status != "awaiting_approval":
            raise InvalidExecutionTransition("request is not awaiting approval")
        if request.expires_at and datetime.fromisoformat(request.expires_at) <= self._now():
            self._transition(request_id, "awaiting_approval", "expired", finished=True)
            raise InvalidExecutionTransition("request approval has expired")
        if not set(approved_capabilities) <= set(request.requested_capabilities):
            raise ValueError("approval cannot grant unrequested capabilities")
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE python_execution_requests
                SET status='approved', approved_capabilities_json=?, approved_at=?
                WHERE request_id=? AND status='awaiting_approval'""",
                (self._json(sorted(approved_capabilities)), self._now().isoformat(), request_id))
        self._changed(cursor, "request approval was already consumed")
        return self.required(request_id)

    def reject(self, request_id: str) -> PythonExecutionRequest:
        self._transition(request_id, "awaiting_approval", "rejected", finished=True)
        return self.required(request_id)

    def start(self, request_id: str, *, project_id: str, batch_id: str, session_id: str,
              code_sha256: str, input_snapshot_id: str,
              sandbox_image_digest: str) -> PythonExecutionRequest:
        request = self.required(request_id)
        self._assert_binding(request, project_id, batch_id, session_id, code_sha256)
        if request.status not in {"approved", "approved_automatically"}:
            raise InvalidExecutionTransition("request is not approved for execution")
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE python_execution_requests SET status='running',
                started_at=?, input_snapshot_id=?, sandbox_image_digest=?
                WHERE request_id=? AND status=?""",
                (self._now().isoformat(), input_snapshot_id, sandbox_image_digest,
                 request_id, request.status))
        self._changed(cursor, "request was already started")
        return self.required(request_id)

    def finish(self, request_id: str, *, status: str, stdout: str = "", stderr: str = "",
               exit_code: int | None = None, error: str | None = None,
               artifacts: tuple[dict[str, object], ...] | list[dict[str, object]] = ()) -> PythonExecutionRequest:
        if status not in FINAL_STATUSES:
            raise ValueError("invalid terminal status")
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE python_execution_requests SET status=?,
                finished_at=?,stdout=?,stderr=?,exit_code=?,error=?,artifacts_json=?
                WHERE request_id=? AND status='running'""",
                (status, self._now().isoformat(), stdout, stderr, exit_code, error,
                 self._json(artifacts), request_id))
        self._changed(cursor, "only a running request can finish")
        return self.required(request_id)

    def expire_pending(self) -> int:
        now = self._now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE python_execution_requests
                SET status='expired',finished_at=? WHERE status='awaiting_approval'
                AND expires_at<=?""", (now, now))
        return cursor.rowcount

    def get(self, request_id: str) -> PythonExecutionRequest | None:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM python_execution_requests WHERE request_id=?",
                                     (request_id,)).fetchone()
        return self._decode(row) if row else None

    def for_run(self, run_id: str, project_id: str, batch_id: str) -> PythonExecutionRequest | None:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """SELECT * FROM python_execution_requests
                WHERE run_id=? AND project_id=? AND batch_id=?
                ORDER BY created_at DESC LIMIT 1""",
                (run_id, project_id, batch_id),
            ).fetchone()
        return self._decode(row) if row else None

    def required(self, request_id: str) -> PythonExecutionRequest:
        request = self.get(request_id)
        if request is None:
            raise LookupError("python execution request not found")
        return request

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def _transition(self, request_id: str, source: str, target: str, *, finished: bool) -> None:
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE python_execution_requests SET status=?,finished_at=?
                WHERE request_id=? AND status=?""",
                (target, self._now().isoformat() if finished else None, request_id, source))
        self._changed(cursor, f"expected request status {source}")

    @staticmethod
    def _assert_binding(request: PythonExecutionRequest, project_id: str, batch_id: str,
                        session_id: str, code_sha256: str) -> None:
        if (request.project_id, request.batch_id, request.session_id, request.code_sha256) != (
                project_id, batch_id, session_id, code_sha256):
            raise InvalidExecutionTransition("approval binding does not match request context")

    @staticmethod
    def _changed(cursor: sqlite3.Cursor, message: str) -> None:
        if cursor.rowcount != 1:
            raise InvalidExecutionTransition(message)

    def _now(self) -> datetime:
        value = self.clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(row: sqlite3.Row) -> PythonExecutionRequest:
        values = dict(row)
        for source, target in (("policy_reasons_json", "policy_reasons"),
                               ("requested_capabilities_json", "requested_capabilities"),
                               ("approved_capabilities_json", "approved_capabilities"),
                               ("affected_paths_json", "affected_paths"),
                               ("inputs_json", "inputs"),
                               ("outputs_json", "outputs"),
                               ("artifacts_json", "artifacts")):
            values[target] = tuple(json.loads(values.pop(source)))
        values["overwrite"] = bool(values["overwrite"])
        return PythonExecutionRequest(**values)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, timeout=30)
