"""Report template, analysis facts and report draft routes."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from analysis.report_templates import InvalidReportTemplate
from web.schemas import ReportDraftRequest, batch_data, job_data, project_data
from web.uploads import read_upload


def register(app: FastAPI) -> None:
    @app.post(
        "/api/projects/{project_id}/report-templates",
        status_code=201,
    )
    async def upload_report_template(
        project_id: str,
        file: UploadFile = File(...),
        name: str = Form(""),
    ) -> dict[str, object]:
        try:
            template = app.state.report_templates.upload(
                project_id,
                name,
                file.filename or "",
                await read_upload(file),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidReportTemplate as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(template)

    @app.get("/api/projects/{project_id}/report-templates")
    def list_report_templates(
        project_id: str,
    ) -> list[dict[str, object]]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        builtin = {
            "template_id": "builtin",
            "project_id": project_id,
            "name": "内置契约模板",
            "artifact": None,
        }
        return [
            builtin,
            *[
                asdict(item)
                for item in app.state.report_templates.list_templates(
                    project_id
                )
            ],
        ]

    @app.get("/api/projects/{project_id}/batches/{batch_id}/facts")
    def get_batch_facts(project_id: str, batch_id: str) -> dict[str, object]:
        project = app.state.projects.get(project_id)
        batch = app.state.projects.get_batch(project_id, batch_id)
        if project is None or batch is None:
            raise HTTPException(status_code=404, detail="监测项目或分析批次不存在")
        baseline = app.state.filter_baselines.current_baseline(
            project_id, batch_id
        )
        current_results = {
            algorithm: asdict(result)
            for algorithm in (
                "data_quality",
                "patterns",
                "rainfall",
                "event_response",
                "rdii",
                "risk",
            )
            if (
                result := app.state.analysis_runner.current(
                    project_id, batch_id, algorithm
                )
            )
            is not None
        }
        return {
            "project": project_data(project),
            "batch": batch_data(batch),
            "baseline": asdict(baseline) if baseline else None,
            "analysis_results": current_results,
            "jobs": [
                job_data(job)
                for job in app.state.background_jobs.list_for_batch(
                    project_id, batch_id
                )
            ],
        }

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/reports",
        status_code=201,
    )
    def create_report_draft(
        project_id: str,
        batch_id: str,
        request: ReportDraftRequest,
    ) -> dict[str, object]:
        try:
            draft = app.state.report_templates.create_draft(
                project_id, batch_id, request.template_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        data = asdict(draft)
        data["docx_url"] = (
            f"/api/projects/{project_id}/batches/{batch_id}/files/{draft.docx}"
        )
        data["workbook_url"] = (
            f"/api/projects/{project_id}/batches/{batch_id}/files/{draft.workbook}"
        )
        return data

    @app.get("/api/projects/{project_id}/batches/{batch_id}/reports")
    def list_report_drafts(
        project_id: str,
        batch_id: str,
    ) -> list[dict[str, object]]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        return [
            asdict(item)
            for item in app.state.report_templates.list_drafts(
                project_id, batch_id
            )
        ]
