from __future__ import annotations

import os
from collections import defaultdict, deque
from copy import copy
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from analysis.jobs import BackgroundJobService
from analysis.report_templates import ReportTemplateService
from analysis.runs import AnalysisRunner
from agent.core import build_agent
from agent.conversations import ConversationRepository, ConversationRunner
from agent.deps import AgentDeps, available_agent_settings, build_deps
from agent.run_records import RunRecorder
from web.projects import ProjectRepository
from web.import_profiles import (
    ImportProfileRepository,
    LLMMappingSuggester,
    MappingSuggester,
    NoMappingSuggester,
)
from web.standard_data import BatchDataImporter
from web.routes import analysis, chat, files, imports, projects, reports


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEMO_TIMEZONE = timezone(timedelta(hours=8))


def _demo_request_is_blocked(method: str, path: str) -> bool:
    """Keep public demos useful while denying data replacement and destructive APIs."""
    if path.endswith("/raw") or path.endswith("/downloads/all"):
        return True
    if method == "DELETE":
        return True
    if method not in {"POST", "PUT", "PATCH"}:
        return False
    if path == "/api/projects" or path.endswith("/batches"):
        return True
    blocked_markers = (
        "/imports",
        "/auxiliary-data",
        "/chat-attachments",
        "/filters/upload",
        "/revisions",
        "/workspace/reset",
        "/report-templates",
    )
    if any(marker in path for marker in blocked_markers):
        return True
    return method == "POST" and path.endswith("/files")


def _deps_with_settings(deps: AgentDeps, settings: Any) -> AgentDeps:
    model_deps = copy(deps)
    model_deps.settings = settings
    return model_deps


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
    demo_mode = _env_flag("DRAINAGE_DEMO_MODE")
    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI):
        yield
        lifespan_app.state.background_jobs.shutdown()

    app = FastAPI(
        title="Drainage Agent",
        docs_url=None if demo_mode else "/docs",
        redoc_url=None if demo_mode else "/redoc",
        lifespan=lifespan,
    )
    app.state.demo_mode = demo_mode
    app.state.demo_rate_hits = defaultdict(deque)
    app.state.demo_rate_lock = Lock()
    app.state.demo_active_chats = 0
    app.state.demo_requests_per_minute = max(
        1, int(os.getenv("DRAINAGE_DEMO_REQUESTS_PER_MINUTE", "3"))
    )
    app.state.demo_max_concurrent_chats = max(
        1, int(os.getenv("DRAINAGE_DEMO_MAX_CONCURRENT_CHATS", "2"))
    )
    app.state.demo_daily_per_visitor = max(
        1, int(os.getenv("DRAINAGE_DEMO_DAILY_CHATS_PER_VISITOR", "20"))
    )
    app.state.demo_daily_total = max(1, int(os.getenv("DRAINAGE_DEMO_DAILY_CHATS_TOTAL", "300")))
    app.state.demo_daily_day = None
    app.state.demo_daily_counts = defaultdict(int)

    @app.middleware("http")
    async def public_demo_guard(request: Request, call_next: Callable[..., Any]) -> Response:
        if app.state.demo_mode and _demo_request_is_blocked(request.method, request.url.path):
            return JSONResponse(
                status_code=403,
                content={"detail": "公开演示环境不允许上传、替换或删除数据。"},
            )
        is_chat = app.state.demo_mode and request.method == "POST" and request.url.path == "/api/chat"
        if not is_chat:
            return await call_next(request)

        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",", 1)[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        now = monotonic()
        with app.state.demo_rate_lock:
            hits = app.state.demo_rate_hits[client_ip]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= app.state.demo_requests_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "演示请求过于频繁，请一分钟后再试。"},
                    headers={"Retry-After": "60"},
                )
            # Daily quotas cap model cost; counts live in memory and restart with the process.
            today = datetime.now(DEMO_TIMEZONE).date()
            if app.state.demo_daily_day != today:
                app.state.demo_daily_day = today
                app.state.demo_daily_counts = defaultdict(int)
            counts = app.state.demo_daily_counts
            if counts[client_ip] >= app.state.demo_daily_per_visitor:
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"今天的演示提问次数已用完（每人每天 {app.state.demo_daily_per_visitor} 次），请明天再来。"},
                )
            if sum(counts.values()) >= app.state.demo_daily_total:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "今天全站的演示提问次数已用完，请明天再来。"},
                )
            if app.state.demo_active_chats >= app.state.demo_max_concurrent_chats:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "演示任务正在排队，请稍后重试。"},
                    headers={"Retry-After": "10"},
                )
            hits.append(now)
            counts[client_ip] += 1
            app.state.demo_active_chats += 1
        try:
            return await call_next(request)
        finally:
            with app.state.demo_rate_lock:
                app.state.demo_active_chats -= 1
    app.state.root = (root or Path.cwd()).resolve()
    app.state.deps = deps_factory(app.state.root)
    if app.state.deps.python_execution_requests is None:
        from agent.python_execution_requests import PythonExecutionRequestRepository

        app.state.deps.python_execution_requests = PythonExecutionRequestRepository(
            app.state.root / "var" / "drainage.sqlite3"
        )
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

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"status": "ok", "demo_mode": app.state.demo_mode}

    @app.get("/api/demo")
    def demo_status() -> dict[str, bool]:
        return {"enabled": app.state.demo_mode}
    app.state.current_project_id: str | None = None
    app.state.current_batch_id: str | None = None

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html_path = Path(__file__).resolve().parent / "static" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        if app.state.demo_mode:
            html = html.replace("<body>", '<body class="demo-mode">', 1)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store"},
        )

    for register in (
        projects.register, imports.register, analysis.register, files.register,
        reports.register, chat.register,
    ):
        register(app)

    return app


app = create_app()
