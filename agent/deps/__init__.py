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
    notes: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        root = root.resolve()
        return cls(
            root=root,
            data=root / "data",
            outputs=root / "outputs",
            workspace=root / "workspace",
            logs=root / "logs",
            templates=root / "templates",
            notes=root / "docs" / "PROJECT_NOTES.md",
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

    @classmethod
    def from_env(cls) -> "AgentSettings":
        return cls(
            model=os.getenv("AGENT_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat")),
            base_url=os.getenv("AGENT_BASE_URL", os.getenv("DEEPSEEK_BASE_URL")),
            api_key=os.getenv("AGENT_API_KEY", os.getenv("DEEPSEEK_API_KEY")),
        )


@dataclass
class SessionState:
    selected_event_ids: list[int] = field(default_factory=list)
    window_event_id_map: dict[int, int] = field(default_factory=dict)
    unavailable_event_ids: list[int] = field(default_factory=list)
    skip_confirmations: bool = False
    auto_confirm_filter_result: bool = False
    pending_filter_result_path: str | None = None
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
    project_notes: str = ""
    trace: Any | None = None


def ensure_directories(paths: Paths) -> None:
    for path in (paths.data, paths.flow_dir, paths.outputs, paths.workspace, paths.logs, paths.templates):
        path.mkdir(parents=True, exist_ok=True)
    paths.notes.parent.mkdir(parents=True, exist_ok=True)
    if not paths.notes.exists():
        paths.notes.write_text("# Project Notes\n\n", encoding="utf-8")


def build_deps(root: Path | None = None) -> AgentDeps:
    paths = Paths.from_root(root or Path.cwd())
    load_dotenv(paths.root / ".env")
    ensure_directories(paths)
    logger = logging.getLogger("drainage_agent")
    notes = paths.notes.read_text(encoding="utf-8") if paths.notes.exists() else ""
    return AgentDeps(paths=paths, settings=AgentSettings.from_env(), logger=logger, project_notes=notes)
