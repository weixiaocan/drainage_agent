from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.exports import render_exports
from analysis.io import StandardDataStore, StandardDataUnavailable
from analysis.modules.event_response import analyze_event_response
from analysis.modules.dry_curves import build_dry_curves, dry_statistics
from analysis.modules.patterns import analyze_patterns
from analysis.modules.rainfall import analyze_rainfall
from analysis.modules.rdii import analyze_rdii
from analysis.modules.risk import assess_risk
from analysis.modules.stats import check_data
from analysis.baselines import FilterBaselineService


ALGORITHM_VERSIONS = {
    "data_quality": "1",
    "patterns": "1",
    "rainfall": "1",
    "event_response": "1",
    "rdii": "1",
    "risk": "1",
}


def _export_scope_prefix(request: "AnalysisRequest") -> str:
    points_part = "全网" if not request.points else f"{len(request.points)}点"
    time_part = "全时段" if not (request.start or request.end) else "指定时段"
    return f"{points_part}_{time_part}"


class AnalysisPreconditionError(ValueError):
    """Raised when an analysis cannot run until the batch is prepared."""


class AnalysisInputRequired(AnalysisPreconditionError):
    """Raised when a caller must provide a structured analysis input."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


@dataclass(frozen=True)
class AnalysisRequest:
    project_id: str
    batch_id: str
    algorithm: str
    points: list[str] | None = None
    start: str | None = None
    end: str | None = None
    event_ids: list[int] | None = None
    scope: str = "all"
    force_rerun: bool = False
    exports: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "exports", tuple(self.exports))


@dataclass(frozen=True)
class AnalysisResult:
    run_id: str
    project_id: str
    batch_id: str
    algorithm: str
    version: int
    status: str
    reused: bool
    identity: dict[str, Any]
    data: dict[str, Any]
    artifacts: list[str]
    created_at: str


class AnalysisRunner:
    """Run deterministic analyses against confirmed standard batch data."""

    def __init__(
        self,
        database: Path,
        files_root: Path,
        *,
        baseline_service: FilterBaselineService | None = None,
    ) -> None:
        self.database = database
        self.files_root = files_root.resolve()
        self.standard_data = StandardDataStore(self.files_root)
        self.baselines = baseline_service or FilterBaselineService(
            database, self.files_root
        )
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    identity_digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (project_id, batch_id, algorithm, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS current_analysis_results (
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    PRIMARY KEY (project_id, batch_id, algorithm),
                    FOREIGN KEY (run_id) REFERENCES analysis_runs (run_id)
                )
                """
            )

    def run(self, request: AnalysisRequest) -> AnalysisResult:
        self.validate(request)
        self._require_batch(request.project_id, request.batch_id)

        try:
            flow = self.standard_data.load_flow(
                request.project_id,
                request.batch_id,
            )
        except StandardDataUnavailable as exc:
            raise AnalysisPreconditionError(
                f"{exc}；请先导入并确认当前分析批次的标准数据"
            ) from exc
        return self._run_validated(request, flow)

    def validate(self, request: AnalysisRequest) -> None:
        """Validate cheap, structured request requirements before queuing."""
        if request.algorithm not in ALGORITHM_VERSIONS:
            raise ValueError(f"不支持的分析算法: {request.algorithm}")
        if request.algorithm in {"event_response", "rdii"} and not request.event_ids:
            raise AnalysisInputRequired(
                "event_ids",
                "请先选择需要分析的降雨场次 event_ids",
            )
        if request.algorithm == "risk":
            if request.scope not in {"dry", "rainy", "all"}:
                raise ValueError("风险范围 scope 必须为 dry、rainy 或 all")
            if request.scope in {"rainy", "all"} and not request.event_ids:
                raise AnalysisInputRequired(
                    "event_ids",
                    "雨天风险分析需要选择降雨场次 event_ids",
                )

    def _run_validated(
        self, request: AnalysisRequest, flow: Any
    ) -> AnalysisResult:
        parameters = self._normalize_parameters(request)

        def scoped(frame: Any) -> Any:
            result = frame
            if parameters["points"]:
                result = result[
                    result["point_id"].astype(str).isin(parameters["points"])
                ]
            if parameters["start"] is not None:
                result = result[result["timestamp"] >= parameters["start"]]
            if parameters["end"] is not None:
                result = result[result["timestamp"] <= parameters["end"]]
            return result.copy()

        raw_flow = scoped(flow)
        baseline_flow = None
        if request.algorithm in {"patterns", "rdii", "risk"}:
            baseline_flow = scoped(self.baselines.load_flow(
                request.project_id,
                request.batch_id,
            ))
            for column in ("flow_lps", "level_m", "velocity_mps"):
                baseline_flow[column] = baseline_flow[column].astype(float)
        identity = {
            "standard_input": {
                "contract_version": 1,
                "content_sha256": self._standard_digest(
                    request.project_id,
                    request.batch_id,
                ),
            },
            "baseline": self._baseline_identity(
                request.project_id, request.batch_id, request.algorithm
            ),
            "supplemental_inputs": self._supplemental_identity(request),
            "parameters": {
                "points": parameters["points"],
                "start": self._identity_timestamp(parameters["start"]),
                "end": self._identity_timestamp(parameters["end"]),
                "event_ids": parameters["event_ids"],
                "scope": parameters["scope"],
                "exports": sorted(request.exports),
            },
            "algorithm": {
                "name": request.algorithm,
                "version": ALGORITHM_VERSIONS[request.algorithm],
            },
        }
        identity_digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not request.force_rerun:
            existing = self._successful_with_identity(
                request.project_id,
                request.batch_id,
                request.algorithm,
                identity_digest,
            )
            if existing is not None:
                return self._with_reused(existing, True)

        version = self._next_version(
            request.project_id,
            request.batch_id,
            request.algorithm,
        )
        run_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        export_context: dict[str, Any] = {}
        if request.algorithm == "data_quality":
            table = check_data(raw_flow)
            data = {"table": table.to_dict(orient="records")}
            export_context["tables"] = {"table": table}
        elif request.algorithm == "patterns":
            patterns_result = analyze_patterns(baseline_flow)
            table = patterns_result["patterns"]
            data = {"table": table.to_dict(orient="records")}
            export_context["tables"] = {"table": table}
            export_context["curves"] = patterns_result["curves"]
            export_context["dry_flow"] = baseline_flow
        elif request.algorithm == "rainfall":
            try:
                rainfall = self.standard_data.load_rainfall(
                    request.project_id, request.batch_id
                )
            except StandardDataUnavailable as exc:
                raise AnalysisPreconditionError(str(exc)) from exc
            rainfall_result = analyze_rainfall(rainfall)
            data = {
                key: self._json_records(frame)
                for key, frame in rainfall_result.items()
            }
            for row in data["daily"]:
                row["date"] = str(row["date"]).split("T", 1)[0]
            export_context["tables"] = dict(rainfall_result)
            export_context["daily"] = rainfall_result["daily"]
        elif request.algorithm == "event_response":
            if not parameters["event_ids"]:
                raise AnalysisInputRequired(
                    "event_ids",
                    "请先选择需要分析的降雨场次 event_ids",
                )
            try:
                rainfall = self.standard_data.load_rainfall(
                    request.project_id, request.batch_id
                )
            except StandardDataUnavailable as exc:
                raise AnalysisPreconditionError(str(exc)) from exc
            events = analyze_rainfall(rainfall)["events"]
            table = analyze_event_response(
                raw_flow, events, parameters["event_ids"]
            )
            if table.empty:
                raise AnalysisPreconditionError(
                    "所选降雨场次、点位或时间范围没有数据覆盖"
                )
            data = {"table": self._json_records(table)}
            export_context["tables"] = {"table": table}
        elif request.algorithm == "rdii":
            if not parameters["event_ids"]:
                raise AnalysisInputRequired(
                    "event_ids",
                    "请先选择需要分析的降雨场次 event_ids",
                )
            rainfall = self._load_rainfall(request)
            events = analyze_rainfall(rainfall)["events"]
            rdii = analyze_rdii(
                raw_flow,
                build_dry_curves(baseline_flow),
                events,
                parameters["event_ids"],
            )
            table = rdii["rdii_total"]
            if table.empty:
                raise AnalysisPreconditionError(
                    "所选降雨场次、点位或时间范围没有数据覆盖"
                )
            data = {"table": self._json_records(table)}
            export_context["tables"] = {"table": table}
            export_context["rdii_curve_data"] = rdii["rdii_curve_data"]
            export_context["rain"] = rainfall
            export_context["events"] = events
            export_context["event_ids"] = parameters["event_ids"]
        else:
            if parameters["scope"] not in {"dry", "rainy", "all"}:
                raise ValueError("风险范围 scope 必须为 dry、rainy 或 all")
            if (
                parameters["scope"] in {"rainy", "all"}
                and not parameters["event_ids"]
            ):
                raise AnalysisInputRequired(
                    "event_ids",
                    "雨天风险分析需要选择降雨场次 event_ids",
                )
            try:
                sites = self.standard_data.load_sites(
                    request.project_id, request.batch_id
                )
            except StandardDataUnavailable as exc:
                raise AnalysisPreconditionError(str(exc)) from exc
            dry_stats = dry_statistics(baseline_flow, sites)
            rainfall = None
            events = None
            if parameters["scope"] in {"rainy", "all"}:
                rainfall = self._load_rainfall(request)
                events = analyze_rainfall(rainfall)["events"]
            risk = assess_risk(
                dry_stats,
                scope=parameters["scope"],
                sites=sites,
                flow=raw_flow,
                events=events,
                event_ids=parameters["event_ids"],
            )
            data = {
                "dry_analysis": self._json_records(dry_stats),
                "dry_risk": self._json_records(risk["dry_risk"]),
                "rainy_risk": self._json_records(risk["rainy_risk"]),
            }
            export_context["tables"] = {
                "dry_analysis": dry_stats,
                "dry_risk": risk["dry_risk"],
                "rainy_risk": risk["rainy_risk"],
            }
        export_paths: list[str] = []
        if request.exports:
            export_paths = render_exports(
                self._batch_root(request.project_id, request.batch_id),
                request.algorithm,
                run_id,
                request.exports,
                {**export_context, "scope_prefix": _export_scope_prefix(request)},
            )
        artifact_relative = f"results/{request.algorithm}/{run_id}/result.json"
        result = AnalysisResult(
            run_id=run_id,
            project_id=request.project_id,
            batch_id=request.batch_id,
            algorithm=request.algorithm,
            version=version,
            status="succeeded",
            reused=False,
            identity=identity,
            data=data,
            artifacts=[artifact_relative, *export_paths],
            created_at=created_at,
        )
        artifact = self._batch_root(request.project_id, request.batch_id) / artifact_relative
        artifact.parent.mkdir(parents=True, exist_ok=False)
        artifact.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        serialized = json.dumps(asdict(result), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    run_id, project_id, batch_id, algorithm, version, status,
                    identity_digest, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request.project_id,
                    request.batch_id,
                    request.algorithm,
                    version,
                    result.status,
                    identity_digest,
                    serialized,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO current_analysis_results (
                    project_id, batch_id, algorithm, run_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (project_id, batch_id, algorithm)
                DO UPDATE SET run_id = excluded.run_id
                """,
                (
                    request.project_id,
                    request.batch_id,
                    request.algorithm,
                    run_id,
                ),
            )
        return result

    def current(
        self,
        project_id: str,
        batch_id: str,
        algorithm: str,
    ) -> AnalysisResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT runs.result_json
                FROM current_analysis_results AS current
                JOIN analysis_runs AS runs ON runs.run_id = current.run_id
                WHERE current.project_id = ?
                  AND current.batch_id = ?
                  AND current.algorithm = ?
                """,
                (project_id, batch_id, algorithm),
            ).fetchone()
        return self._deserialize(row[0]) if row is not None else None

    def get(
        self,
        project_id: str,
        batch_id: str,
        run_id: str,
    ) -> AnalysisResult | None:
        """Return one historical result without crossing project/batch scope."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM analysis_runs
                WHERE run_id = ? AND project_id = ? AND batch_id = ?
                """,
                (run_id, project_id, batch_id),
            ).fetchone()
        return self._deserialize(row[0]) if row is not None else None

    def _successful_with_identity(
        self,
        project_id: str,
        batch_id: str,
        algorithm: str,
        identity_digest: str,
    ) -> AnalysisResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM analysis_runs
                WHERE project_id = ? AND batch_id = ? AND algorithm = ?
                  AND identity_digest = ? AND status = 'succeeded'
                ORDER BY version DESC LIMIT 1
                """,
                (project_id, batch_id, algorithm, identity_digest),
            ).fetchone()
        return self._deserialize(row[0]) if row is not None else None

    def _next_version(self, project_id: str, batch_id: str, algorithm: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 FROM analysis_runs
                WHERE project_id = ? AND batch_id = ? AND algorithm = ?
                """,
                (project_id, batch_id, algorithm),
            ).fetchone()
        return int(row[0])

    def _require_batch(self, project_id: str, batch_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM analysis_batches
                WHERE project_id = ? AND id = ?
                """,
                (project_id, batch_id),
            ).fetchone()
        if row is None:
            raise LookupError("分析批次不存在或不属于当前监测项目")

    def _standard_digest(self, project_id: str, batch_id: str) -> str:
        path = self._batch_root(project_id, batch_id) / "standard" / "flow.csv"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _supplemental_identity(
        self, request: AnalysisRequest
    ) -> dict[str, str]:
        identity: dict[str, str] = {}
        if request.algorithm in {"rainfall", "event_response", "rdii"} or (
            request.algorithm == "risk" and request.scope in {"rainy", "all"}
        ):
            path = (
                self._batch_root(request.project_id, request.batch_id)
                / "standard"
                / "rainfall.csv"
            )
            if not path.is_file():
                raise AnalysisPreconditionError(
                    "当前分析批次缺少标准降雨数据"
                )
            identity["rainfall_sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        if request.algorithm == "risk":
            sites = (
                self._batch_root(request.project_id, request.batch_id)
                / "standard"
                / "sites.csv"
            )
            if not sites.is_file():
                raise AnalysisPreconditionError(
                    "当前分析批次缺少标准点位资料"
                )
            identity["sites_sha256"] = hashlib.sha256(
                sites.read_bytes()
            ).hexdigest()
        return identity

    def _load_rainfall(self, request: AnalysisRequest) -> Any:
        try:
            return self.standard_data.load_rainfall(
                request.project_id, request.batch_id
            )
        except StandardDataUnavailable as exc:
            raise AnalysisPreconditionError(str(exc)) from exc

    @staticmethod
    def _json_records(frame: Any) -> list[dict[str, Any]]:
        return json.loads(frame.to_json(orient="records", date_format="iso"))

    def _baseline_identity(
        self, project_id: str, batch_id: str, algorithm: str
    ) -> dict[str, Any]:
        if algorithm in {"data_quality", "rainfall", "event_response"}:
            return {"kind": "none", "identity": None}
        baseline = self.baselines.current_baseline(project_id, batch_id)
        if baseline is None:
            raise AnalysisPreconditionError("请先确认当前分析批次的筛选结果")
        return baseline.identity

    def _batch_root(self, project_id: str, batch_id: str) -> Path:
        root = (self.files_root / project_id / "batches" / batch_id).resolve()
        if not root.is_relative_to(self.files_root):
            raise LookupError("项目或批次标识超出项目目录")
        return root

    @staticmethod
    def _normalize_parameters(request: AnalysisRequest) -> dict[str, Any]:
        import pandas as pd

        try:
            return {
                "points": sorted(set(map(str, request.points or []))),
                "start": pd.to_datetime(request.start) if request.start else None,
                "end": pd.to_datetime(request.end) if request.end else None,
                "event_ids": sorted(set(map(int, request.event_ids or []))),
                "scope": str(request.scope),
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("分析时间参数无效，请使用 ISO 8601 时间") from exc

    @staticmethod
    def _identity_timestamp(value: Any | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _deserialize(serialized: str) -> AnalysisResult:
        return AnalysisResult(**json.loads(serialized))

    @staticmethod
    def _with_reused(result: AnalysisResult, reused: bool) -> AnalysisResult:
        values = asdict(result)
        values["reused"] = reused
        return AnalysisResult(**values)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)
