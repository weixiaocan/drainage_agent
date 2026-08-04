from __future__ import annotations

import sqlite3
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    created_at: str


@dataclass(frozen=True)
class AnalysisBatch:
    id: str
    project_id: str
    name: str
    created_at: str


class ProjectRepository:
    INTERNAL_WORKSPACE_NAME = "__project_workspace__"
    BATCH_DIRECTORIES = (
        "inputs",
        "standard",
        "baseline",
        "results",
        "sessions",
        "jobs",
    )

    def __init__(self, database: Path, files_root: Path) -> None:
        self.database = database
        self.files_root = files_root
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.files_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_batches (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects (id)
                )
                """
            )

    def create(self, name: str) -> Project:
        project = Project(
            id=uuid.uuid4().hex,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
                (project.id, project.name, project.created_at),
            )
        self.workspace(project.id).mkdir(parents=True, exist_ok=True)
        return project

    def get(self, project_id: str) -> Project | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, name, created_at FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        return Project(*row) if row is not None else None

    def list(self) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, created_at FROM projects ORDER BY created_at, id"
            ).fetchall()
        return [Project(*row) for row in rows]

    def create_batch(self, project_id: str, name: str) -> AnalysisBatch:
        batch = AnalysisBatch(
            id=uuid.uuid4().hex,
            project_id=project_id,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_batches (id, project_id, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (batch.id, batch.project_id, batch.name, batch.created_at),
            )
        batch_workspace = self.batch_workspace(project_id, batch.id)
        for directory in self.BATCH_DIRECTORIES:
            (batch_workspace / directory).mkdir(parents=True, exist_ok=True)
        return batch

    def get_batch(self, project_id: str, batch_id: str) -> AnalysisBatch | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, project_id, name, created_at
                FROM analysis_batches
                WHERE project_id = ? AND id = ?
                """,
                (project_id, batch_id),
            ).fetchone()
        return AnalysisBatch(*row) if row is not None else None

    def list_batches(self, project_id: str) -> list[AnalysisBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, name, created_at
                FROM analysis_batches
                WHERE project_id = ?
                ORDER BY created_at, id
                """,
                (project_id,),
            ).fetchall()
        return [AnalysisBatch(*row) for row in rows]

    def get_or_create_workspace(self, project_id: str) -> AnalysisBatch:
        """Return the single internal workspace used by the project-first UI."""
        batches = self.list_batches(project_id)
        internal = next(
            (batch for batch in reversed(batches)
             if batch.name == self.INTERNAL_WORKSPACE_NAME),
            None,
        )
        if internal is not None:
            return internal
        if batches:
            return batches[-1]
        return self.create_batch(project_id, self.INTERNAL_WORKSPACE_NAME)

    def workspace(self, project_id: str) -> Path:
        return self.files_root / project_id

    def batch_workspace(self, project_id: str, batch_id: str) -> Path:
        return self.workspace(project_id) / "batches" / batch_id

    def delete(self, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            batch_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM analysis_batches WHERE project_id = ?",
                    (project_id,),
                )
            ]
            for table in (
                "current_analysis_baselines",
                "analysis_baselines",
                "filter_results",
                "data_imports",
                "analysis_runs",
                "agent_sessions",
                "archived_agent_sessions",
                "report_drafts",
                "report_templates",
                "background_jobs",
            ):
                for batch_id in batch_ids:
                    connection.execute(
                        f"DELETE FROM {table} WHERE project_id = ? AND batch_id = ?",
                        (project_id, batch_id),
                    )
                connection.execute(
                    f"DELETE FROM {table} WHERE project_id = ?",
                    (project_id,),
                )
            connection.execute(
                "DELETE FROM analysis_batches WHERE project_id = ?",
                (project_id,),
            )
            connection.execute(
                "DELETE FROM projects WHERE id = ?", (project_id,)
            )
        project_dir = self.workspace(project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir)

    def resolve_file(self, project_id: str, file_path: str) -> Path:
        workspace = self.workspace(project_id).resolve()
        requested = (workspace / file_path).resolve()
        if requested == workspace or not requested.is_relative_to(workspace):
            raise ValueError("项目文件路径超出当前项目空间")
        return requested

    def resolve_batch_file(
        self, project_id: str, batch_id: str, file_path: str
    ) -> Path:
        if self.get_batch(project_id, batch_id) is None:
            raise LookupError("分析批次不存在")
        workspace = self.batch_workspace(project_id, batch_id).resolve()
        requested = (workspace / file_path).resolve()
        if requested == workspace or not requested.is_relative_to(workspace):
            raise ValueError("批次文件路径超出当前分析批次空间")
        return requested

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)
