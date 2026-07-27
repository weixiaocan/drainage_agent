from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path, PurePath
from typing import Any, Callable

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from agent.core import build_agent
from agent.deps import AgentDeps, build_deps
from agent.core.logging_utils import TraceLogger, trace_event
from web.projects import AnalysisBatch, Project, ProjectRepository


ALLOWED_FLOW_EXTENSIONS = {".csv"}
ALLOWED_RAINFALL_EXTENSIONS = {".csv"}
ALLOWED_SITE_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
ALLOWED_TEMPLATE_EXTENSIONS = {".docx"}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class ProjectCreateRequest(BaseModel):
    name: str


class AnalysisBatchCreateRequest(BaseModel):
    name: str


def _project_data(project: Project) -> dict[str, str]:
    return asdict(project)


def _batch_data(batch: AnalysisBatch) -> dict[str, str]:
    return asdict(batch)


def _result_text(result: Any) -> str:
    for attr in ("output", "data"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            return value() if callable(value) else str(value)
    return str(result)


def _safe_upload_name(upload: UploadFile, allowed_extensions: set[str]) -> str:
    filename = upload.filename or ""
    name = PurePath(filename).name
    if not name or name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail=f"非法文件名: {filename!r}")
    suffix = Path(name).suffix.lower()
    if suffix not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise HTTPException(status_code=400, detail=f"{name} 文件类型不支持，仅允许 {allowed}")
    return name


def _safe_project_file_name(upload: UploadFile) -> str:
    filename = upload.filename or ""
    name = PurePath(filename).name
    if not name or name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail=f"非法文件名: {filename!r}")
    return name


