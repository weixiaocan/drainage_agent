from __future__ import annotations

import uuid
from pathlib import Path
import re
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


REPORT_SCOPE_CONFIRMATION_PROMPT = (
    "我需要先确认报告范围：要包含哪些点位、哪段时间、哪些模块/章节？"
    "如果包含雨天风险，也请说明采用哪些降雨事件。"
)


def _is_report_request(message: str) -> bool:
    return any(keyword in message for keyword in ("报告", "DOCX", "docx", "文档"))


def _has_explicit_report_scope(message: str) -> bool:
    if re.search(r"(?<![A-Za-z0-9])W\d+(?![A-Za-z0-9])", message, flags=re.IGNORECASE):
        return True
    scope_keywords = ("全网", "全部", "所有", "19个点", "19 个点", "全时段", "全月", "上旬", "中旬", "下旬")
    if any(keyword in message for keyword in scope_keywords):
        return True
    section_keywords = ("旱天", "雨天", "降雨", "风险", "排污规律", "RDII", "监测概况", "数据质量", "全部章节")
    if any(keyword in message for keyword in section_keywords):
        return True
    if re.search(r"\d+\s*月|\d{4}-\d{1,2}-\d{1,2}|\d+\s*号|第\s*\d+\s*场", message):
        return True
    return False


def _history_has_mixed_report_scope(history: list[str]) -> bool:
    texts = [text for text in history if text.strip()]
    joined = "\n".join(texts)
    has_full_scope = any(keyword in joined for keyword in ("全网", "19个点", "19 个点", "全部点位", "所有点位"))
    has_partial_scope = bool(re.search(r"(?<![A-Za-z0-9])W\d+(?![A-Za-z0-9])", joined, flags=re.IGNORECASE))
    time_markers: set[str] = set()
    for text in texts:
        if re.search(r"\d+\s*月\s*\d+\s*日|\d+\s*号|\d{4}-\d{1,2}-\d{1,2}", text):
            time_markers.add("dated")
        for keyword in ("上旬", "中旬", "下旬", "全月"):
            if keyword in text:
                time_markers.add(keyword)
    has_multiple_time_scopes = len(time_markers) >= 2
    return (has_full_scope and has_partial_scope) or has_multiple_time_scopes


def needs_report_scope_confirmation(message: str, history: list[str]) -> bool:
    if not _is_report_request(message):
        return False
    if _has_explicit_report_scope(message):
        return False
    if not history:
        return True
    return _history_has_mixed_report_scope(history)


class _PreflightResult:
    def __init__(self, output: str, message_history: list[Any], new_messages: list[Any] | None = None):
        self.output = output
        self._message_history = message_history
        self._new_messages = new_messages or []

    def all_messages(self) -> list[Any]:
        return self._message_history

    def new_messages(self) -> list[Any]:
        return self._new_messages


class _FakeToolCallPart:
    part_kind = "tool-call"

    def __init__(self, tool_name: str, args: dict[str, Any]):
        self.tool_name = tool_name
        self.args = args


class _FakeToolMessage:
    def __init__(self, tool_name: str, args: dict[str, Any]):
        self.parts = [_FakeToolCallPart(tool_name, args)]


def _report_sections_from_message(message: str) -> list[str] | None:
    dry_only_markers = ("只要旱天", "跳过雨天", "不要雨天", "降雨分析都不要", "不含降雨")
    if any(marker in message for marker in dry_only_markers):
        return ["监测概况", "旱天排污规律统计分析", "旱天风险"]
    return None


def _report_args_from_message(message: str) -> dict[str, Any]:
    points = sorted(
        set(re.findall(r"(?<![A-Za-z0-9])W\d+(?![A-Za-z0-9])", message, flags=re.IGNORECASE)),
        key=lambda value: int(value[1:]),
    )
    if any(keyword in message for keyword in ("全网", "19个点", "19 个点", "全部点位", "所有点位")):
        points_arg: list[str] | None = None
    else:
        points_arg = points or None

    start = None
    end = None
    if "全月" in message:
        start = "2026-03-01"
        end = "2026-03-31"
    after_match = re.search(r"3\s*月\s*(\d{1,2})\s*(?:日|号)?\s*之后", message)
    if after_match:
        start = f"2026-03-{int(after_match.group(1)):02d}"
        end = None

    event_ids = [int(value) for value in re.findall(r"第\s*(\d+)\s*场", message)]
    return {
        "points": points_arg,
        "start": start,
        "end": end,
        "sections": _report_sections_from_message(message),
        "event_ids": event_ids or None,
    }


