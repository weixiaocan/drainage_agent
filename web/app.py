from __future__ import annotations

import json
import io
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from copy import copy
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from analysis.io.standard import STANDARD_FLOW_COLUMNS
from analysis.baselines import (
    BaselinePreconditionError,
    FilterRequest,
)
from analysis.jobs import BackgroundJob, BackgroundJobService
from analysis.report_templates import (
    InvalidReportTemplate,
    ReportTemplateService,
)
from analysis.runs import (
    AnalysisPreconditionError,
    AnalysisInputRequired,
    AnalysisRequest,
    AnalysisRunner,
)
from agent.core import build_agent
from agent.conversations import ConversationRepository, ConversationRunner
from agent.deps import AgentDeps, available_agent_settings, build_deps
from agent.run_records import RunRecorder
from web.projects import AnalysisBatch, Project, ProjectRepository
from web.import_profiles import (
    ImportProfileRepository,
    LLMMappingSuggester,
    MappingSuggester,
    NoMappingSuggester,
)
from web.standard_data import BatchDataImporter


ALLOWED_FLOW_EXTENSIONS = {".csv"}
ALLOWED_RAINFALL_EXTENSIONS = {".csv"}
ALLOWED_SITE_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
ALLOWED_PROJECT_EXTENSIONS = {
    ".csv",
    ".docx",
    ".json",
    ".png",
    ".txt",
    ".xls",
    ".xlsm",
    ".xlsx",
}
MAX_UPLOAD_BYTES = int(
    os.getenv("DRAINAGE_MAX_UPLOAD_BYTES", str(256 * 1024 * 1024))
)
UPLOAD_CHUNK_BYTES = 1024 * 1024


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    project_id: str | None = None
    batch_id: str | None = None
    debug: bool = False
    model_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    run_id: str
    reply: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


def _deps_with_settings(deps: AgentDeps, settings: Any) -> AgentDeps:
    model_deps = copy(deps)
    model_deps.settings = settings
    return model_deps


def _select_chat_artifacts(
    current: list[dict[str, Any]],
    before_paths: set[str],
) -> list[dict[str, Any]]:
    created = [item for item in current if item["path"] not in before_paths]
    return [
        item
        for item in created
        if str(item["path"]).startswith("exports/")
    ]


_DOWNLOAD_REQUEST_MARKERS = ("下载", "导出", "打包", "zip")
_CHAT_DELIVERABLE_SUFFIXES = {".png", ".jpg", ".jpeg", ".csv", ".xlsx", ".zip"}
_CHAT_DELIVERABLE_EXCLUDED_PREFIXES = (
    "standard/",
    "inputs/",
    "baseline/",
    "sessions/",
)
_REPLY_PATH_PATTERN = re.compile(
    r"[\w\-./一-鿿()]+?\.(?:png|jpe?g|csv|xlsx|zip)",
    re.IGNORECASE,
)


def _requests_file_download(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _DOWNLOAD_REQUEST_MARKERS)