def _save_upload(upload: UploadFile, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return target.name


def _clear_manifest(deps: AgentDeps) -> None:
    deps.paths.outputs.mkdir(parents=True, exist_ok=True)
    deps.paths.manifest.write_text(
        json.dumps({"version": 1, "results": {}, "notice": "uploaded data changed"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _list_files(root: Path, base: Path) -> list[dict[str, Any]]:
    if not base.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if path.is_file():
            rel = path.resolve().relative_to(root.resolve()).as_posix()
            files.append({"path": rel, "name": path.name, "size": path.stat().st_size})
    return files


def _resolve_download_path(deps: AgentDeps, file_path: str) -> Path:
    root = deps.paths.root.resolve()
    requested = (root / file_path).resolve()
    allowed_roots = [deps.paths.outputs.resolve(), deps.paths.workspace.resolve()]
    if not any(requested == allowed or requested.is_relative_to(allowed) for allowed in allowed_roots):
        raise HTTPException(status_code=403, detail="只能下载 var/outputs/ 或 var/workspace/ 下的文件")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return requested


def create_app(
    root: Path | None = None,
    *,
    deps_factory: Callable[[Path], AgentDeps] = build_deps,
    agent_factory: Callable[[AgentDeps], Any] = build_agent,
) -> FastAPI:
    app = FastAPI(title="Drainage Agent", docs_url="/docs")
    app.state.root = (root or Path.cwd()).resolve()
    app.state.deps = deps_factory(app.state.root)
    app.state.trace = TraceLogger(app.state.deps.paths.logs)
    app.state.deps.trace = app.state.trace
    app.state.agent = agent_factory(app.state.deps)
    app.state.histories: dict[str, list[Any]] = {}
    app.state.projects = ProjectRepository(
        app.state.root / "var" / "drainage.sqlite3",
        app.state.root / "var" / "projects",
    )
    app.state.current_project_id: str | None = None
    app.state.current_batch_id: str | None = None

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html_path = Path(__file__).resolve().parent / "static" / "index.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    @app.post("/api/projects", status_code=201)
    def create_project(request: ProjectCreateRequest) -> dict[str, str]:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="项目名称不能为空")
        return _project_data(app.state.projects.create(name))

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, str]]:
        return [_project_data(project) for project in app.state.projects.list()]

    @app.get("/api/projects/selection")
    def get_project_selection() -> dict[str, dict[str, str] | None]:
        project = (
            app.state.projects.get(app.state.current_project_id)
            if app.state.current_project_id
            else None
        )
        return {"current_project": _project_data(project) if project else None}

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, str]:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        return _project_data(project)

    @app.put("/api/projects/{project_id}/selection")
    def select_project(project_id: str) -> dict[str, dict[str, str]]:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        app.state.current_project_id = project.id
        app.state.current_batch_id = None
        return {"current_project": _project_data(project)}

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
        return _batch_data(app.state.projects.create_batch(project_id, name))

    @app.get("/api/projects/{project_id}/batches")
    def list_analysis_batches(project_id: str) -> list[dict[str, str]]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        return [
            _batch_data(batch)
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
        return {"current_batch": _batch_data(batch) if batch else None}

    @app.get("/api/projects/{project_id}/batches/{batch_id}")
    def get_analysis_batch(project_id: str, batch_id: str) -> dict[str, str]:
        batch = app.state.projects.get_batch(project_id, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        return _batch_data(batch)

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
        return {"current_batch": _batch_data(batch)}

    @app.post("/api/projects/{project_id}/files")
    def upload_project_files(
        project_id: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, list[str]]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        saved = [
            _save_upload(
                upload,
                app.state.projects.workspace(project_id)
                / _safe_project_file_name(upload),
            )
            for upload in files
        ]
        return {"saved": saved}

    @app.get("/api/projects/{project_id}/files/{file_path:path}")
    def download_project_file(project_id: str, file_path: str) -> FileResponse:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        try:
            path = app.state.projects.resolve_file(project_id, file_path)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(path, filename=path.name)

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        session_id = request.session_id or uuid.uuid4().hex
        history = app.state.histories.setdefault(session_id, [])
        run_id = uuid.uuid4().hex
        app.state.deps.session.current_run_id = run_id
        trace_event(app.state.trace, {"event": "turn_start", "run_id": run_id, "session_id": session_id, "user": message})
        try:
            result = app.state.agent.run_sync(message, deps=app.state.deps, message_history=history)
            reply = _result_text(result)
            if hasattr(result, "all_messages"):
                app.state.histories[session_id] = result.all_messages()
            trace_event(app.state.trace, {"event": "turn_end", "run_id": run_id, "session_id": session_id, "reply": reply})
            app.state.deps.session.current_run_id = None
            return ChatResponse(session_id=session_id, reply=reply)
        except Exception as exc:
            app.state.deps.logger.exception("Web agent turn failed")
            app.state.deps.session.current_run_id = None
            return ChatResponse(session_id=session_id, reply=f"Agent 调用失败: {exc}")

    @app.post("/api/upload")
    def upload_files(
        flow_files: list[UploadFile] = File(default=[]),
        rainfall_file: UploadFile | None = File(default=None),
        site_info_file: UploadFile | None = File(default=None),
        template_file: UploadFile | None = File(default=None),
    ) -> JSONResponse:
        deps: AgentDeps = app.state.deps
        saved: list[str] = []

        for upload in flow_files:
            name = _safe_upload_name(upload, ALLOWED_FLOW_EXTENSIONS)
            saved.append("resources/data/flow/" + _save_upload(upload, deps.paths.flow_dir / name))

        if rainfall_file is not None and rainfall_file.filename:
            _safe_upload_name(rainfall_file, ALLOWED_RAINFALL_EXTENSIONS)
            saved.append("resources/data/" + _save_upload(rainfall_file, deps.paths.rainfall_file))

        if site_info_file is not None and site_info_file.filename:
            _safe_upload_name(site_info_file, ALLOWED_SITE_EXTENSIONS)
            saved.append("resources/data/" + _save_upload(site_info_file, deps.paths.site_info_file))

        if template_file is not None and template_file.filename:
            name = _safe_upload_name(template_file, ALLOWED_TEMPLATE_EXTENSIONS)
            for old_template in deps.paths.templates.glob("*.docx"):
                old_template.unlink()
            saved.append("resources/templates/" + _save_upload(template_file, deps.paths.templates / name))

        if saved:
            _clear_manifest(deps)

        message = "上传完成，旧分析结果已标记为可能过期。" if saved else "没有上传文件。"
        return JSONResponse({"saved": saved, "message": message})

    @app.get("/api/results")
    def results() -> dict[str, Any]:
        deps: AgentDeps = app.state.deps
        return {
            "outputs": _list_files(deps.paths.root, deps.paths.outputs),
            "workspace": _list_files(deps.paths.root, deps.paths.workspace),
        }

    @app.get("/files/{file_path:path}")
    def files(file_path: str) -> FileResponse:
        path = _resolve_download_path(app.state.deps, file_path)
        return FileResponse(path, filename=path.name)

    return app


app = create_app()
