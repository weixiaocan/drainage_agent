from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.io import StandardDataStore, StandardDataUnavailable
from analysis.modules.stats import check_data
from analysis.baselines import FilterBaselineService


DATA_QUALITY_ALGORITHM_VERSION = "1"


class AnalysisPreconditionError(ValueError):
    """Raised when an analysis cannot run until the batch is prepared."""


@dataclass(frozen=True)
class AnalysisRequest:
    project_id: str
    batch_id: str
    algorithm: str
    points: list[str] | None = None
    start: str | None = None
    end: str | None = None
    force_rerun: bool = False


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
        if request.algorithm != "data_quality":
            raise ValueError(f"不支持的分析算法: {request.algorithm}")
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

        parameters = self._normalize_parameters(request)
        if parameters["points"]:
            flow = flow[flow["point_id"].astype(str).isin(parameters["points"])]
        if parameters["start"] is not None:
            flow = flow[flow["timestamp"] >= parameters["start"]]
        if parameters["end"] is not None:
            flow = flow[flow["timestamp"] <= parameters["end"]]
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
            "parameters": {
                "points": parameters["points"],
                "start": self._identity_timestamp(parameters["start"]),
                "end": self._identity_timestamp(parameters["end"]),
            },
            "algorithm": {
                "name": request.algorithm,
                "version": DATA_QUALITY_ALGORITHM_VERSION,
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
        table = check_data(flow).to_dict(orient="records")
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
            data={"table": table},
            artifacts=[artifact_relative],
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

    def _baseline_identity(
        self, project_id: str, batch_id: str, algorithm: str
    ) -> dict[str, Any]:
        if algorithm == "data_quality":
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
