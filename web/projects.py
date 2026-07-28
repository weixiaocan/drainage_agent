from __future__ import annotations

import sqlite3
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS derived_batch_sources (
                    derived_batch_id TEXT NOT NULL,
                    source_batch_id TEXT NOT NULL,
                    source_position INTEGER NOT NULL,
                    PRIMARY KEY (derived_batch_id, source_batch_id),
                    FOREIGN KEY (derived_batch_id) REFERENCES analysis_batches (id),
                    FOREIGN KEY (source_batch_id) REFERENCES analysis_batches (id)
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

    def create_derived_batch(
        self,
        project_id: str,
        name: str,
        source_batch_ids: list[str],
    ) -> AnalysisBatch:
        batch = self.create_batch(project_id, name)
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO derived_batch_sources (
                    derived_batch_id,
                    source_batch_id,
                    source_position
                )
                VALUES (?, ?, ?)
                """,
                [
                    (batch.id, source_batch_id, position)
                    for position, source_batch_id in enumerate(source_batch_ids)
                ],
            )
        return batch

    def get_batch_sources(
        self,
        project_id: str,
        derived_batch_id: str,
    ) -> list[AnalysisBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source.id, source.project_id, source.name, source.created_at
                FROM derived_batch_sources AS relation
                JOIN analysis_batches AS derived
                    ON derived.id = relation.derived_batch_id
                JOIN analysis_batches AS source
                    ON source.id = relation.source_batch_id
                WHERE derived.project_id = ? AND derived.id = ?
                ORDER BY relation.source_position
                """,
                (project_id, derived_batch_id),
            ).fetchall()
        return [AnalysisBatch(*row) for row in rows]

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

    def workspace(self, project_id: str) -> Path:
        return self.files_root / project_id

    def batch_workspace(self, project_id: str, batch_id: str) -> Path:
        return self.workspace(project_id) / "batches" / batch_id

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
