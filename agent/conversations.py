from __future__ import annotations

import json
import sqlite3
import uuid
from copy import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.deps import AgentDeps, Paths, SessionState


@dataclass(frozen=True)
class ConversationTurn:
    session_id: str
    run_id: str
    project_id: str
    batch_id: str
    reply: str


class ConversationRepository:
    """Persist project-scoped chat history and deterministic session state."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def load(
        self,
        session_id: str,
        project_id: str,
        batch_id: str,
    ) -> tuple[list[Any], SessionState]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT project_id, batch_id, history_json, state_json
                FROM agent_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return [], SessionState()
        if row[0] != project_id or row[1] != batch_id:
            raise ValueError("会话已绑定其他监测项目或分析批次")
        return self._decode_history(row[2]), self._decode_state(row[3])

    def save(
        self,
        session_id: str,
        project_id: str,
        batch_id: str,
        history: list[Any],
        state: SessionState,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    session_id, project_id, batch_id, history_json,
                    state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (session_id) DO UPDATE SET
                    history_json = excluded.history_json,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                WHERE agent_sessions.project_id = excluded.project_id
                  AND agent_sessions.batch_id = excluded.batch_id
                """,
                (
                    session_id,
                    project_id,
                    batch_id,
                    self._encode_history(history),
                    json.dumps(asdict(state), ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )

    @staticmethod
    def _encode_history(history: list[Any]) -> str:
        if all(isinstance(message, dict) for message in history):
            return json.dumps(history, ensure_ascii=False, default=str)
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            return ModelMessagesTypeAdapter.dump_json(history).decode("utf-8")
        except (ImportError, TypeError, ValueError):
            return json.dumps(history, ensure_ascii=False, default=str)

    @staticmethod
    def _decode_history(value: str) -> list[Any]:
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            return list(ModelMessagesTypeAdapter.validate_json(value))
        except (ImportError, TypeError, ValueError):
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []

    @staticmethod
    def _decode_state(value: str) -> SessionState:
        fields = SessionState.__dataclass_fields__
        decoded = json.loads(value)
        return SessionState(
            **{key: item for key, item in decoded.items() if key in fields}
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)


class ConversationRunner:
    """Run one Agent turn against an isolated project and batch context."""

    def __init__(
        self,
        repository: ConversationRepository,
        agent: Any,
        base_deps: AgentDeps,
        files_root: Path,
        run_recorder: Any,
    ) -> None:
        self.repository = repository
        self.agent = agent
        self.base_deps = base_deps
        self.files_root = files_root.resolve()
        self.run_recorder = run_recorder

    def run(
        self,
        *,
        project_id: str,
        batch_id: str,
        message: str,
        session_id: str | None = None,
        debug: bool = False,
    ) -> ConversationTurn:
        session_id = session_id or uuid.uuid4().hex
        history, session_state = self.repository.load(
            session_id, project_id, batch_id
        )
        run_id = uuid.uuid4().hex
        deps = self._scoped_deps(project_id, batch_id, session_state, run_id)
        self.run_recorder.start(
            run_id=run_id,
            project_id=project_id,
            batch_id=batch_id,
            session_id=session_id,
            model=deps.settings.model,
            debug=debug,
        )
        if debug:
            self.run_recorder.write(
                {
                    "event": "debug_input",
                    "run_id": run_id,
                    "args": {"message": message},
                }
            )
        try:
            result = self.agent.run_sync(
                message,
                deps=deps,
                message_history=history,
            )
            reply = self._result_text(result)
            new_history = (
                list(result.all_messages())
                if hasattr(result, "all_messages")
                else history
            )
            self.repository.save(
                session_id,
                project_id,
                batch_id,
                new_history,
                deps.session,
            )
            usage = self._usage(result)
            if debug:
                self.run_recorder.write(
                    {
                        "event": "debug_output",
                        "run_id": run_id,
                        "args": {"reply": reply},
                    }
                )
            self.run_recorder.finish(
                run_id,
                status="succeeded",
                reply=reply,
                usage=usage,
            )
            return ConversationTurn(
                session_id=session_id,
                run_id=run_id,
                project_id=project_id,
                batch_id=batch_id,
                reply=reply,
            )
        except Exception as exc:
            self.run_recorder.finish(
                run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            deps.session.current_run_id = None

    def _scoped_deps(
        self,
        project_id: str,
        batch_id: str,
        session: SessionState,
        run_id: str,
    ) -> AgentDeps:
        batch_root = self.files_root / project_id / "batches" / batch_id
        scoped = copy(self.base_deps)
        scoped.paths = Paths(
            root=batch_root,
            data=batch_root / "inputs",
            outputs=batch_root / "results",
            workspace=batch_root / "sessions",
            logs=self.base_deps.paths.logs,
            templates=batch_root / "inputs" / "templates",
            notes=self.base_deps.paths.notes,
        )
        scoped.session = session
        scoped.session.current_run_id = run_id
        scoped.current_project_id = project_id
        scoped.current_batch_id = batch_id
        scoped.trace = self.run_recorder
        return scoped

    @staticmethod
    def _result_text(result: Any) -> str:
        for attr in ("output", "data"):
            if hasattr(result, attr):
                value = getattr(result, attr)
                return value() if callable(value) else str(value)
        return str(result)

    @staticmethod
    def _usage(result: Any) -> dict[str, int]:
        if not hasattr(result, "usage"):
            return {}
        usage = result.usage()
        result_usage: dict[str, int] = {}
        for source, target in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = getattr(usage, source, None)
            if isinstance(value, int):
                result_usage[target] = value
        return result_usage
