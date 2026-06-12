from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from .deps import AgentDeps
from .tools.inspect_tools import list_results_impl
from .tools.memory_tool import record_note_impl
from .tools.module_tools import (
    analyze_event_response_impl,
    analyze_patterns_impl,
    analyze_rainfall_impl,
    analyze_rdii_impl,
    assess_risk_impl,
    check_data_impl,
    generate_report_impl,
    query_stats_impl,
)
from .tools.python_tool import run_python_impl


def load_system_prompt(root: Path, project_notes: str = "") -> str:
    prompt_path = root / "agent" / "prompts" / "system.md"
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    if project_notes.strip():
        prompt += "\n\n## Project Notes\n\n" + project_notes.strip() + "\n"
    return prompt


def build_agent(deps: AgentDeps) -> Any:
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider

        provider_kwargs = {}
        if deps.settings.base_url:
            provider_kwargs["base_url"] = deps.settings.base_url
        if deps.settings.api_key:
            provider_kwargs["api_key"] = deps.settings.api_key
        try:
            model = OpenAIModel(deps.settings.model, provider=OpenAIProvider(**provider_kwargs))
        except Exception as exc:
            raise RuntimeError(
                "OpenAI-compatible model initialization failed. Check AGENT_API_KEY/AGENT_BASE_URL/AGENT_MODEL in .env."
            ) from exc

        agent = Agent(model, deps_type=AgentDeps, system_prompt=load_system_prompt(deps.paths.root, deps.project_notes))

        @agent.tool
        def query_stats(
            ctx: RunContext[AgentDeps],
            points: list[str] | None = None,
            time_range: list[str] | None = None,
            dry_only: bool = True,
            metrics: list[str] | None = None,
            aggs: list[str] | None = None,
            clean: bool = True,
        ) -> dict:
            """按条件聚合统计流量、液位、流速。dry_only 默认 True。"""
            return query_stats_impl(ctx.deps, points=points, time_range=time_range, dry_only=dry_only, metrics=metrics, aggs=aggs, clean=clean)

        @agent.tool
        def check_data(ctx: RunContext[AgentDeps], points: list[str] | None = None) -> dict:
            """检查数据收集率、缺失、异常概况与格式问题。"""
            return check_data_impl(ctx.deps, points=points)

        @agent.tool
        def analyze_rainfall(
            ctx: RunContext[AgentDeps],
            time_range: list[str] | None = None,
            output: str = "all",
            rainfall_gap_hours: int = 12,
        ) -> dict:
            """分析降雨日统计、降雨场次和降雨输出。output: all/daily/events/charts。"""
            return analyze_rainfall_impl(ctx.deps, time_range=time_range, output=output, rainfall_gap_hours=rainfall_gap_hours)

        @agent.tool
        def analyze_event_response(ctx: RunContext[AgentDeps], event_ids: list[int] | None = None, points: list[str] | None = None) -> dict:
            """统计降雨事件期间各点位响应指标；event_ids 未给时返回 needs_input。"""
            return analyze_event_response_impl(ctx.deps, event_ids=event_ids, points=points)

        @agent.tool
        def analyze_patterns(ctx: RunContext[AgentDeps], points: list[str] | None = None, output: str = "all") -> dict:
            """分析排污规律并生成旱天特征曲线底料。"""
            return analyze_patterns_impl(ctx.deps, points=points, output=output)

        @agent.tool
        def analyze_rdii(
            ctx: RunContext[AgentDeps],
            event_ids: list[int] | None = None,
            points: list[str] | None = None,
            output: str = "all",
        ) -> dict:
            """计算指定降雨事件的 RDII 指标；event_ids 未给时返回 needs_input。"""
            return analyze_rdii_impl(ctx.deps, event_ids=event_ids, points=points, output=output)

        @agent.tool
        def assess_risk(ctx: RunContext[AgentDeps], scope: str = "all", event_ids: list[int] | None = None) -> dict:
            """评估运行风险。scope: all/dry/rainy。"""
            return assess_risk_impl(ctx.deps, scope=scope, event_ids=event_ids)

        @agent.tool
        def generate_report(ctx: RunContext[AgentDeps], sections: list[str] | None = None, event_ids: list[int] | None = None) -> dict:
            """生成排水监测分析报告。"""
            return generate_report_impl(ctx.deps, sections=sections, event_ids=event_ids)

        @agent.tool
        def list_results(ctx: RunContext[AgentDeps]) -> dict:
            """列出已有结果、manifest 与新鲜度。"""
            return list_results_impl(ctx.deps)

        @agent.tool
        def run_python(ctx: RunContext[AgentDeps], code: str) -> dict:
            """执行长尾现场 Python 分析，预置 analysis.io 数据访问函数。"""
            return run_python_impl(ctx.deps, code)

        @agent.tool
        def record_note(ctx: RunContext[AgentDeps], note: str) -> dict:
            """写入项目记忆。"""
            return record_note_impl(ctx.deps, note)

        return agent
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run `pip install -r requirements.txt`.") from exc
