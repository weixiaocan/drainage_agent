"""Filter baseline, analysis run and background job routes."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from analysis.baselines import BaselinePreconditionError, FilterRequest
from analysis.runs import AnalysisPreconditionError, AnalysisInputRequired, AnalysisRequest
from web.schemas import AnalysisRunRequest, FilterConfirmationRequest, FilterRunRequest, job_data
from web.uploads import read_upload
from web.workspace import archive_chat_and_clear_analysis


def register(app: FastAPI) -> None:
    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/filters",
        status_code=201,
    )
    def run_batch_filter(
        project_id: str,
        batch_id: str,
        request: FilterRunRequest,
    ) -> dict[str, object]:
        try:
            result = app.state.filter_baselines.run_filter(
                FilterRequest(
                    project_id=project_id,
                    batch_id=batch_id,
                    **request.model_dump(),
                )
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BaselinePreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/filters/upload",
        status_code=201,
    )
    async def upload_filter_file(
        project_id: str,
        batch_id: str,
        file: UploadFile = File(...),
    ) -> dict[str, object]:
        try:
            result = app.state.filter_baselines.upload_filter(
                project_id,
                batch_id,
                file.filename or "",
                await read_upload(file),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BaselinePreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    @app.get("/api/projects/{project_id}/batches/{batch_id}/filters")
    def list_batch_filters(
        project_id: str, batch_id: str
    ) -> list[dict[str, object]]:
        try:
            return [
                asdict(item)
                for item in app.state.filter_baselines.list_filters(
                    project_id, batch_id
                )
            ]
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/filters/{filter_id}"
    )
    def get_batch_filter(
        project_id: str,
        batch_id: str,
        filter_id: str,
    ) -> dict[str, object]:
        try:
            result = app.state.filter_baselines.get_filter(
                project_id, batch_id, filter_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="筛选结果不存在")
        return asdict(result)

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/filters/{filter_id}/download"
    )
    def download_filter_result(
        project_id: str,
        batch_id: str,
        filter_id: str,
    ) -> FileResponse:
        try:
            path = app.state.filter_baselines.artifact_path(
                project_id, batch_id, filter_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="筛选文件不存在") from exc
        return FileResponse(path, filename="filter_result.xlsx")

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/filters/{filter_id}/revisions",
        status_code=201,
    )
    async def upload_filter_revision(
        project_id: str,
        batch_id: str,
        filter_id: str,
        file: UploadFile = File(...),
    ) -> dict[str, object]:
        try:
            result = app.state.filter_baselines.upload_revision(
                project_id,
                batch_id,
                filter_id,
                file.filename or "",
                await read_upload(file),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BaselinePreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/filters/{filter_id}/confirmation"
    )
    def confirm_filter_result(
        project_id: str,
        batch_id: str,
        filter_id: str,
        request: FilterConfirmationRequest,
    ) -> dict[str, object]:
        if not request.confirm:
            raise HTTPException(
                status_code=400,
                detail="必须明确确认筛选结果才能建立分析基线",
            )
        try:
            candidate = app.state.filter_baselines.get_filter(
                project_id, batch_id, filter_id
            )
            with sqlite3.connect(
                app.state.root / "var" / "drainage.sqlite3"
            ) as connection:
                previous = connection.execute(
                    """
                    SELECT filter_id FROM analysis_baselines
                    WHERE project_id = ? AND batch_id = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (project_id, batch_id),
                ).fetchone()
            derived_state_reset = (
                previous is not None
                and candidate is not None
                and bool(candidate.identity.get("source_filter_id"))
            )
            baseline = app.state.filter_baselines.confirm(
                project_id, batch_id, filter_id
            )
            if derived_state_reset:
                archive_chat_and_clear_analysis(app, project_id, batch_id)
            data = asdict(baseline)
            data["derived_state_reset"] = derived_state_reset
            return data
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BaselinePreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/batches/{batch_id}/baseline")
    def get_current_baseline(
        project_id: str, batch_id: str
    ) -> dict[str, object]:
        try:
            baseline = app.state.filter_baselines.current_baseline(
                project_id, batch_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if baseline is None:
            raise HTTPException(status_code=409, detail="当前无有效分析基线")
        return asdict(baseline)

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/analysis-runs/{algorithm}"
    )
    def run_batch_analysis(
        project_id: str,
        batch_id: str,
        algorithm: str,
        request: AnalysisRunRequest,
    ) -> dict[str, object]:
        try:
            result = app.state.analysis_runner.run(
                AnalysisRequest(
                    project_id=project_id,
                    batch_id=batch_id,
                    algorithm=algorithm,
                    points=request.points,
                    start=request.start,
                    end=request.end,
                    event_ids=request.event_ids,
                    scope=request.scope,
                    force_rerun=request.force_rerun,
                )
            )
        except AnalysisInputRequired as exc:
            raise HTTPException(
                status_code=422,
                detail={"missing": exc.field, "message": str(exc)},
            ) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AnalysisPreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/analysis-jobs/{algorithm}",
        status_code=202,
    )
    def submit_analysis_job(
        project_id: str,
        batch_id: str,
        algorithm: str,
        request: AnalysisRunRequest,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            job = app.state.background_jobs.submit(
                AnalysisRequest(
                    project_id=project_id,
                    batch_id=batch_id,
                    algorithm=algorithm,
                    points=request.points,
                    start=request.start,
                    end=request.end,
                    event_ids=request.event_ids,
                    scope=request.scope,
                    force_rerun=request.force_rerun,
                )
            )
        except AnalysisInputRequired as exc:
            raise HTTPException(
                status_code=422,
                detail={"missing": exc.field, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return job_data(job)

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/analysis-jobs"
    )
    def list_analysis_jobs(
        project_id: str,
        batch_id: str,
    ) -> list[dict[str, object]]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        return [
            job_data(job)
            for job in app.state.background_jobs.list_for_batch(
                project_id, batch_id
            )
        ]

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/analysis-jobs/{job_id}"
    )
    def get_analysis_job(
        project_id: str,
        batch_id: str,
        job_id: str,
    ) -> dict[str, object]:
        job = app.state.background_jobs.get(project_id, batch_id, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="后台作业不存在")
        return job_data(job)

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/analysis-results/{run_id}"
    )
    def get_analysis_result(
        project_id: str,
        batch_id: str,
        run_id: str,
    ) -> dict[str, object]:
        result = app.state.analysis_runner.get(
            project_id, batch_id, run_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="分析结果不存在")
        return asdict(result)
