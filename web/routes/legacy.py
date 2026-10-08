"""Single-workspace upload/results/files routes kept for the pre-project API."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from agent.deps import AgentDeps
from web.uploads import (
    ALLOWED_FLOW_EXTENSIONS,
    ALLOWED_RAINFALL_EXTENSIONS,
    ALLOWED_SITE_EXTENSIONS,
    clear_manifest,
    list_files,
    resolve_download_path,
    safe_upload_name,
    save_upload,
)


def register(app: FastAPI) -> None:
    @app.post("/api/upload")
    def upload_files(
        flow_files: list[UploadFile] = File(default=[]),
        rainfall_file: UploadFile | None = File(default=None),
        site_info_file: UploadFile | None = File(default=None),
        template_file: UploadFile | None = File(default=None),
    ) -> JSONResponse:
        deps: AgentDeps = app.state.deps
        saved: list[str] = []
        if template_file is not None and template_file.filename:
            raise HTTPException(
                status_code=400,
                detail="请在当前监测项目中通过报告模板接口上传并校验 DOCX",
            )

        for upload in flow_files:
            name = safe_upload_name(upload, ALLOWED_FLOW_EXTENSIONS)
            saved.append("resources/data/flow/" + save_upload(upload, deps.paths.flow_dir / name))

        if rainfall_file is not None and rainfall_file.filename:
            safe_upload_name(rainfall_file, ALLOWED_RAINFALL_EXTENSIONS)
            saved.append("resources/data/" + save_upload(rainfall_file, deps.paths.rainfall_file))

        if site_info_file is not None and site_info_file.filename:
            safe_upload_name(site_info_file, ALLOWED_SITE_EXTENSIONS)
            saved.append("resources/data/" + save_upload(site_info_file, deps.paths.site_info_file))

        if saved:
            clear_manifest(deps)

        message = "上传完成，旧分析结果已标记为可能过期。" if saved else "没有上传文件。"
        return JSONResponse({"saved": saved, "message": message})

    @app.get("/api/results")
    def results() -> dict[str, Any]:
        deps: AgentDeps = app.state.deps
        return {
            "outputs": list_files(deps.paths.root, deps.paths.outputs),
            "workspace": list_files(deps.paths.root, deps.paths.workspace),
        }

    @app.get("/files/{file_path:path}")
    def files(file_path: str) -> FileResponse:
        path = resolve_download_path(app.state.deps, file_path)
        return FileResponse(path, filename=path.name)