def _workspace_file_paths(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _is_chat_deliverable(rel_path: str) -> bool:
    if rel_path.startswith(_CHAT_DELIVERABLE_EXCLUDED_PREFIXES):
        return False
    if PurePath(rel_path).name == "result.json":
        return False
    return PurePath(rel_path).suffix.lower() in _CHAT_DELIVERABLE_SUFFIXES


def _select_requested_downloads(
    root: Path,
    before_files: set[str],
    reply_text: str,
    run_id: str,
) -> list[dict[str, Any]]:
    created = _workspace_file_paths(root) - before_files
    cited = {
        match.group(0).lstrip("./")
        for match in _REPLY_PATH_PATTERN.finditer(reply_text)
    }
    deliverable = sorted(
        path
        for path in (created | cited)
        if _is_chat_deliverable(path) and (root / path).is_file()
    )
    if not deliverable:
        return []
    if len(deliverable) == 1:
        rel = deliverable[0]
        return [
            {
                "path": rel,
                "name": PurePath(rel).name,
                "size": (root / rel).stat().st_size,
            }
        ]
    zip_rel = f"exports/chat-{run_id[:8]}.zip"
    zip_path = root / zip_rel
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in deliverable:
            name = PurePath(rel).name
            if name in used_names:
                name = f"{PurePath(rel).parent.name}_{name}"
            used_names.add(name)
            archive.write(root / rel, arcname=name)
    return [
        {
            "path": zip_rel,
            "name": "本轮生成文件打包.zip",
            "size": zip_path.stat().st_size,
        }
    ]


class ProjectCreateRequest(BaseModel):
    name: str


class AnalysisBatchCreateRequest(BaseModel):
    name: str


class ImportMappingRequest(BaseModel):
    mapping: dict[str, str]
    units: dict[str, str]


class BatchImportMappingItem(BaseModel):
    import_id: str
    mapping: dict[str, str]
    units: dict[str, str]


class BatchImportMappingRequest(BaseModel):
    imports: list[BatchImportMappingItem]


class ImportProfileCreateRequest(BaseModel):
    name: str
    source_identifier: str
    mapping: dict[str, str]
    source_units: dict[str, str]
    parsing_rules: dict[str, str]


class AnalysisRunRequest(BaseModel):
    points: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    event_ids: list[int] = Field(default_factory=list)
    scope: str = "all"
    force_rerun: bool = False


class ReportDraftRequest(BaseModel):
    template_id: str = "builtin"


class FilterRunRequest(BaseModel):
    missing_rate_threshold: float = 0.1
    expected_rows_per_day: int = 1440
    rain_day_filter_threshold: float = 2.0
    zero_like_threshold: float = 0.02
    high_zero_ratio_threshold: float = 0.5
    high_zero_ratio_normal_days_threshold: int = 5
    zero_day_drop_min_nonzero_keep_days: int = 3
    mean_lower_ratio: float = 0.5
    mean_upper_ratio: float = 2.0


class FilterConfirmationRequest(BaseModel):
    confirm: bool


def _project_data(project: Project) -> dict[str, str]:
    return asdict(project)


def _batch_data(batch: AnalysisBatch) -> dict[str, str]:
    return asdict(batch)


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


def _save_upload(upload: UploadFile, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    created = False
    try:
        with target.open("xb") as f:
            created = True
            while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件超过 {MAX_UPLOAD_BYTES} 字节上限",
                    )
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="上传文件不能为空")
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"文件已存在，不允许静默覆盖: {target.name}",
        ) from exc
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
    return target.name


async def _read_upload(upload: UploadFile) -> bytes:
    content = bytearray()
    while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"上传文件超过 {MAX_UPLOAD_BYTES} 字节上限",
            )
    if not content:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    return bytes(content)


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


def _default_mapping_suggester(deps: AgentDeps) -> MappingSuggester:
    if deps.settings.api_key:
        return LLMMappingSuggester(
            model=deps.settings.model,
            base_url=deps.settings.base_url,
            api_key=deps.settings.api_key,
        )
    return NoMappingSuggester()


