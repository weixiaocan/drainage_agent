from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from analysis.io.standard import STANDARD_FLOW_COLUMNS


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


_FIELD_DESCRIPTIONS = {
    "timestamp": "监测时间",
    "device_id": "设备编号",
    "point_id": "点位编号",
    "flow_lps": "流量（升/秒）",
    "level_m": "液位（米）",
    "velocity_mps": "流速（米/秒）",
}


class LLMMappingSuggester:
    """LLM-backed candidate provider; suggestions always require engineer confirmation."""

    def __init__(self, model: str, base_url: str | None, api_key: str) -> None:
        from openai import OpenAI

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model

    def suggest(
        self,
        *,
        source_identifier: str | None,
        columns: list[dict[str, object]],
    ) -> list[MappingCandidate]:
        if not columns:
            return []
        try:
            return self._suggest_with_retry(
                source_identifier=source_identifier, columns=columns
            )
        except Exception:
            return []

    def _suggest_with_retry(
        self,
        *,
        source_identifier: str | None,
        columns: list[dict[str, object]],
    ) -> list[MappingCandidate]:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": self._prompt(
                        source_identifier, columns
                    )}],
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                payload = json.loads(response.choices[0].message.content or "{}")
                return self._validated_candidates(payload, columns)
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        raise last_exc or RuntimeError("LLM 映射候选调用失败")

    @staticmethod
    def _prompt(
        source_identifier: str | None,
        columns: list[dict[str, object]],
    ) -> str:
        fields = "、".join(
            f"{field}（{label}）" for field, label in _FIELD_DESCRIPTIONS.items()
        )
        lines = "\n".join(
            f"- {column['source']}（类型：{column.get('type', '未知')}）"
            for column in columns
        )
        return (
            "你是排水监测数据导入助手。下面是 CSV 文件中无法自动识别的列，"
            "请判断每一列对应哪个规范字段。\n"
            f"规范字段：{fields}\n"
            f"数据来源标识：{source_identifier or '未知'}\n"
            f"待判断列：\n{lines}\n"
            "要求：只输出你有把握的映射；无法判断的列不要输出；"
            "不要根据数值猜测单位；同一规范字段最多对应一列。\n"
            '输出 JSON：{"candidates": [{"source": "<原始列名>", "field": "<规范字段>"}]}'
        )

    @staticmethod
    def _validated_candidates(
        payload: dict[str, object],
        columns: list[dict[str, object]],
    ) -> list[MappingCandidate]:
        valid_sources = {column["source"] for column in columns}
        candidates: list[MappingCandidate] = []
        used_fields: set[str] = set()
        for item in payload.get("candidates", []):
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            field = item.get("field")
            if (
                isinstance(source, str)
                and isinstance(field, str)
                and source in valid_sources
                and field in STANDARD_FLOW_COLUMNS
                and field not in used_fields
            ):
                used_fields.add(field)
                candidates.append(MappingCandidate(source=source, field=field))
        return candidates


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
