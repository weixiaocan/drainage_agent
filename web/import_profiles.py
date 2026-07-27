from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImportProfile:
    id: str
    project_id: str
    name: str
    source_identifier: str
    mapping: dict[str, str]
    source_units: dict[str, str]
    parsing_rules: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MappingCandidate:
    source: str
    field: str
    confidence: str = "suggested"


class MappingSuggester(Protocol):
    """Read-only candidate provider; it has no import confirmation capability."""

    def suggest(
        self,
        *,
        source_identifier: str | None,
        columns: list[dict[str, object]],
    ) -> list[MappingCandidate]: ...


class NoMappingSuggester:
    def suggest(
        self,
        *,
        source_identifier: str | None,
        columns: list[dict[str, object]],
    ) -> list[MappingCandidate]:
        return []


class ImportProfileRepository:
    def __init__(self, database: str) -> None:
        self.database = database
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_profiles (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_identifier TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    source_units_json TEXT NOT NULL,
                    parsing_rules_json TEXT NOT NULL,
                    UNIQUE(project_id, name)
                )
                """
            )

    def create(
        self,
        project_id: str,
        name: str,
        source_identifier: str,
        mapping: dict[str, str],
        source_units: dict[str, str],
        parsing_rules: dict[str, str],
    ) -> ImportProfile:
        profile = ImportProfile(
            id=uuid.uuid4().hex,
            project_id=project_id,
            name=name,
            source_identifier=source_identifier,
            mapping=dict(mapping),
            source_units=dict(source_units),
            parsing_rules=dict(parsing_rules),
        )
        try:
            with sqlite3.connect(self.database) as connection:
                connection.execute(
                    """
                    INSERT INTO import_profiles
                        (id, project_id, name, source_identifier, mapping_json,
                         source_units_json, parsing_rules_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.id,
                        project_id,
                        name,
                        source_identifier,
                        json.dumps(mapping, ensure_ascii=False),
                        json.dumps(source_units, ensure_ascii=False),
                        json.dumps(parsing_rules, ensure_ascii=False),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("当前项目中已存在同名导入配置") from exc
        return profile

    def get(self, project_id: str, profile_id: str) -> ImportProfile | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """
                SELECT id, project_id, name, source_identifier, mapping_json,
                       source_units_json, parsing_rules_json
                FROM import_profiles WHERE id = ? AND project_id = ?
                """,
                (profile_id, project_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(self, project_id: str) -> list[ImportProfile]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, name, source_identifier, mapping_json,
                       source_units_json, parsing_rules_json
                FROM import_profiles WHERE project_id = ? ORDER BY rowid
                """,
                (project_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: tuple[str, ...]) -> ImportProfile:
        return ImportProfile(
            id=row[0],
            project_id=row[1],
            name=row[2],
            source_identifier=row[3],
            mapping=json.loads(row[4]),
            source_units=json.loads(row[5]),
            parsing_rules=json.loads(row[6]),
        )