def _should_direct_generate_report(message: str, history: list[str]) -> bool:
    return _is_report_request(message) and _has_explicit_report_scope(message) and not needs_report_scope_confirmation(message, history)


def _report_tool_output(result: dict[str, Any]) -> str:
    status = result.get("status")
    summary = str(result.get("summary") or "")
    if status != "ok":
        return summary or str(result)
    artifacts = result.get("artifacts") or []
    destinations = result.get("data", {}).get("result_destinations", [])
    lines = ["报告已生成。"]
    if summary:
        lines.append(summary)
    if artifacts:
        lines.append("产物：" + "；".join(str(path) for path in artifacts))
    destination_paths = [str(item.get("path")) for item in destinations if isinstance(item, dict) and item.get("path")]
    if destination_paths:
        lines.append("综合表：" + "；".join(destination_paths))
    return "\n".join(lines)


class _ReportScopeGuardedAgent:
    def __init__(self, inner: Any):
        self._inner = inner

    def run_sync(self, message: str, *, deps: AgentDeps, message_history: list[Any] | None = None) -> Any:
        history = list(message_history or [])
        prior_user_prompts = list(deps.session.user_prompt_history)
        deps.session.current_user_prompt = message
        try:
            if needs_report_scope_confirmation(message, prior_user_prompts):
                return _PreflightResult(REPORT_SCOPE_CONFIRMATION_PROMPT, history)
            if _should_direct_generate_report(message, prior_user_prompts):
                args = _report_args_from_message(message)
                result = generate_report_impl(deps, **args)
                return _PreflightResult(
                    _report_tool_output(result),
                    history,
                    [_FakeToolMessage("generate_report", args)],
                )
            return self._inner.run_sync(message, deps=deps, message_history=history)
        finally:
            deps.session.user_prompt_history.append(message)
            deps.session.current_user_prompt = None


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
        from pydantic_ai.settings import ModelSettings

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

        agent = Agent(
            model,
            deps_type=AgentDeps,
            system_prompt=load_system_prompt(deps.paths.root, deps.project_notes),
            model_settings=ModelSettings(request_limit=100),
        )

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
        def check_data(
            ctx: RunContext[AgentDeps],
            points: list[str] | None = None,
            export: bool = False,
            start: str | None = None,
            end: str | None = None,
        ) -> dict:
            """检查数据收集率、缺失、异常概况与格式问题。"""
            args = {"points": points, "export": export, "start": start, "end": end}
            return traced_tool(ctx, "check_data", args, lambda: check_data_impl(ctx.deps, **args))

        @agent.tool
        def analyze_rainfall(
            ctx: RunContext[AgentDeps],
            time_range: list[str] | None = None,
            output: str = "all",
            rainfall_gap_hours: int = 12,
            export: bool = False,
        ) -> dict:
            """分析降雨日统计、降雨场次和降雨输出。output: all/daily/events/charts。"""
            args = {
                "time_range": time_range,
                "output": output,
                "rainfall_gap_hours": rainfall_gap_hours,
                "export": export,
            }
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
        def generate_report(
            ctx: RunContext[AgentDeps],
            points: list[str] | None = None,
            start: str | None = None,
            end: str | None = None,
            sections: list[str] | None = None,
            event_ids: list[int] | None = None,
        ) -> dict:
            """生成排水监测分析报告。"""
            args = {
                "points": points,
                "start": start,
                "end": end,
                "sections": sections,
                "event_ids": event_ids,
            }
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

        return _ReportScopeGuardedAgent(agent)
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run `pip install -r requirements.txt`.") from exc
