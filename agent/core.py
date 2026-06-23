from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from .deps import AgentDeps
from .logging_utils import summarize_tool_result, trace_event
from .tools.inspect_tools import list_results_impl
from .tools.memory_tool import record_note_impl
from .tools.module_tools import (
    analyze_event_response_impl,
    analyze_patterns_impl,
    analyze_rainfall_impl,
    analyze_rdii_impl,
    assess_risk_impl,
    check_data_impl,
    data_filter_impl,
    generate_report_impl,
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

        def traced_tool(ctx: RunContext[AgentDeps], tool_name: str, args: dict[str, Any], func: Any) -> dict:
            call_id = uuid.uuid4().hex
            run_id = ctx.deps.session.current_run_id
            trace_event(
                ctx.deps.trace,
                {
                    "event": "tool_call",
                    "run_id": run_id,
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "args": args,
                },
            )
            try:
                result = func()
            except Exception as exc:
                trace_event(
                    ctx.deps.trace,
                    {
                        "event": "tool_error",
                        "run_id": run_id,
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "error": repr(exc),
                    },
                )
                raise
            trace_event(
                ctx.deps.trace,
                {
                    "event": "tool_result",
                    "run_id": run_id,
                    "call_id": call_id,
                    "tool_name": tool_name,
                    **summarize_tool_result(result),
                },
            )
            return result

        @agent.tool
        def data_filter(
            ctx: RunContext[AgentDeps],
            missing_rate_threshold: float = 0.1,
            expected_rows_per_day: int = 1440,
            rain_day_filter_threshold: float = 2.0,
            zero_like_threshold: float = 0.02,
            high_zero_ratio_threshold: float = 0.5,
            high_zero_ratio_normal_days_threshold: int = 5,
            zero_day_drop_min_nonzero_keep_days: int = 3,
            mean_lower_ratio: float = 0.5,
            mean_upper_ratio: float = 2.0,
            output_file: str | None = None,
        ) -> dict:
            """按固定筛选规则生成筛选结果.xlsx，作为旱天分析前置结果。"""
            args = {
                "missing_rate_threshold": missing_rate_threshold,
                "expected_rows_per_day": expected_rows_per_day,
                "rain_day_filter_threshold": rain_day_filter_threshold,
                "zero_like_threshold": zero_like_threshold,
                "high_zero_ratio_threshold": high_zero_ratio_threshold,
                "high_zero_ratio_normal_days_threshold": high_zero_ratio_normal_days_threshold,
                "zero_day_drop_min_nonzero_keep_days": zero_day_drop_min_nonzero_keep_days,
                "mean_lower_ratio": mean_lower_ratio,
                "mean_upper_ratio": mean_upper_ratio,
                "output_file": output_file,
            }
            return traced_tool(ctx, "data_filter", args, lambda: data_filter_impl(ctx.deps, **args))

        @agent.tool
        def check_data(ctx: RunContext[AgentDeps], points: list[str] | None = None, export: bool = False) -> dict:
            """检查数据收集率、缺失、异常概况与格式问题。"""
            args = {"points": points, "export": export}
            return traced_tool(ctx, "check_data", args, lambda: check_data_impl(ctx.deps, **args))

        @agent.tool
        def analyze_rainfall(
            ctx: RunContext[AgentDeps],
            time_range: list[str] | None = None,
            output: str = "all",
            rainfall_gap_hours: int = 12,
        ) -> dict:
            """分析降雨日统计、降雨场次和降雨输出。output: all/daily/events/charts。"""
            args = {"time_range": time_range, "output": output, "rainfall_gap_hours": rainfall_gap_hours}
            return traced_tool(ctx, "analyze_rainfall", args, lambda: analyze_rainfall_impl(ctx.deps, **args))

        @agent.tool
        def analyze_event_response(
            ctx: RunContext[AgentDeps],
            event_ids: list[int] | None = None,
            points: list[str] | None = None,
            export: bool = False,
        ) -> dict:
            """统计降雨事件期间各点位响应指标；event_ids 未给时返回 needs_input。"""
            args = {"event_ids": event_ids, "points": points, "export": export}
            return traced_tool(ctx, "analyze_event_response", args, lambda: analyze_event_response_impl(ctx.deps, **args))

        @agent.tool
        def analyze_patterns(
            ctx: RunContext[AgentDeps],
            points: list[str] | None = None,
            output: str = "all",
            export: bool = False,
            start: str | None = None,
            end: str | None = None,
        ) -> dict:
            """分析排污规律并生成旱天特征曲线底料。"""
            args = {"points": points, "start": start, "end": end, "output": output, "export": export}
            return traced_tool(ctx, "analyze_patterns", args, lambda: analyze_patterns_impl(ctx.deps, **args))

        @agent.tool
        def analyze_rdii(
            ctx: RunContext[AgentDeps],
            event_ids: list[int] | None = None,
            points: list[str] | None = None,
            output: str = "all",
            export: bool = False,
        ) -> dict:
            """计算指定降雨事件的 RDII 指标；event_ids 未给时返回 needs_input。"""
            args = {"event_ids": event_ids, "points": points, "output": output, "export": export}
            return traced_tool(ctx, "analyze_rdii", args, lambda: analyze_rdii_impl(ctx.deps, **args))

        @agent.tool
        def assess_risk(
            ctx: RunContext[AgentDeps],
            scope: str = "all",
            event_ids: list[int] | None = None,
            points: list[str] | None = None,
            export: bool = False,
            start: str | None = None,
            end: str | None = None,
        ) -> dict:
            """评估运行风险。scope: all/dry/rainy。"""
            args = {
                "scope": scope,
                "event_ids": event_ids,
                "points": points,
                "start": start,
                "end": end,
                "export": export,
            }
            return traced_tool(ctx, "assess_risk", args, lambda: assess_risk_impl(ctx.deps, **args))

        @agent.tool
        def generate_report(ctx: RunContext[AgentDeps], sections: list[str] | None = None, event_ids: list[int] | None = None) -> dict:
            """生成排水监测分析报告。"""
            args = {"sections": sections, "event_ids": event_ids}
            return traced_tool(ctx, "generate_report", args, lambda: generate_report_impl(ctx.deps, **args))

        @agent.tool
        def list_results(ctx: RunContext[AgentDeps]) -> dict:
            """列出已有结果、manifest 与新鲜度。"""
            return traced_tool(ctx, "list_results", {}, lambda: list_results_impl(ctx.deps))

        @agent.tool
        def run_python(ctx: RunContext[AgentDeps], code: str) -> dict:
            """执行长尾现场 Python 分析，预置 analysis.io 数据访问函数。"""
            args = {"code": code}
            return traced_tool(ctx, "run_python", args, lambda: run_python_impl(ctx.deps, **args))

        @agent.tool
        def record_note(ctx: RunContext[AgentDeps], note: str) -> dict:
            """写入项目记忆。"""
            args = {"note": note}
            return traced_tool(ctx, "record_note", args, lambda: record_note_impl(ctx.deps, **args))

        return agent
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run `pip install -r requirements.txt`.") from exc
