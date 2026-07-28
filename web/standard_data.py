from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePath

import pandas as pd

from analysis.io.standard import STANDARD_FLOW_COLUMNS, STANDARD_FLOW_UNITS

@dataclass(frozen=True)
class InspectedColumn:
    source: str
    field: str | None
    type: str
    unit: str | None


@dataclass(frozen=True)
class ImportInspection:
    id: str
    status: str
    encoding: str
    row_count: int
    columns: list[InspectedColumn]
    anomalies: list[str]
    mapping: dict[str, str] | None = None
    source_units: dict[str, str] | None = None
    source_identifier: str | None = None
    profile_id: str | None = None
    parsing_rules: dict[str, str] | None = None
    standard_preview: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        return value


class BatchDataImporter:
    """Deterministic batch import boundary shared by Web and later batch tooling."""

    COLUMN_RULES = {
        "数据时间": ("timestamp", "datetime", None),
        "时间": ("timestamp", "datetime", None),
        "timestamp": ("timestamp", "datetime", None),
        "time": ("timestamp", "datetime", None),
        "设备编号": ("device_id", "string", None),
        "device_id": ("device_id", "string", None),
        "点位": ("point_id", "string", None),
        "点位编号": ("point_id", "string", None),
        "point_id": ("point_id", "string", None),
        "site": ("point_id", "string", None),
        "流量(L/s)(均值)": ("flow_lps", "number", "L/s"),
        "流量(L/s)": ("flow_lps", "number", "L/s"),
        "flow_lps": ("flow_lps", "number", "L/s"),
        "flow_lps(m3/h)": ("flow_lps", "number", None),
        "flow": ("flow_lps", "number", None),
        "流速(m/s)(均值)": ("velocity_mps", "number", "m/s"),
        "流速(m/s)": ("velocity_mps", "number", "m/s"),
        "velocity_mps": ("velocity_mps", "number", "m/s"),
        "velocity": ("velocity_mps", "number", None),
        "液位(m)(均值)": ("level_m", "number", "m"),
        "液位(m)": ("level_m", "number", "m"),
        "level_m": ("level_m", "number", "m"),
        "level": ("level_m", "number", None),
    }
    UNIT_CONFLICTS = {
        "flow_lps(m3/h)": (
            "字段 flow_lps(m3/h) 的名称表示 L/s，但表头单位为 m3/h，请确认源单位"
        )
    }
    IGNORED_COLUMNS = {"1分钟内记录总数"}

    def __init__(self, database: Path, files_root: Path) -> None:
        self.database = database
        self.files_root = files_root
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_imports (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    inspection_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'uploaded',
                    mapping_json TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(data_imports)")
            }
            if "status" not in columns:
                connection.execute(
                    "ALTER TABLE data_imports ADD COLUMN status TEXT NOT NULL DEFAULT 'uploaded'"
                )
            if "mapping_json" not in columns:
                connection.execute(
                    "ALTER TABLE data_imports ADD COLUMN mapping_json TEXT"
                )

    def inspect_upload(
        self,
        project_id: str,
        batch_id: str,
        filename: str,
        content: bytes,
        *,
        profile_id: str | None = None,
        source_identifier: str | None = None,
        profile_mapping: dict[str, str] | None = None,
        profile_units: dict[str, str] | None = None,
        parsing_rules: dict[str, str] | None = None,
    ) -> ImportInspection:
        safe_name = PurePath(filename).name
        if not safe_name or safe_name != filename or Path(safe_name).suffix.lower() != ".csv":
            raise ValueError("原始监测数据必须是安全命名的 CSV 文件")
        encoding, text = self._decode(content)
        delimiter = (parsing_rules or {}).get("delimiter", ",")
        if len(delimiter) != 1:
            raise ValueError("解析规则 delimiter 必须是单个字符")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows: list[dict[str, str]] = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(rows) < 100:
                rows.append(row)
        if not rows:
            raise ValueError("文件不包含数据行，请上传至少一行监测数据")
        columns = []
        for source in rows[0].keys():
            if source in self.IGNORED_COLUMNS:
                continue
            rule = self.COLUMN_RULES.get(source)
            profile_field = (profile_mapping or {}).get(source)
            profile_unit = (profile_units or {}).get(source)
            columns.append(
                InspectedColumn(
                    source=source,
                    field=rule[0] if rule else profile_field,
                    type=rule[1] if rule else self._infer_type(rows, source),
                    unit=rule[2] if rule and rule[2] else profile_unit,
                )
            )
        anomalies = [
            self.UNIT_CONFLICTS[column.source]
            for column in columns
            if column.source in self.UNIT_CONFLICTS
        ]
        anomalies.extend(
            f"字段 {column.source} 的单位缺失，请确认源单位"
            for column in columns
            if column.field in {"flow_lps", "level_m", "velocity_mps"}
            and column.unit is None
            and column.source not in self.UNIT_CONFLICTS
        )
        mapped_fields = {column.field for column in columns if column.field}
        required = {"timestamp", "flow_lps"}
        if self._point_id_from_filename(safe_name) is None:
            required.add("point_id")
        missing = sorted(required - mapped_fields)
        if missing:
            anomalies.append(
                "无法自动映射必需字段: "
                + ", ".join(missing)
                + "；请修正字段映射"
            )
        effective_mapping = {
            column.source: column.field
            for column in columns
            if column.field is not None
        }
        effective_units = {
            column.source: column.unit
            for column in columns
            if column.unit is not None
        }
        preview = None
        if profile_id and not anomalies:
            preview = self._mapped_preview(
                rows,
                effective_mapping,
                effective_units,
                safe_name,
                decimal=(parsing_rules or {}).get("decimal", "."),
            )
        inspection = ImportInspection(
            id=uuid.uuid4().hex,
            status="pending_confirmation" if anomalies or profile_id else "ready",
            encoding=encoding,
            row_count=row_count,
            columns=columns,
            anomalies=anomalies,
            mapping=effective_mapping if profile_id else None,
            source_units=effective_units if profile_id else None,
            source_identifier=source_identifier,
            profile_id=profile_id,
            parsing_rules=dict(parsing_rules or {}) if profile_id else None,
            standard_preview=preview,
        )
        raw_path = self._raw_path(project_id, batch_id, inspection.id, safe_name)
        raw_path.parent.mkdir(parents=True, exist_ok=False)
        with raw_path.open("xb") as raw_file:
            raw_file.write(content)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT INTO data_imports
                    (id, project_id, batch_id, filename, sha256, inspection_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inspection.id,
                    project_id,
                    batch_id,
                    safe_name,
                    hashlib.sha256(content).hexdigest(),
                    json.dumps(inspection.as_dict(), ensure_ascii=False),
                    inspection.status,
                ),
            )
        return inspection

    def inspection(
        self,
        project_id: str,
        batch_id: str,
        import_id: str,
    ) -> dict[str, object] | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """
                SELECT inspection_json FROM data_imports
                WHERE id = ? AND project_id = ? AND batch_id = ?
                """,
                (import_id, project_id, batch_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _mapped_preview(
        self,
        rows: list[dict[str, str]],
        mapping: dict[str, str],
        units: dict[str, str],
        filename: str,
        decimal: str = ".",
        limit: int = 20,
    ) -> dict[str, object]:
        raw = pd.DataFrame(rows[:limit])
        canonical = pd.DataFrame(index=raw.index)
        for field in STANDARD_FLOW_COLUMNS:
            source = next(
                (name for name, target in mapping.items() if target == field),
                None,
            )
            canonical[field] = raw[source] if source else None
        if "point_id" not in mapping.values():
            canonical["point_id"] = self._point_id_from_filename(filename)
        canonical["timestamp"] = pd.to_datetime(
            canonical["timestamp"], errors="coerce"
        )
        for field in ("flow_lps", "level_m", "velocity_mps"):
            if decimal != ".":
                canonical[field] = canonical[field].str.replace(
                    decimal, ".", regex=False
                )
            canonical[field] = pd.to_numeric(canonical[field], errors="coerce")
            source = next(
                (name for name, target in mapping.items() if target == field),
                None,
            )
            if source:
                canonical[field] = self._convert(
                    canonical[field], field, units[source]
                )
        canonical["timestamp"] = canonical["timestamp"].map(
            lambda value: value.isoformat() if pd.notna(value) else None
        )
        return {
            "columns": list(STANDARD_FLOW_COLUMNS),
            "units": STANDARD_FLOW_UNITS,
            "rows": canonical.where(pd.notna(canonical), None).to_dict(
                orient="records"
            ),
        }

    def confirm_mapping(
        self,
        project_id: str,
        batch_id: str,
        import_id: str,
        mapping: dict[str, str],
        units: dict[str, str],
    ) -> dict[str, str]:
        source = self.raw_file(project_id, batch_id, import_id)
        if source is None:
            raise LookupError("导入记录不存在")
        standard_path = self.standard_flow_path(project_id, batch_id)
        if standard_path.exists():
            raise ValueError("标准数据已经生成，不可覆盖")

        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """
                SELECT inspection_json, sha256 FROM data_imports
                WHERE id = ? AND project_id = ? AND batch_id = ?
                """,
                (import_id, project_id, batch_id),
            ).fetchone()
        inspection = json.loads(row[0])
        source_sha256 = row[1]
        parsing_rules = inspection.get("parsing_rules") or {}
        read_options = {
            "dtype": str,
            "sep": parsing_rules.get("delimiter", ","),
            "encoding": inspection["encoding"],
        }
        source_columns = list(pd.read_csv(source, nrows=0, **read_options).columns)
        unknown_sources = sorted(set(mapping) - set(source_columns))
        if unknown_sources:
            raise ValueError(
                "映射包含文件中不存在的字段: " + ", ".join(unknown_sources)
            )
        invalid_targets = sorted(set(mapping.values()) - set(STANDARD_FLOW_COLUMNS))
        if invalid_targets:
            raise ValueError(
                "映射包含不支持的规范字段: " + ", ".join(invalid_targets)
            )
        duplicate_targets = sorted(
            field
            for field in set(mapping.values())
            if list(mapping.values()).count(field) > 1
        )
        if duplicate_targets:
            raise ValueError(
                "多个源字段不能映射到同一规范字段: "
                + ", ".join(duplicate_targets)
            )
        filename_point_id = self._point_id_from_filename(source.name)
        required = {"timestamp", "flow_lps"}
        if filename_point_id is None:
            required.add("point_id")
        mapped_fields = set(mapping.values())
        missing = sorted(required - mapped_fields)
        if missing:
            raise ValueError(f"缺少必需字段映射: {', '.join(missing)}")

        standard_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            for chunk_index, raw in enumerate(
                pd.read_csv(source, chunksize=200_000, **read_options)
            ):
                canonical = self._canonicalize(
                    raw,
                    mapping,
                    units,
                    filename_point_id,
                    parsing_rules.get("decimal", "."),
                )
                canonical.to_csv(
                    standard_path,
                    mode="w" if chunk_index == 0 else "a",
                    header=chunk_index == 0,
                    index=False,
                    encoding="utf-8",
                    date_format="%Y-%m-%dT%H:%M:%S",
                )
        except Exception:
            standard_path.unlink(missing_ok=True)
            raise
        manifest = {
            "contract_version": 1,
            "kind": "standard_flow",
            "columns": list(STANDARD_FLOW_COLUMNS),
            "units": STANDARD_FLOW_UNITS,
            "source_import_id": import_id,
            "source_sha256": source_sha256,
            "source_encoding": inspection["encoding"],
            "mapping": mapping,
            "source_units": units,
            "file": "flow.csv",
        }
        (standard_path.parent / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE data_imports SET status = 'confirmed', mapping_json = ?
                WHERE id = ? AND project_id = ? AND batch_id = ?
                """,
                (
                    json.dumps(
                        {"mapping": mapping, "units": units},
                        ensure_ascii=False,
                    ),
                    import_id,
                    project_id,
                    batch_id,
                ),
            )
        return {"id": import_id, "status": "confirmed"}

    def confirm_batch_mappings(
        self,
        project_id: str,
        batch_id: str,
        imports: list[dict[str, object]],
    ) -> dict[str, object]:
        if not imports:
            raise ValueError("请至少确认一个监测数据文件")
        standard_path = self.standard_flow_path(project_id, batch_id)
        if standard_path.exists():
            raise ValueError("标准数据已经生成，不可覆盖")

        frames: list[pd.DataFrame] = []
        manifests: list[dict[str, object]] = []
        for item in imports:
            import_id = str(item["import_id"])
            mapping = dict(item["mapping"])
            units = dict(item["units"])
            source = self.raw_file(project_id, batch_id, import_id)
            if source is None:
                raise LookupError(f"导入记录不存在: {import_id}")
            with sqlite3.connect(self.database) as connection:
                row = connection.execute(
                    """
                    SELECT inspection_json, sha256 FROM data_imports
                    WHERE id = ? AND project_id = ? AND batch_id = ?
                    """,
                    (import_id, project_id, batch_id),
                ).fetchone()
            inspection = json.loads(row[0])
            parsing_rules = inspection.get("parsing_rules") or {}
            read_options = {
                "dtype": str,
                "sep": parsing_rules.get("delimiter", ","),
                "encoding": inspection["encoding"],
            }
            raw = pd.read_csv(source, **read_options)
            source_columns = set(raw.columns)
            unknown_sources = sorted(set(mapping) - source_columns)
            if unknown_sources:
                raise ValueError(
                    f"{source.name} 的映射包含不存在字段: "
                    + ", ".join(unknown_sources)
                )
            invalid_targets = sorted(set(mapping.values()) - set(STANDARD_FLOW_COLUMNS))
            if invalid_targets:
                raise ValueError("映射包含不支持的规范字段: " + ", ".join(invalid_targets))
            duplicate_targets = sorted(
                field for field in set(mapping.values())
                if list(mapping.values()).count(field) > 1
            )
            if duplicate_targets:
                raise ValueError("多个源字段不能映射到同一规范字段: " + ", ".join(duplicate_targets))
            filename_point_id = self._point_id_from_filename(source.name)
            required = {"timestamp", "flow_lps"}
            if filename_point_id is None:
                required.add("point_id")
            missing = sorted(required - set(mapping.values()))
            if missing:
                raise ValueError(f"{source.name} 缺少必需字段映射: {', '.join(missing)}")
            frames.append(
                self._canonicalize(
                    raw, mapping, units, filename_point_id,
                    parsing_rules.get("decimal", "."),
                )
            )
            manifests.append({
                "import_id": import_id,
                "source_sha256": row[1],
                "source_encoding": inspection["encoding"],
                "mapping": mapping,
                "source_units": units,
            })

        standard_path.parent.mkdir(parents=True, exist_ok=True)
        combined = pd.concat(frames, ignore_index=True)
        combined.to_csv(
            standard_path,
            index=False,
            encoding="utf-8",
            date_format="%Y-%m-%dT%H:%M:%S",
        )
        (standard_path.parent / "manifest.json").write_text(
            json.dumps({
                "contract_version": 1,
                "kind": "standard_flow",
                "columns": list(STANDARD_FLOW_COLUMNS),
                "units": STANDARD_FLOW_UNITS,
                "sources": manifests,
                "file": "flow.csv",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with sqlite3.connect(self.database) as connection:
            for item in imports:
                connection.execute(
                    """
                    UPDATE data_imports SET status = 'confirmed', mapping_json = ?
                    WHERE id = ? AND project_id = ? AND batch_id = ?
                    """,
                    (
                        json.dumps(
                            {"mapping": item["mapping"], "units": item["units"]},
                            ensure_ascii=False,
                        ),
                        item["import_id"], project_id, batch_id,
                    ),
                )
        return {
            "status": "confirmed",
            "import_count": len(imports),
            "row_count": len(combined),
        }

    def _canonicalize(
        self,
        raw: pd.DataFrame,
        mapping: dict[str, str],
        units: dict[str, str],
        filename_point_id: str | None,
        decimal: str,
    ) -> pd.DataFrame:
        canonical = pd.DataFrame(index=raw.index)
        for field in STANDARD_FLOW_COLUMNS:
            source_names = [
                source for source, target in mapping.items() if target == field
            ]
            canonical[field] = raw[source_names[0]] if source_names else None
        if "point_id" not in mapping.values():
            canonical["point_id"] = filename_point_id
        canonical["timestamp"] = pd.to_datetime(
            canonical["timestamp"], errors="coerce"
        )
        if canonical["timestamp"].isna().any():
            raise ValueError("时间字段包含无效值，请修正后重新确认")
        for field in ("flow_lps", "level_m", "velocity_mps"):
            if decimal != ".":
                canonical[field] = canonical[field].str.replace(
                    decimal, ".", regex=False
                )
            canonical[field] = pd.to_numeric(canonical[field], errors="coerce")
            source_name = next(
                (name for name, target in mapping.items() if target == field),
                None,
            )
            if source_name is not None:
                unit = units.get(source_name)
                if not unit:
                    raise ValueError(f"字段 {source_name} 的单位仍未确认")
                canonical[field] = self._convert(
                    canonical[field], field, unit
                )
        if (
            canonical["point_id"].isna().any()
            or canonical["point_id"].astype(str).str.strip().eq("").any()
        ):
            raise ValueError("点位字段包含空值，请补齐后重新确认")
        if canonical["flow_lps"].isna().any():
            raise ValueError("流量字段包含非数值或空值，请修正后重新确认")
        return canonical

    def raw_file(self, project_id: str, batch_id: str, import_id: str) -> Path | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """
                SELECT filename FROM data_imports
                WHERE id = ? AND project_id = ? AND batch_id = ?
                """,
                (import_id, project_id, batch_id),
            ).fetchone()
        if row is None:
            return None
        path = self._raw_path(project_id, batch_id, import_id, row[0])
        return path if path.is_file() else None

    def _raw_path(
        self,
        project_id: str,
        batch_id: str,
        import_id: str,
        filename: str,
    ) -> Path:
        return (
            self.files_root
            / project_id
            / "batches"
            / batch_id
            / "inputs"
            / import_id
            / filename
        )

    def standard_flow_path(self, project_id: str, batch_id: str) -> Path:
        return (
            self.files_root
            / project_id
            / "batches"
            / batch_id
            / "standard"
            / "flow.csv"
        )

    def standard_preview(
        self,
        project_id: str,
        batch_id: str,
        limit: int = 20,
    ) -> dict[str, object] | None:
        path = self.standard_flow_path(project_id, batch_id)
        if not path.is_file():
            return None
        frame = pd.read_csv(
            path,
            dtype={"device_id": "string", "point_id": "string"},
        ).head(limit)
        rows = frame.where(pd.notna(frame), None).to_dict(orient="records")
        return {
            "columns": list(STANDARD_FLOW_COLUMNS),
            "units": STANDARD_FLOW_UNITS,
            "rows": rows,
        }

    @staticmethod
    def _convert(series: pd.Series, field: str, source_unit: str) -> pd.Series:
        unit = source_unit.strip().lower().replace("³", "3")
        conversions = {
            ("flow_lps", "l/s"): 1.0,
            ("flow_lps", "m3/h"): 1000.0 / 3600.0,
            ("level_m", "m"): 1.0,
            ("level_m", "mm"): 0.001,
            ("velocity_mps", "m/s"): 1.0,
        }
        factor = conversions.get((field, unit))
        if factor is None:
            raise ValueError(
                f"字段 {field} 的单位 {source_unit} 不受支持，请选择明确的源单位"
            )
        return series * factor

    @staticmethod
    def _point_id_from_filename(filename: str) -> str | None:
        match = re.search(r"(?:^|_)(W\d+)(?:_|\.|$)", filename, re.IGNORECASE)
        return match.group(1).upper() if match else None

    @staticmethod
    def _infer_type(rows: list[dict[str, str]], source: str) -> str:
        values = [row.get(source, "").strip() for row in rows]
        try:
            for value in values:
                float(value)
            return "number"
        except ValueError:
            return "string"

    @staticmethod
    def _decode(content: bytes) -> tuple[str, str]:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = content.decode(encoding)
                return ("utf-8" if encoding == "utf-8-sig" else encoding), text
            except UnicodeDecodeError:
                continue
        raise ValueError("无法识别文件编码，请另存为 UTF-8 或 GB18030 后重试")
