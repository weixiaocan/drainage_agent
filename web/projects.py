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


class ProjectRepository:
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

    def workspace(self, project_id: str) -> Path:
        return self.files_root / project_id

    def resolve_file(self, project_id: str, file_path: str) -> Path:
        workspace = self.workspace(project_id).resolve()
        requested = (workspace / file_path).resolve()
        if requested == workspace or not requested.is_relative_to(workspace):
            raise ValueError("项目文件路径超出当前项目空间")
        return requested

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)
