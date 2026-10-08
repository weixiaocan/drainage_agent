"""Project file upload/download and workspace state routes."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePath

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from web.uploads import (
    ALLOWED_PROJECT_EXTENSIONS,
    CHAT_ATTACHMENT_EXTENSIONS,
    read_upload,
    safe_upload_name,
    save_upload,
)
from web.workspace import archive_chat_and_clear_analysis, current_workspace_artifacts


def register(app: FastAPI) -> None:
    @app.post("/api/projects/{project_id}/files")
    def upload_project_files(
        project_id: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, list[str]]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        saved = [
            save_upload(
                upload,
                app.state.projects.workspace(project_id)
                / safe_upload_name(upload, ALLOWED_PROJECT_EXTENSIONS),
            )
            for upload in files
        ]
        return {"saved": saved}

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/chat-attachments",
        status_code=201,
    )
    async def upload_chat_attachments(
        project_id: str,
        batch_id: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, list[dict[str, object]]]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="当前监测项目不存在")
        if not files or len(files) > 10:
            raise HTTPException(status_code=400, detail="每轮可上传 1 至 10 个补充文件")
        root = app.state.projects.batch_workspace(project_id, batch_id)
        target_dir = root / "inputs" / "attachments"
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for upload in files:
            original = safe_upload_name(upload, CHAT_ATTACHMENT_EXTENSIONS)
            stored = f"{uuid.uuid4().hex[:8]}-{original}"
            content = await read_upload(upload)
            target = target_dir / stored
            target.write_bytes(content)
            saved.append({
                "name": original,
                "path": target.relative_to(root).as_posix(),
                "size": len(content),
            })
        return {"files": saved}

    @app.get("/api/projects/{project_id}/files/{file_path:path}")
    def download_project_file(project_id: str, file_path: str) -> FileResponse:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        if PurePath(file_path).parts[:1] == ("batches",):
            raise HTTPException(
                status_code=403,
                detail="批次产物必须通过绑定分析批次的下载接口访问",
            )
        try:
            path = app.state.projects.resolve_file(project_id, file_path)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(path, filename=path.name)

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/files/{file_path:path}"
    )
    def download_batch_file(
        project_id: str, batch_id: str, file_path: str
    ) -> FileResponse:
        try:
            path = app.state.projects.resolve_batch_file(
                project_id, batch_id, file_path
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(path, filename=path.name)

    @app.get("/api/projects/{project_id}/workspace/artifacts")
    def list_project_artifacts(project_id: str) -> dict[str, object]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        workspace = app.state.projects.get_or_create_workspace(project_id)
        return {
            "workspace_id": workspace.id,
            "files": current_workspace_artifacts(app, project_id, workspace.id),
        }

    def _project_zip(
        project_id: str,
        *,
        results_only: bool,
    ) -> FileResponse:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        workspace = app.state.projects.get_or_create_workspace(project_id)
        root = app.state.projects.batch_workspace(project_id, workspace.id)
        temporary = tempfile.NamedTemporaryFile(
            prefix=f"drainage-{project_id}-",
            suffix=".zip",
            delete=False,
        )
        temporary_path = Path(temporary.name)
        temporary.close()
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            if results_only:
                for user_dir in ("exports",):
                    base = root / user_dir
                    if not base.is_dir():
                        continue
                    for path in sorted(base.rglob("*")):
                        if path.is_file() and not (
                            path.parent == base
                            and path.name.startswith("chat-")
                            and path.suffix.lower() == ".zip"
                        ):
                            archive.write(
                                path,
                                arcname=path.relative_to(root).as_posix(),
                            )
            else:
                archive.writestr(
                    "project.json",
                    json.dumps(
                        {
                            "id": project.id,
                            "name": project.name,
                            "workspace_id": workspace.id,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                for directory in (
                    "inputs", "standard", "baseline", "results", "exports",
                ):
                    folder = root / directory
                    if not folder.is_dir():
                        continue
                    for path in folder.rglob("*"):
                        if path.is_file() and not (
                            directory == "exports"
                            and path.parent == folder
                            and path.name.startswith("chat-")
                            and path.suffix.lower() == ".zip"
                        ):
                            archive.write(
                                path,
                                arcname=path.relative_to(root).as_posix(),
                            )
        suffix = "results" if results_only else "all"
        return FileResponse(
            temporary_path,
            media_type="application/zip",
            filename=f"{project.name}-{suffix}.zip",
            background=BackgroundTask(temporary_path.unlink, missing_ok=True),
        )

    @app.get("/api/projects/{project_id}/downloads/all")
    def download_project_all(project_id: str) -> FileResponse:
        return _project_zip(project_id, results_only=False)

    @app.get("/api/projects/{project_id}/downloads/results")
    def download_project_results(project_id: str) -> FileResponse:
        return _project_zip(project_id, results_only=True)

    @app.get("/api/projects/{project_id}/workspace/state")
    def get_project_workspace_state(project_id: str) -> dict[str, object]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        workspace = app.state.projects.get_or_create_workspace(project_id)
        root = app.state.projects.batch_workspace(project_id, workspace.id)
        standard = root / "standard"
        flow_files = 0
        flow_manifest_path = standard / "manifest.json"
        if flow_manifest_path.is_file():
            try:
                flow_manifest = json.loads(
                    flow_manifest_path.read_text(encoding="utf-8")
                )
                sources = flow_manifest.get("sources", [])
                if isinstance(sources, list):
                    flow_files = len(sources)
            except (OSError, json.JSONDecodeError):
                flow_files = 0
        auxiliary_manifest = {}
        auxiliary_path = standard / "auxiliary_manifest.json"
        if auxiliary_path.is_file():
            try:
                auxiliary_manifest = json.loads(
                    auxiliary_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                auxiliary_manifest = {}
        baseline = app.state.filter_baselines.current_baseline(
            project_id, workspace.id
        )
        return {
            "workspace_id": workspace.id,
            "has_data": (standard / "flow.csv").is_file(),
            "flow": {
                "present": (standard / "flow.csv").is_file(),
                "file_count": int(flow_files),
            },
            "rainfall": {
                "present": (standard / "rainfall.csv").is_file(),
                "filename": auxiliary_manifest.get("rainfall"),
            },
            "sites": {
                "present": (standard / "sites.csv").is_file(),
                "filename": auxiliary_manifest.get("sites"),
            },
            "filter": (
                {
                    "present": True,
                    "version": baseline.version,
                    "filter_id": baseline.filter_id,
                    "path": baseline.artifact,
                }
                if baseline is not None
                else {"present": False}
            ),
        }

    @app.post("/api/projects/{project_id}/workspace/reset")
    def reset_project_workspace(project_id: str) -> dict[str, object]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        workspace = app.state.projects.get_or_create_workspace(project_id)
        archive_chat_and_clear_analysis(app, project_id, workspace.id)
        database = app.state.root / "var" / "drainage.sqlite3"
        with sqlite3.connect(database) as connection:
            for table in (
                "current_analysis_baselines",
                "analysis_baselines",
                "filter_results",
                "data_imports",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE project_id = ? AND batch_id = ?",
                    (project_id, workspace.id),
                )
        root = app.state.projects.batch_workspace(project_id, workspace.id)
        for directory in (
            "inputs", "standard", "baseline", "results",
            "exports", "jobs", "sessions",
        ):
            target = (root / directory).resolve()
            if target.is_relative_to(root.resolve()) and target.is_dir():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
        return {
            "workspace_id": workspace.id,
            "message": "当前数据已清空，旧对话已归档。",
        }
