from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    outputs: Path
    workspace: Path
    logs: Path
    templates: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        root = root.resolve()
        return cls(
            root=root,
            data=root / "resources" / "data",
            outputs=root / "var" / "outputs",
            workspace=root / "var" / "workspace",
            logs=root / "var" / "logs",
            templates=root / "resources" / "templates",
        )

    @property
    def flow_dir(self) -> Path:
        return self.data / "flow"

    @property
    def rainfall_file(self) -> Path:
        return self.data / "降雨数据.csv"

    @property
    def site_info_file(self) -> Path:
        return self.data / "点位信息.xlsx"

    @property
    def report_template_file(self) -> Path:
        candidates = sorted(self.templates.glob("*.docx"))
        return candidates[0] if candidates else self.templates / "监测数据分析报告模板.docx"

    @property
    def combined_xlsx(self) -> Path:
        return self.outputs / "综合分析结果.xlsx"

    @property
    def filter_result(self) -> Path:
        return self.outputs / "筛选结果.xlsx"

    @property
    def manifest(self) -> Path:
        return self.outputs / "manifest.json"


@dataclass(frozen=True)
class AgentSettings:
    model: str
    base_url: str | None
    api_key: str | None
    provider_id: str = "deepseek"
    display_name: str = "DeepSeek"

    @classmethod
    def from_env(cls) -> "AgentSettings":
        return cls(
            model=os.getenv("AGENT_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat")),
            base_url=os.getenv("AGENT_BASE_URL", os.getenv("DEEPSEEK_BASE_URL")),
            api_key=os.getenv("AGENT_API_KEY", os.getenv("DEEPSEEK_API_KEY")),
        )


def available_agent_settings() -> dict[str, AgentSettings]:
    """Return configured chat-model adapters without exposing credentials."""
    default = AgentSettings.from_env()
    settings = {default.provider_id: default}
    glm_key = os.getenv("GLM_API_KEY")
    if glm_key:
        settings["glm"] = AgentSettings(
            model=os.getenv("GLM_MODEL", "glm-5.2"),
            base_url=os.getenv(
                "GLM_BASE_URL",
                "https://open.bigmodel.cn/api/paas/v4",
            ),
            api_key=glm_key,
            provider_id="glm",
            display_name="GLM-5.2",
        )
    return settings


@dataclass
class SessionState:
    selected_event_ids: list[int] = field(default_factory=list)
    window_event_id_map: dict[int, int] = field(default_factory=dict)
    unavailable_event_ids: list[int] = field(default_factory=list)
    skip_confirmations: bool = False
    auto_confirm_filter_result: bool = False
    pending_filter_result_path: str | None = None
    pending_filter_id: str | None = None
    pending_filter_result_identity: str | None = None
    pending_filter_result_params: dict[str, Any] = field(default_factory=dict)
    pending_filter_result_request: str | None = None
    pending_filter_result_message: str | None = None
    confirmed_filter_result_path: str | None = None
    confirmed_filter_result_identity: str | None = None
    confirmed_filter_result_params: dict[str, Any] = field(default_factory=dict)
    current_run_id: str | None = None
    current_user_prompt: str | None = None
    user_prompt_history: list[str] = field(default_factory=list)
    analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    report_data_cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDeps:
    paths: Paths
    settings: AgentSettings
    logger: logging.Logger
    session: SessionState = field(default_factory=SessionState)
    trace: Any | None = None
    analysis_runner: Any | None = None
    filter_baselines: Any | None = None
    background_jobs: Any | None = None
    report_templates: Any | None = None
    current_project_id: str | None = None
    current_batch_id: str | None = None
    cancel_session_id: str = ""
    python_execution_requests: Any | None = None
    python_sandbox: Any | None = None
    sandbox_jobs_root: Path | None = None


def ensure_directories(paths: Paths) -> None:
    for path in (paths.data, paths.flow_dir, paths.outputs, paths.workspace, paths.logs, paths.templates):
        path.mkdir(parents=True, exist_ok=True)


def build_deps(root: Path | None = None) -> AgentDeps:
    paths = Paths.from_root(root or Path.cwd())
    load_dotenv(paths.root / ".env")
    ensure_directories(paths)
    logger = logging.getLogger("drainage_agent")
    from agent.python_execution_requests import PythonExecutionRequestRepository

    deps = AgentDeps(
        paths=paths,
        settings=AgentSettings.from_env(),
        logger=logger,
        python_execution_requests=PythonExecutionRequestRepository(
            paths.root / "var" / "drainage.sqlite3"
        ),
        sandbox_jobs_root=Path(
            os.getenv("DRAINAGE_SANDBOX_JOBS_ROOT", str(paths.root / "var" / "sandbox-jobs"))
        ).resolve(),
    )
    controller_url = os.getenv("DRAINAGE_SANDBOX_CONTROLLER_URL", "").strip()
    controller_token = os.getenv("DRAINAGE_SANDBOX_CONTROLLER_TOKEN", "").strip()
    image_digest = os.getenv("DRAINAGE_SANDBOX_IMAGE_DIGEST", "").strip()
    if controller_url and controller_token and image_digest:
        from agent.docker_python_sandbox import DockerPythonSandbox, HttpControllerClient

        deps.python_sandbox = DockerPythonSandbox(
            HttpControllerClient(controller_url, controller_token),
            image_digest=image_digest,
        )
    elif controller_url or controller_token or image_digest:
        logger.warning(
            "Python sandbox remains disabled: controller URL, token, and image digest must all be configured"
        )
    return deps
