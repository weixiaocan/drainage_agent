from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from analysis.runs import AnalysisRequest, AnalysisRunner


TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed"})


@dataclass(frozen=True)
class BackgroundJob:
    job_id: str
    project_id: str
    batch_id: str
    request: AnalysisRequest
    status: str
    step: str
    progress: int
    error_summary: str | None
    result_run_id: str | None
    result_artifacts: list[str]
    created_at: str
    started_at: str | None
    finished_at: str | None


class BackgroundJobService:
    """Persist and execute local analysis jobs for Web and Agent callers."""

    def __init__(
        self,
        database: Path,
        runner: AnalysisRunner,
        *,
        max_workers: int = 2,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.database = Path(database)
        self.runner = runner
        self.max_workers = max_workers
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="analysis-job",
        )
        self._lock_guard = threading.Lock()
        self._analysis_locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._initialize()

    def submit(self, request: AnalysisRequest) -> BackgroundJob:
        job = BackgroundJob(
            job_id=uuid.uuid4().hex,
            project_id=request.project_id,
            batch_id=request.batch_id,
            request=request,
            status="queued",
            step="等待本地执行器",
            progress=0,
            error_summary=None,
            result_run_id=None,
            result_artifacts=[],
            created_at=_now(),
            started_at=None,
            finished_at=None,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO background_jobs (
                    job_id, project_id, batch_id, request_json, status, step,
                    progress, error_summary, result_run_id,
                    result_artifacts_json, created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._row_values(job),
            )
        self._executor.submit(self._execute, job.job_id)
        return job

    def get(
        self,
        project_id: str,
        batch_id: str,
        job_id: str,
    ) -> BackgroundJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_id, project_id, batch_id, request_json, status,
                       step, progress, error_summary, result_run_id,
                       result_artifacts_json, created_at, started_at, finished_at
                FROM background_jobs
                WHERE job_id = ? AND project_id = ? AND batch_id = ?
                """,
                (job_id, project_id, batch_id),
            ).fetchone()
        return self._deserialize(row) if row is not None else None

    def list_for_batch(
        self,
        project_id: str,
        batch_id: str,
    ) -> list[BackgroundJob]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, project_id, batch_id, request_json, status,
                       step, progress, error_summary, result_run_id,
                       result_artifacts_json, created_at, started_at, finished_at
                FROM background_jobs
                WHERE project_id = ? AND batch_id = ?
                ORDER BY created_at DESC, job_id DESC
                """,
                (project_id, batch_id),
            ).fetchall()
        return [self._deserialize(row) for row in rows]

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_futures: bool = False,
    ) -> None:
        self._executor.shutdown(
            wait=wait,
            cancel_futures=cancel_futures,
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS background_jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    error_summary TEXT,
                    result_run_id TEXT,
                    result_artifacts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            interrupted_at = _now()
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'failed',
                    step = '应用重启后停止',
                    progress = 100,
                    error_summary = '应用重启时作业尚未完成，请重新提交分析。',
                    finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (interrupted_at,),
            )

    def _execute(self, job_id: str) -> None:
        started_at = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM background_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'running', step = '执行分析', progress = 10,
                    started_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (started_at, job_id),
            )
        request = AnalysisRequest(**json.loads(row[0]))
        try:
            lock_key = (
                request.project_id,
                request.batch_id,
                request.algorithm,
            )
            with self._lock_guard:
                analysis_lock = self._analysis_locks.setdefault(
                    lock_key, threading.Lock()
                )
            with analysis_lock:
                result = self.runner.run(request)
        except Exception as exc:
            self._mark_failed(job_id, exc)
            return
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'succeeded', step = '分析完成', progress = 100,
                    result_run_id = ?, result_artifacts_json = ?,
                    finished_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (
                    result.run_id,
                    json.dumps(result.artifacts, ensure_ascii=False),
                    _now(),
                    job_id,
                ),
            )

    def _mark_failed(self, job_id: str, exc: Exception) -> None:
        if isinstance(exc, (LookupError, ValueError)):
            summary = " ".join(str(exc).split())
        else:
            summary = f"分析执行发生意外错误（{type(exc).__name__}）。"
        if not summary:
            summary = "分析执行失败，未提供错误详情。"
        summary = summary[:500]
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'failed', step = '分析失败', progress = 100,
                    error_summary = ?, finished_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (summary, _now(), job_id),
            )

    @staticmethod
    def _row_values(job: BackgroundJob) -> tuple[object, ...]:
        return (
            job.job_id,
            job.project_id,
            job.batch_id,
            json.dumps(asdict(job.request), ensure_ascii=False),
            job.status,
            job.step,
            job.progress,
            job.error_summary,
            job.result_run_id,
            json.dumps(job.result_artifacts, ensure_ascii=False),
            job.created_at,
            job.started_at,
            job.finished_at,
        )

    @staticmethod
    def _deserialize(row: tuple[object, ...]) -> BackgroundJob:
        return BackgroundJob(
            job_id=str(row[0]),
            project_id=str(row[1]),
            batch_id=str(row[2]),
            request=AnalysisRequest(**json.loads(str(row[3]))),
            status=str(row[4]),
            step=str(row[5]),
            progress=int(row[6]),
            error_summary=str(row[7]) if row[7] is not None else None,
            result_run_id=str(row[8]) if row[8] is not None else None,
            result_artifacts=json.loads(str(row[9])),
            created_at=str(row[10]),
            started_at=str(row[11]) if row[11] is not None else None,
            finished_at=str(row[12]) if row[12] is not None else None,
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, timeout=30)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
