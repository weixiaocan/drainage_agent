"""Monitoring project, workspace selection and import-profile routes."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from web.schemas import (
    AnalysisBatchCreateRequest,
    ImportProfileCreateRequest,
    ProjectCreateRequest,
    batch_data,
    project_data,
)


def register(app: FastAPI) -> None:
    @app.post("/api/projects", status_code=201)
    def create_project(request: ProjectCreateRequest) -> dict[str, str]:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="项目名称不能为空")
        return project_data(app.state.projects.create(name))

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, str]]:
        return [project_data(project) for project in app.state.projects.list()]

    @app.get("/api/projects/selection")
    def get_project_selection() -> dict[str, dict[str, str] | None]:
        project = (
            app.state.projects.get(app.state.current_project_id)
            if app.state.current_project_id
            else None
        )
        batch = (
            app.state.projects.get_batch(project.id, app.state.current_batch_id)
            if project and app.state.current_batch_id
            else None
        )
        return {
            "current_project": project_data(project) if project else None,
            "current_workspace": batch_data(batch) if batch else None,
        }

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, str]:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        return project_data(project)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, str]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        if app.state.current_project_id == project_id:
            app.state.current_project_id = None
            app.state.current_batch_id = None
            app.state.deps.current_project_id = None
            app.state.deps.current_batch_id = None
        app.state.projects.delete(project_id)
        return {"message": "项目已删除"}

    @app.put("/api/projects/{project_id}/selection")
    def select_project(project_id: str) -> dict[str, dict[str, str]]:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        workspace = app.state.projects.get_or_create_workspace(project.id)
        app.state.current_project_id = project.id
        app.state.current_batch_id = workspace.id
        app.state.deps.current_project_id = project.id
        app.state.deps.current_batch_id = workspace.id
        return {
            "current_project": project_data(project),
            "current_workspace": batch_data(workspace),
        }

    @app.post("/api/projects/{project_id}/import-profiles", status_code=201)
    def create_import_profile(
        project_id: str,
        request: ImportProfileCreateRequest,
    ) -> dict[str, object]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="导入配置名称不能为空")
        if not request.source_identifier.strip():
            raise HTTPException(status_code=400, detail="数据来源标识不能为空")
        try:
            profile = app.state.import_profiles.create(
                project_id,
                request.name.strip(),
                request.source_identifier.strip(),
                request.mapping,
                request.source_units,
                request.parsing_rules,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return profile.as_dict()

    @app.get("/api/projects/{project_id}/import-profiles")
    def list_import_profiles(project_id: str) -> list[dict[str, object]]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        return [
            profile.as_dict()
            for profile in app.state.import_profiles.list(project_id)
        ]

    @app.get("/api/projects/{project_id}/import-profiles/{profile_id}")
    def get_import_profile(
        project_id: str,
        profile_id: str,
    ) -> dict[str, object]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        profile = app.state.import_profiles.get(project_id, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="导入配置不存在")
        return profile.as_dict()

    @app.post("/api/projects/{project_id}/batches", status_code=201)
    def create_analysis_batch(
        project_id: str,
        request: AnalysisBatchCreateRequest,
    ) -> dict[str, str]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="分析批次名称不能为空")
        return batch_data(app.state.projects.create_batch(project_id, name))

    @app.get("/api/projects/{project_id}/batches")
    def list_analysis_batches(project_id: str) -> list[dict[str, str]]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        return [
            batch_data(batch)
            for batch in app.state.projects.list_batches(project_id)
        ]

    @app.get("/api/projects/{project_id}/batches/selection")
    def get_analysis_batch_selection(
        project_id: str,
    ) -> dict[str, dict[str, str] | None]:
        batch = (
            app.state.projects.get_batch(project_id, app.state.current_batch_id)
            if app.state.current_batch_id
            else None
        )
        return {"current_batch": batch_data(batch) if batch else None}

    @app.get("/api/projects/{project_id}/batches/{batch_id}")
    def get_analysis_batch(project_id: str, batch_id: str) -> dict[str, str]:
        batch = app.state.projects.get_batch(project_id, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        return batch_data(batch)

    @app.put("/api/projects/{project_id}/batches/{batch_id}/selection")
    def select_analysis_batch(
        project_id: str,
        batch_id: str,
    ) -> dict[str, dict[str, str]]:
        batch = app.state.projects.get_batch(project_id, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        app.state.current_project_id = project_id
        app.state.current_batch_id = batch.id
        app.state.deps.current_project_id = project_id
        app.state.deps.current_batch_id = batch.id
        return {"current_batch": batch_data(batch)}