def create_app(
    root: Path | None = None,
    *,
    deps_factory: Callable[[Path], AgentDeps] = build_deps,
    agent_factory: Callable[[AgentDeps], Any] = build_agent,
    mapping_suggester: MappingSuggester | None = None,
    background_job_workers: int = 2,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI):
        yield
        lifespan_app.state.background_jobs.shutdown()

    app = FastAPI(
        title="Drainage Agent",
        docs_url="/docs",
        lifespan=lifespan,
    )
    app.state.root = (root or Path.cwd()).resolve()
    app.state.deps = deps_factory(app.state.root)
    model_settings = available_agent_settings()
    model_settings[app.state.deps.settings.provider_id] = app.state.deps.settings
    app.state.model_agents = {
        model_id: (agent_factory(_deps_with_settings(app.state.deps, settings)), settings)
        for model_id, settings in model_settings.items()
    }
    app.state.agent = app.state.model_agents[
        app.state.deps.settings.provider_id
    ][0]
    app.state.projects = ProjectRepository(
        app.state.root / "var" / "drainage.sqlite3",
        app.state.root / "var" / "projects",
    )
    app.state.data_importer = BatchDataImporter(
        app.state.root / "var" / "drainage.sqlite3",
        app.state.root / "var" / "projects",
    )
    app.state.import_profiles = ImportProfileRepository(
        str(app.state.root / "var" / "drainage.sqlite3")
    )
    app.state.mapping_suggester = (
        mapping_suggester
        if mapping_suggester is not None
        else _default_mapping_suggester(app.state.deps)
    )
    app.state.analysis_runner = AnalysisRunner(
        app.state.root / "var" / "drainage.sqlite3",
        app.state.root / "var" / "projects",
    )
    app.state.filter_baselines = app.state.analysis_runner.baselines
    app.state.deps.filter_baselines = app.state.filter_baselines
    app.state.background_jobs = BackgroundJobService(
        app.state.root / "var" / "drainage.sqlite3",
        app.state.analysis_runner,
        max_workers=background_job_workers,
    )
    app.state.deps.analysis_runner = app.state.analysis_runner
    app.state.deps.background_jobs = app.state.background_jobs
    builtin_report_template = app.state.deps.paths.report_template_file
    if not builtin_report_template.is_file():
        builtin_report_template = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "contract_report_template.docx"
        )
    app.state.report_templates = ReportTemplateService(
        app.state.root / "var" / "drainage.sqlite3",
        app.state.root / "var" / "projects",
        builtin_report_template,
    )
    app.state.deps.report_templates = app.state.report_templates
    app.state.run_records = RunRecorder(
        app.state.root / "var" / "drainage.sqlite3"
    )
    app.state.conversations = ConversationRunner(
        ConversationRepository(app.state.root / "var" / "drainage.sqlite3"),
        app.state.agent,
        app.state.deps,
        app.state.root / "var" / "projects",
        app.state.run_records,
        model_agents=app.state.model_agents,
    )

    @app.get("/api/models")
    def list_chat_models() -> dict[str, object]:
        return {
            "default": app.state.deps.settings.provider_id,
            "models": [
                {
                    "id": model_id,
                    "name": settings.display_name,
                    "model": settings.model,
                }
                for model_id, (_, settings) in app.state.model_agents.items()
            ],
        }
    app.state.current_project_id: str | None = None
    app.state.current_batch_id: str | None = None

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html_path = Path(__file__).resolve().parent / "static" / "index.html"
        return HTMLResponse(
            html_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

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
        batch = (
            app.state.projects.get_batch(project.id, app.state.current_batch_id)
            if project and app.state.current_batch_id
            else None
        )
        return {
            "current_project": _project_data(project) if project else None,
            "current_workspace": _batch_data(batch) if batch else None,
        }

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, str]:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        return _project_data(project)

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
            "current_project": _project_data(project),
            "current_workspace": _batch_data(workspace),
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
        app.state.deps.current_project_id = project_id
        app.state.deps.current_batch_id = batch.id
        return {"current_batch": _batch_data(batch)}

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/imports",
        status_code=201,
    )
    async def import_batch_data(
        project_id: str,
        batch_id: str,
        file: UploadFile = File(...),
        profile_id: str | None = None,
        source_identifier: str | None = None,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        profile = None
        if profile_id:
            profile = app.state.import_profiles.get(project_id, profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="导入配置不存在")
        try:
            inspection = app.state.data_importer.inspect_upload(
                project_id,
                batch_id,
                file.filename or "",
                await _read_upload(file),
                profile_id=profile.id if profile else None,
                source_identifier=(
                    profile.source_identifier if profile else source_identifier
                ),
                profile_mapping=profile.mapping if profile else None,
                profile_units=profile.source_units if profile else None,
                parsing_rules=profile.parsing_rules if profile else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return inspection.as_dict()

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/batch-imports",
        status_code=201,
    )
    async def import_batch_files(
        project_id: str,
        batch_id: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        if not files:
            raise HTTPException(status_code=400, detail="请至少选择一个监测数据文件")
        inspections = []
        try:
            for upload in files:
                inspections.append(
                    app.state.data_importer.inspect_upload(
                        project_id,
                        batch_id,
                        upload.filename or "",
                        await _read_upload(upload),
                    ).as_dict()
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"imports": inspections}

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/imports/{import_id}/mapping-suggestions"
    )
    def suggest_import_mapping(
        project_id: str,
        batch_id: str,
        import_id: str,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        inspection = app.state.data_importer.inspection(
            project_id, batch_id, import_id
        )
        if inspection is None:
            raise HTTPException(status_code=404, detail="导入记录不存在")
        unresolved = [
            {"source": column["source"], "type": column["type"]}
            for column in inspection["columns"]
            if column["field"] is None
        ]
        candidates: list[dict[str, object]] = []
        if unresolved:
            suggested = app.state.mapping_suggester.suggest(
                source_identifier=inspection.get("source_identifier"),
                columns=unresolved,
            )
            candidates = [
                item if isinstance(item, dict) else asdict(item)
                for item in suggested
                if (
                    item.get("source") if isinstance(item, dict) else item.source
                )
                in {column["source"] for column in unresolved}
                and (
                    item.get("field") if isinstance(item, dict) else item.field
                )
                in STANDARD_FLOW_COLUMNS
            ]
        return {
            "status": "awaiting_engineer_confirmation",
            "candidates": candidates,
        }

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/imports/{import_id}/raw"
    )
    def download_raw_batch_data(
        project_id: str,
        batch_id: str,
        import_id: str,
    ) -> FileResponse:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        path = app.state.data_importer.raw_file(project_id, batch_id, import_id)
        if path is None:
            raise HTTPException(status_code=404, detail="原始监测数据不存在")
        return FileResponse(path, filename=path.name)

    @app.put(
        "/api/projects/{project_id}/batches/{batch_id}/imports/{import_id}/mapping"
    )
    def confirm_import_mapping(
        project_id: str,
        batch_id: str,
        import_id: str,
        request: ImportMappingRequest,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            result = app.state.data_importer.confirm_mapping(
                project_id,
                batch_id,
                import_id,
                request.mapping,
                request.units,
            )
            if result["replaced_existing"]:
                _invalidate_derived_state(project_id, batch_id)
            result["derived_state_reset"] = bool(result["replaced_existing"])
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put(
        "/api/projects/{project_id}/batches/{batch_id}/batch-imports/mapping"
    )
    def confirm_batch_import_mappings(
        project_id: str,
        batch_id: str,
        request: BatchImportMappingRequest,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            result = app.state.data_importer.confirm_batch_mappings(
                project_id,
                batch_id,
                [item.model_dump() for item in request.imports],
            )
            if result["replaced_existing"]:
                _invalidate_derived_state(project_id, batch_id)
            result["derived_state_reset"] = bool(result["replaced_existing"])
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/standard-flow-template")
    def download_standard_flow_template() -> Response:
        content = (
            "数据时间,设备编号,点位编号,流量(L/s),液位(m),流速(m/s)\n"
            "2026-01-01 00:00:00,D001,W1,12.5,1.23,0.45\n"
        )
        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                    'attachment; filename="standard_flow_template.csv"'
            },
        )

    @app.get("/api/projects/{project_id}/batches/{batch_id}/standard/flow")
    def get_standard_flow(project_id: str, batch_id: str) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        preview = app.state.data_importer.standard_preview(project_id, batch_id)
        if preview is None:
            raise HTTPException(status_code=409, detail="标准数据尚未确认生成")
        return preview

    def _auxiliary_frame(content: bytes, filename: str) -> pd.DataFrame:
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv":
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    return pd.read_csv(io.BytesIO(content), encoding=encoding)
                except UnicodeDecodeError:
                    continue
            raise ValueError("CSV 编码无法识别")
        if suffix in ALLOWED_SITE_EXTENSIONS:
            return pd.read_excel(io.BytesIO(content))
        raise ValueError("辅助数据文件类型不受支持")

    def _auxiliary_columns(frame: pd.DataFrame, kind: str) -> list[dict[str, str | None]]:
        aliases = {
            "rainfall": {
                "timestamp": {
                    "timestamp", "time", "date", "时间", "日期", "数据时间"
                },
                "rain_mm": {"rain", "rain_mm", "雨量", "降雨量", "降雨量(mm)", "日降雨量(mm)"},
            },
            "sites": {
                "point_id": {
                    "point_id", "点位", "点位编号", "监测点位",
                    "安装点位", "安装监测点位", "监测点编号",
                },
                "device_type": {"device_type", "设备类型", "类型"},
                "shape": {"shape", "形状", "管道形状", "管形状", "绑定管形状"},
                "diameter_m": {"diameter_m", "管径", "管径(m)", "管径（m）"},
                "well_depth_m": {"well_depth_m", "井深", "井深(m)", "井深（m）"},
                "install_time": {
                    "install_time", "设备安装时间", "安装时间", "监测设备安装时间"
                },
                "pipe_type": {"pipe_type", "管道类型", "管材", "管网类型"},
            },
        }[kind]
        return [
            {
                "source": str(source),
                "field": next(
                    (field for field, names in aliases.items() if str(source).strip() in names),
                    None,
                ),
                "type": str(frame[source].dtype),
            }
            for source in frame.columns
        ]

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/auxiliary/inspect"
    )
    async def inspect_auxiliary_data(
        project_id: str,
        batch_id: str,
        rainfall_file: UploadFile | None = File(default=None),
        site_info_file: UploadFile | None = File(default=None),
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        result: dict[str, object] = {}
        try:
            if rainfall_file and rainfall_file.filename:
                content = await _read_upload(rainfall_file)
                frame = _auxiliary_frame(content, rainfall_file.filename)
                result["rainfall"] = {
                    "filename": rainfall_file.filename,
                    "row_count": len(frame),
                    "columns": _auxiliary_columns(frame, "rainfall"),
                }
            if site_info_file and site_info_file.filename:
                content = await _read_upload(site_info_file)
                frame = _auxiliary_frame(content, site_info_file.filename)
                result["sites"] = {
                    "filename": site_info_file.filename,
                    "row_count": len(frame),
                    "columns": _auxiliary_columns(frame, "sites"),
                }
        except (ValueError, ImportError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not result:
            raise HTTPException(status_code=400, detail="请至少选择一个辅助数据文件")
        return result

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/auxiliary/confirm"
    )
    async def confirm_auxiliary_data(
        project_id: str,
        batch_id: str,
        mappings: str = Form(...),
        rainfall_file: UploadFile | None = File(default=None),
        site_info_file: UploadFile | None = File(default=None),
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            mapping_data = json.loads(mappings)
            standard_root = app.state.data_importer.standard_flow_path(
                project_id, batch_id
            ).parent
            standard_root.mkdir(parents=True, exist_ok=True)
            saved: list[str] = []
            source_filenames: dict[str, str] = {}
            specs = [
                (
                    "rainfall",
                    rainfall_file,
                    ["timestamp", "rain_mm"],
                    ["timestamp", "rain_mm"],
                    "rainfall.csv",
                ),
                (
                    "sites",
                    site_info_file,
                    [
                        "point_id",
                        "device_type",
                        "shape",
                        "diameter_m",
                        "well_depth_m",
                        "install_time",
                        "pipe_type",
                    ],
                    ["point_id", "diameter_m", "well_depth_m"],
                    "sites.csv",
                ),
            ]
            for kind, upload, fields, required, target_name in specs:
                if upload is None or not upload.filename:
                    continue
                frame = _auxiliary_frame(await _read_upload(upload), upload.filename)
                mapping = mapping_data.get(kind, {})
                missing = [field for field in required if field not in mapping.values()]
                if missing:
                    raise ValueError(f"{upload.filename} 缺少字段匹配: {', '.join(missing)}")
                normalized = pd.DataFrame(
                    {
                        field: (
                            frame[
                                next(
                                    source
                                    for source, target in mapping.items()
                                    if target == field
                                )
                            ]
                            if field in mapping.values()
                            else pd.Series([""] * len(frame), index=frame.index)
                        )
                        for field in fields
                    }
                )
                normalized.to_csv(standard_root / target_name, index=False, encoding="utf-8")
                saved.append(target_name)
                source_filenames[kind] = upload.filename
        except (ValueError, KeyError, json.JSONDecodeError, StopIteration) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not saved:
            raise HTTPException(
                status_code=400,
                detail="辅助数据文件已失效，请重新选择后再确认",
            )
        manifest_path = standard_root / "auxiliary_manifest.json"
        existing_manifest = {}
        if manifest_path.is_file():
            try:
                existing_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                existing_manifest = {}
        existing_manifest.update(source_filenames)
        manifest_path.write_text(
            json.dumps(existing_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"saved": saved, "message": "辅助数据已确认并生成标准文件。"}

    def _archive_chat_and_clear_analysis(
        project_id: str,
        batch_id: str,
    ) -> None:
        database = app.state.root / "var" / "drainage.sqlite3"
        archived_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS archived_agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO archived_agent_sessions
                SELECT session_id, project_id, batch_id, history_json,
                       state_json, created_at, updated_at, ?
                FROM agent_sessions
                WHERE project_id = ? AND batch_id = ?
                """,
                (archived_at, project_id, batch_id),
            )
            for table in (
                "agent_sessions",
                "current_analysis_results",
                "analysis_runs",
                "background_jobs",
                "report_drafts",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE project_id = ? AND batch_id = ?",
                    (project_id, batch_id),
                )
            run_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT run_id FROM agent_runs
                    WHERE project_id = ? AND batch_id = ?
                    """,
                    (project_id, batch_id),
                )
            ]
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                connection.execute(
                    f"DELETE FROM agent_run_steps WHERE run_id IN ({placeholders})",
                    run_ids,
                )
            connection.execute(
                "DELETE FROM agent_runs WHERE project_id = ? AND batch_id = ?",
                (project_id, batch_id),
            )
        root = app.state.projects.batch_workspace(project_id, batch_id)
        for directory in ("results", "jobs", "sessions"):
            target = (root / directory).resolve()
            if target.is_relative_to(root.resolve()) and target.is_dir():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
        exports_dir = (root / "exports").resolve()
        if exports_dir.is_dir() and exports_dir.is_relative_to(root.resolve()):
            for path in sorted(exports_dir.rglob("*")):
                if path.is_file() and path.name != "筛选结果.xlsx":
                    path.unlink()
            for subdir in sorted(exports_dir.rglob("*"), reverse=True):
                if subdir.is_dir() and not any(subdir.iterdir()):
                    subdir.rmdir()

    def _invalidate_derived_state(
        project_id: str,
        batch_id: str,
    ) -> None:
        """Clear outputs derived from monitoring data, preserving auxiliary inputs."""
        _archive_chat_and_clear_analysis(project_id, batch_id)
        database = app.state.root / "var" / "drainage.sqlite3"
        with sqlite3.connect(database) as connection:
            for table in (
                "current_analysis_baselines",
                "analysis_baselines",
                "filter_results",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE project_id = ? AND batch_id = ?",
                    (project_id, batch_id),
                )
        root = app.state.projects.batch_workspace(project_id, batch_id)
        baseline_root = (root / "baseline").resolve()
        if (
            baseline_root.is_relative_to(root.resolve())
            and baseline_root.is_dir()
        ):
            shutil.rmtree(baseline_root)
        baseline_root.mkdir(parents=True, exist_ok=True)

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
                await _read_upload(file),
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
                await _read_upload(file),
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
                _archive_chat_and_clear_analysis(project_id, batch_id)
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

    def job_data(job: BackgroundJob) -> dict[str, object]:
        data = asdict(job)
        data["result_url"] = (
            f"/api/projects/{job.project_id}/batches/{job.batch_id}"
            f"/analysis-results/{job.result_run_id}"
            if job.result_run_id
            else None
        )
        return data

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
                / _safe_upload_name(upload, ALLOWED_PROJECT_EXTENSIONS),
            )
            for upload in files
        ]
        return {"saved": saved}

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

    def _current_workspace_artifacts(
        project_id: str,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        root = app.state.projects.batch_workspace(project_id, workspace_id)
        files: list[dict[str, Any]] = []
        current_results = {
            algorithm: app.state.analysis_runner.current(
                project_id, workspace_id, algorithm
            )
            for algorithm in (
                "data_quality", "patterns", "rainfall",
                "event_response", "rdii", "risk",
            )
        }
        labels = {
            "data_quality": "数据质量结果",
            "patterns": "排污规律结果",
            "rainfall": "降雨分析结果",
            "event_response": "降雨响应结果",
            "rdii": "RDII 分析结果",
            "risk": "风险分析结果",
        }
        for algorithm, result in current_results.items():
            if result is None:
                continue
            for artifact in result.artifacts:
                path = root / artifact
                if path.is_file():
                    files.append({
                        "path": artifact,
                        "name": labels[algorithm],
                        "size": path.stat().st_size,
                    })
        baseline = app.state.filter_baselines.current_baseline(
            project_id, workspace_id
        )
        if baseline is not None:
            path = root / baseline.artifact
            if path.is_file():
                files.append({
                    "path": baseline.artifact,
                    "name": "当前筛选结果",
                    "size": path.stat().st_size,
                })
        for draft in app.state.report_templates.list_drafts(
            project_id, workspace_id
        )[-1:]:
            for artifact, label in (
                (draft.docx, "当前报告初稿"),
                (draft.workbook, "当前综合结果表"),
            ):
                path = root / artifact
                if path.is_file():
                    files.append({
                        "path": artifact,
                        "name": label,
                        "size": path.stat().st_size,
                    })
        return files

    @app.get("/api/projects/{project_id}/workspace/artifacts")
    def list_project_artifacts(project_id: str) -> dict[str, object]:
        if app.state.projects.get(project_id) is None:
            raise HTTPException(status_code=404, detail="监测项目不存在")
        workspace = app.state.projects.get_or_create_workspace(project_id)
        return {
            "workspace_id": workspace.id,
            "files": _current_workspace_artifacts(project_id, workspace.id),
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
                        if path.is_file():
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
                        if path.is_file():
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
        _archive_chat_and_clear_analysis(project_id, workspace.id)
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
                await _read_upload(file),
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
            "project": _project_data(project),
            "batch": _batch_data(batch),
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

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        project_id = request.project_id
        batch_id = request.batch_id
        if not project_id or not batch_id:
            raise HTTPException(
                status_code=409,
                detail="请先选择当前监测项目和分析批次",
            )
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(
                status_code=404,
                detail="当前监测项目或分析批次不存在",
            )
        try:
            workspace_root = app.state.projects.batch_workspace(project_id, batch_id)
            before_paths = {
                item["path"]
                for item in _current_workspace_artifacts(project_id, batch_id)
            }
            before_files = _workspace_file_paths(workspace_root)
            turn = app.state.conversations.run(
                project_id=project_id,
                batch_id=batch_id,
                message=message,
                session_id=request.session_id,
                debug=request.debug,
                model_id=request.model_id,
            )
            artifacts = _select_chat_artifacts(
                _current_workspace_artifacts(project_id, batch_id),
                before_paths,
            )
            if not artifacts and _requests_file_download(message):
                artifacts = _select_requested_downloads(
                    workspace_root,
                    before_files,
                    turn.reply,
                    turn.run_id,
                )
            return ChatResponse(
                session_id=turn.session_id,
                run_id=turn.run_id,
                reply=turn.reply,
                artifacts=artifacts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            app.state.deps.logger.exception("Web agent turn failed")
            raise HTTPException(
                status_code=500, detail=f"Agent 调用失败: {exc}"
            ) from exc

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/agent-runs"
    )
    def list_agent_runs(
        project_id: str, batch_id: str, limit: int = 100
    ) -> list[dict[str, object]]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        return [
            asdict(record)
            for record in app.state.run_records.list(
                project_id, batch_id, limit=limit
            )
        ]

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/agent-runs/{run_id}"
    )
    def get_agent_run(
        project_id: str, batch_id: str, run_id: str
    ) -> dict[str, object]:
        record = app.state.run_records.get(project_id, batch_id, run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Agent 运行记录不存在")
        return record

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
            name = _safe_upload_name(upload, ALLOWED_FLOW_EXTENSIONS)
            saved.append("resources/data/flow/" + _save_upload(upload, deps.paths.flow_dir / name))

        if rainfall_file is not None and rainfall_file.filename:
            _safe_upload_name(rainfall_file, ALLOWED_RAINFALL_EXTENSIONS)
            saved.append("resources/data/" + _save_upload(rainfall_file, deps.paths.rainfall_file))

        if site_info_file is not None and site_info_file.filename:
            _safe_upload_name(site_info_file, ALLOWED_SITE_EXTENSIONS)
            saved.append("resources/data/" + _save_upload(site_info_file, deps.paths.site_info_file))

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
