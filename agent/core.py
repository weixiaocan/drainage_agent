from __future__ import annotations

from pathlib import Path

from .deps import AgentDeps
from .tools.inspect_tools import describe_data_impl, list_results_impl
from .tools.memory_tool import record_note_impl
from .tools.module_tools import (
    run_data_filter_impl,
    run_data_stats_impl,
    run_dry_analysis_impl,
    run_event_stats_impl,
    run_pattern_analysis_impl,
    run_rainfall_analysis_impl,
    run_rdii_analysis_impl,
    run_report_assembler_impl,
    run_risk_analysis_impl,
)
from .tools.python_tool import run_python_impl


def load_system_prompt(root: Path, project_notes: str = "") -> str:
    prompt_path = root / "agent" / "prompts" / "system.md"
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    if project_notes.strip():
        prompt += "\n\n## Project Notes\n\n" + project_notes.strip() + "\n"
    return prompt


def build_agent(deps: AgentDeps):
    try:
        from pydantic_ai import Agent
        try:
            from pydantic_ai.models.openai import OpenAIModel
            from pydantic_ai.providers.openai import OpenAIProvider

            provider_kwargs = {}
            if deps.settings.base_url:
                provider_kwargs["base_url"] = deps.settings.base_url
            if deps.settings.api_key:
                provider_kwargs["api_key"] = deps.settings.api_key
            model = OpenAIModel(deps.settings.model, provider=OpenAIProvider(**provider_kwargs))
        except Exception:
            model = deps.settings.model

        agent = Agent(model, deps_type=AgentDeps, system_prompt=load_system_prompt(deps.paths.root, deps.project_notes))

        @agent.tool
        def describe_data(ctx) -> dict:
            return describe_data_impl(ctx.deps)

        @agent.tool
        def list_results(ctx) -> dict:
            return list_results_impl(ctx.deps)

        @agent.tool
        def run_data_stats(ctx) -> dict:
            return run_data_stats_impl(ctx.deps)

        @agent.tool
        def run_data_filter(ctx, missing_rate_threshold: float = 0.1) -> dict:
            return run_data_filter_impl(ctx.deps, missing_rate_threshold=missing_rate_threshold)

        @agent.tool
        def run_dry_analysis(ctx, smooth_window_minutes: int = 20) -> dict:
            return run_dry_analysis_impl(ctx.deps, smooth_window_minutes=smooth_window_minutes)

        @agent.tool
        def run_rainfall_analysis(ctx, rainfall_gap_hours: int = 12) -> dict:
            return run_rainfall_analysis_impl(ctx.deps, rainfall_gap_hours=rainfall_gap_hours)

        @agent.tool
        def run_event_stats(ctx, event_ids: list[int]) -> dict:
            return run_event_stats_impl(ctx.deps, event_ids=event_ids)

        @agent.tool
        def run_rdii_analysis(ctx, event_ids: list[int]) -> dict:
            return run_rdii_analysis_impl(ctx.deps, event_ids=event_ids)

        @agent.tool
        def run_pattern_analysis(ctx) -> dict:
            return run_pattern_analysis_impl(ctx.deps)

        @agent.tool
        def run_risk_analysis(ctx, event_ids: list[int] | None = None) -> dict:
            return run_risk_analysis_impl(ctx.deps, event_ids=event_ids)

        @agent.tool
        def run_report_assembler(ctx) -> dict:
            return run_report_assembler_impl(ctx.deps)

        @agent.tool
        def run_python(ctx, code: str) -> dict:
            return run_python_impl(ctx.deps, code)

        @agent.tool
        def record_note(ctx, note: str) -> dict:
            return record_note_impl(ctx.deps, note)

        return agent
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run `pip install -r requirements.txt`.") from exc

