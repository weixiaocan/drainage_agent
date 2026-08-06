from __future__ import annotations

import uuid
from time import monotonic
from pathlib import Path
import re
from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent.deps import AgentDeps
from .logging_utils import summarize_tool_result, trace_event
from agent.tools.inspect_tools import list_results_impl
from agent.tools.module_tools import (
    analyze_event_response_impl,
    analyze_patterns_impl,
    analyze_rainfall_impl,
    analyze_rdii_impl,
    assess_risk_impl,
    check_data_impl,
    confirm_pending_filter_result,
    data_filter_impl,
    generate_report_impl,
)
from agent.tools.python_tool import run_python_impl
from agent.types import FilterConfirmationRequired


_cancel_flags: dict[str, bool] = {}

_INTERNAL_MONOLOGUE_PATTERNS = (
    r"让我(?:先|确认|思考|整理|向用户|调用)",
    r"这里我应该",
    r"我必须.*(?:用户|规则|工具)",
    r"(?:用户要求|用户需要|告知用户)",
    r"(?:规则强调|根据规则|实际上，?规则)",
    r"我认为应该",
)


def reject_internal_monologue(output: str) -> str:
    """Require model output to contain only the user-facing answer."""
    if any(re.search(pattern, output) for pattern in _INTERNAL_MONOLOGUE_PATTERNS):
        raise ModelRetry("只输出面向用户的最终回答；删除思考、规划、规则复述和自我对话。")
    return output


def request_cancel(session_id: str) -> None:
    _cancel_flags[session_id] = True


def _check_cancel(session_id: str) -> bool:
    return _cancel_flags.pop(session_id, False)


COMPACT_THRESHOLD = 30
COMPACT_KEEP_RECENT_TURNS = 6
COMPACT_SUMMARY_MARKER = "[CONVERSATION_COMPACT_SUMMARY]"


def _part_text(part: Any) -> str:
    content = getattr(part, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item) for item in content)
    args = getattr(part, "args", None)
    if args is not None:
        return f"{getattr(part, 'tool_name', 'tool')} args={args}"
    return ""


def _message_text(message: Any) -> str:
    return "\n".join(text for part in getattr(message, "parts", []) if (text := _part_text(part)).strip())


def _part_user_text(part: Any) -> str:
    if not isinstance(part, UserPromptPart):
        return ""
    content = getattr(part, "content", None)
    if isinstance(content, str):
        if COMPACT_SUMMARY_MARKER in content:
            return ""
        return content
    if isinstance(content, list):
        text = " ".join(str(item) for item in content)
        return "" if COMPACT_SUMMARY_MARKER in text else text
    return ""


def _message_user_text(message: Any) -> str:
    return "\n".join(text for part in getattr(message, "parts", []) if (text := _part_user_text(part)).strip())


def _empty_constraints() -> dict[str, list[str]]:
    return {
        "口径": [],
        "点位集合": [],
        "时间窗": [],
    }


def _summarize_texts(texts: list[str], *, max_items: int = 12, max_chars: int = 1800) -> str:
    lines: list[str] = []
    for text in texts:
        compact = " ".join(text.split())
        if not compact:
            continue
        lines.append(f"- {compact[:180]}")
        if len(lines) >= max_items:
            break
    summary = "\n".join(lines) or "- 无可提取的早期对话文本。"
    return summary[:max_chars]


def _extract_established_constraints(texts: list[str]) -> dict[str, list[str]]:
    constraints = _empty_constraints()

    scope_markers = ("只看", "只要", "全程", "不要", "排除", "限定", "限于", "仅看", "仅关注")
    point_markers = ("只关注", "只看", "限定", "限于", "仅看", "仅关注", "点位集合")
    time_markers = ("时间窗", "时间范围", "数据范围", "限定", "限于", "只看", "仅看", "范围选择")
    point_re = re.compile(r"(?<![A-Za-z0-9])W\d+(?![A-Za-z0-9])", flags=re.IGNORECASE)
    date_patterns = [
        re.compile(
            r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\s*(?:至|到|~|-|—)\s*20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
        ),
        re.compile(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:之后|以前|之前|以后)?"),
        re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)?(?:之后|以前|之前|以后)?"),
    ]

    for text in texts:
        clauses = [clause.strip() for clause in re.split(r"[。！？!?；;\n，,]", text) if clause.strip()]
        for clause in clauses:
            has_scope_marker = any(marker in clause for marker in scope_markers)
            if has_scope_marker and any(marker in clause for marker in ("旱天", "干天")):
                constraints["口径"] = ["只看旱天/干天，排除雨天或降雨相关内容"]
            elif has_scope_marker and any(marker in clause for marker in ("雨天", "降雨期间", "降雨事件")):
                constraints["口径"] = ["雨天/降雨事件相关分析"]

            points = sorted({match.upper() for match in point_re.findall(clause)}, key=lambda value: int(value[1:]))
            if points and any(marker in clause for marker in point_markers):
                constraints["点位集合"] = [", ".join(points)]
            elif any(marker in clause for marker in point_markers) and any(
                keyword in clause for keyword in ("全网", "全部点位", "所有点位", "19个点", "19 个点")
            ):
                constraints["点位集合"] = ["全网/全部点位"]

            if any(marker in clause for marker in time_markers):
                dates: list[str] = []
                for pattern in date_patterns:
                    dates.extend(pattern.findall(clause))
                normalized_dates = []
                for item in dates:
                    normalized = " ".join(str(item).split())
                    if normalized and normalized not in normalized_dates:
                        normalized_dates.append(normalized)
                if normalized_dates:
                    constraints["时间窗"] = normalized_dates

    return constraints


def _extract_constraints_from_prior_summaries(texts: list[str]) -> dict[str, list[str]]:
    constraints = _empty_constraints()
    for text in texts:
        if COMPACT_SUMMARY_MARKER not in text:
            continue
        in_constraints = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "## 已确立的约束/偏好":
                in_constraints = True
                continue
            if in_constraints and stripped.startswith("## "):
                break
            if not in_constraints or not stripped.startswith("- "):
                continue
            for key in ("口径", "点位集合", "时间窗"):
                prefix = f"- {key}:"
                if stripped.startswith(prefix):
                    value = stripped[len(prefix) :].strip()
                    if value and value != "未明确":
                        constraints[key] = [value]
    return constraints


def _merge_constraints(
    prior: dict[str, list[str]],
    current: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged = _empty_constraints()
    for key in ("口径", "点位集合", "时间窗"):
        merged[key] = current.get(key) or prior.get(key) or []
    return merged


def _format_constraints(constraints: dict[str, list[str]]) -> str:
    lines = []
    for key in ("口径", "点位集合", "时间窗"):
        values = constraints.get(key) or []
        lines.append(f"- {key}: {'; '.join(values) if values else '未明确'}")
    return "\n".join(lines)


def _build_compact_summary_message(older_messages: list[ModelMessage]) -> ModelRequest:
    older_texts = [_message_text(message) for message in older_messages]
    user_texts = [_message_user_text(message) for message in older_messages]
    prior_summaries = [text for text in older_texts if COMPACT_SUMMARY_MARKER in text]
    raw_texts = [text for text in older_texts if COMPACT_SUMMARY_MARKER not in text]
    constraints = _merge_constraints(
        _extract_constraints_from_prior_summaries(prior_summaries),
        _extract_established_constraints(user_texts),
    )
    content = (
        f"{COMPACT_SUMMARY_MARKER}\n"
        "以下是被压缩的早期对话摘要。后续回答必须继续遵守“已确立的约束/偏好”。\n\n"
        "## 已确立的约束/偏好\n"
        f"{_format_constraints(constraints)}\n\n"
        "## 早期对话摘要\n"
        f"{_summarize_texts([*prior_summaries, *raw_texts])}"
    )
    return ModelRequest(parts=[UserPromptPart(content=content)])


def compact_history(ctx: RunContext[AgentDeps], messages: list[ModelMessage]) -> list[ModelMessage]:
    if len(messages) <= COMPACT_THRESHOLD:
        return messages

    keep_count = max(COMPACT_KEEP_RECENT_TURNS * 2, 2)
    older_messages = messages[:-keep_count]
    recent_messages = messages[-keep_count:]
    summary_message = _build_compact_summary_message(older_messages)
    summary_text = _message_text(summary_message)
    compacted = [summary_message, *recent_messages]
    trace_event(
        ctx.deps.trace,
        {
            "event": "history_compaction",
            "before_count": len(messages),
            "after_count": len(compacted),
            "summary_text": summary_text,
        },
    )
    ctx.deps.logger.info("触发压缩,压缩前 %s 条→后 %s 条", len(messages), len(compacted))
    ctx.deps.logger.info("压缩摘要全文:\n%s", summary_text)
    return compacted


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


FILTER_CONFIRMATION_CLARIFICATION = "是确认用当前筛选结果继续吗？如果是，请回复“确认继续”；如果要重新筛选或改需求，请直接说明。"
DRY_REPORT_SECTIONS = ["监测概况", "旱天排污规律统计分析", "旱天风险"]
REPORT_SCOPE_CONFLICT_REPLY = (
    "检测到报告口径冲突：旱天报告应排除降雨相关内容，但 RDII 属于雨天分析。"
    "请确认要生成纯旱天报告，还是生成包含雨天/RDII 内容的综合报告。"
)


def _is_dry_report_request(message: str) -> bool:
    user_request = message.split("[本轮补充资料]", 1)[0]
    compact = re.sub(r"\s+", "", user_request)
    return (
        "报告" in compact
        and any(marker in compact for marker in ("旱天", "干天"))
        and not any(marker in compact for marker in ("雨天", "降雨", "RDII"))
        and not re.search(r"(?<![A-Za-z0-9])W\d+|20\d{2}[-/.年]", user_request, re.IGNORECASE)
    )


def _has_report_scope_conflict(message: str) -> bool:
    user_request = message.split("[本轮补充资料]", 1)[0]
    compact = re.sub(r"\s+", "", user_request)
    return (
        "报告" in compact
        and any(marker in compact for marker in ("旱天", "干天"))
        and any(marker in compact for marker in ("雨天", "降雨", "RDII"))
    )


def _direct_report_reply(result: dict[str, Any]) -> str:
    summary = str(result.get("summary") or "旱天分析报告处理完成。")
    if result.get("status") != "ok":
        return f"## 旱天分析报告生成失败\n\n{summary}"
    return (
        "## 旱天分析报告已生成\n\n"
        f"{summary}\n\n"
        "报告仅包含监测概况、旱天排污规律和旱天风险，不依赖降雨数据。"
    )


class _ReportIntentAgent:
    def __init__(self, inner: Any):
        self._inner = inner

    def run_sync(self, message: str, *, deps: AgentDeps, message_history: list[Any] | None = None) -> Any:
        history = list(message_history or [])
        if _has_report_scope_conflict(message):
            saved_history = [
                *history,
                ModelRequest(parts=[UserPromptPart(content=message)]),
                ModelResponse(parts=[TextPart(content=REPORT_SCOPE_CONFLICT_REPLY)]),
            ]
            return _PreflightResult(REPORT_SCOPE_CONFLICT_REPLY, saved_history)
        if not _is_dry_report_request(message):
            return self._inner.run_sync(message, deps=deps, message_history=history)
        result = generate_report_impl(deps, sections=DRY_REPORT_SECTIONS)
        reply = _direct_report_reply(result)
        saved_history = [
            *history,
            ModelRequest(parts=[UserPromptPart(content=message)]),
            ModelResponse(parts=[TextPart(content=reply)]),
        ]
        return _PreflightResult(reply, saved_history)


def _has_pending_filter_confirmation(deps: AgentDeps) -> bool:
    return bool(
        deps.session.pending_filter_id or deps.session.pending_filter_result_path
    )


def _is_clear_filter_confirmation(message: str) -> bool:
    text = message.strip().lower()
    compact = re.sub(r"\s+", "", text)
    clear_values = {
        "确认",
        "确认继续",
        "继续",
        "可以继续",
        "改好了",
        "已修改",
        "修改好了",
        "已确认",
        "用这个继续",
        "按这个继续",
        "ok",
        "okay",
    }
    return compact in clear_values


def _looks_like_ambiguous_filter_confirmation(message: str) -> bool:
    text = message.strip()
    if _is_clear_filter_confirmation(text):
        return False
    if not any(token in text for token in ("继续", "确认", "改好了", "已修改", "可以")):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])W\d+(?![A-Za-z0-9])|风险|分析|报告|RDII|响应|排污|导出|生成", text, re.IGNORECASE))


def _filter_confirmation_output(result: dict[str, Any]) -> str:
    summary = str(result.get("summary") or "")
    if summary:
        return summary
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    path = data.get("output_file") or (result.get("artifacts") or [""])[0]
    return f"筛选结果已生成于 {path}，请确认或修改后告知继续。"


def _resume_after_filter_confirmation_message(deps: AgentDeps, confirmed_path: Path) -> str:
    original = deps.session.pending_filter_result_request or "继续后续分析"
    return (
        f"用户已确认使用筛选结果文件 {confirmed_path}。"
        "继续执行上一轮未完成的请求；必须读取这份现成筛选结果，禁止重新调用 data_filter。"
        f"\n上一轮请求：{original}"
    )


class _FilterConfirmationAgent:
    def __init__(self, inner: Any):
        self._inner = inner

    def run_sync(self, message: str, *, deps: AgentDeps, message_history: list[Any] | None = None) -> Any:
        history = list(message_history or [])
        deps.session.current_user_prompt = message
        try:
            if _has_pending_filter_confirmation(deps):
                if _is_clear_filter_confirmation(message):
                    original_request = deps.session.pending_filter_result_request or ""
                    confirmed_path = confirm_pending_filter_result(deps)
                    continuation = _resume_after_filter_confirmation_message(deps, confirmed_path)
                    deps.session.current_user_prompt = continuation
                    result = self._inner.run_sync(continuation, deps=deps, message_history=history)
                    deps.session.pending_filter_result_request = None
                    return result
                if _looks_like_ambiguous_filter_confirmation(message):
                    return _PreflightResult(FILTER_CONFIRMATION_CLARIFICATION, history)
            return self._inner.run_sync(message, deps=deps, message_history=history)
        except FilterConfirmationRequired as exc:
            return _PreflightResult(
                _filter_confirmation_output(exc.result),
                history,
                [_FakeToolMessage(exc.tool_name, exc.args)],
            )
        finally:
            deps.session.user_prompt_history.append(message)
            deps.session.current_user_prompt = None


def load_system_prompt(root: Path) -> str:
    prompt_path = root / "agent" / "prompts" / "system.md"
    return prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""


def build_agent(deps: AgentDeps) -> Any:
    try:
        from pydantic_ai import Agent
        from pydantic_ai.capabilities import ProcessHistory
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
            system_prompt=load_system_prompt(deps.paths.root),
            model_settings=ModelSettings(request_limit=100, timeout=90),
            capabilities=[ProcessHistory(compact_history)],
        )

        @agent.output_validator
        def validate_user_facing_output(output: str) -> str:
            return reject_internal_monologue(output)

        def traced_tool(ctx: RunContext[AgentDeps], tool_name: str, args: dict[str, Any], func: Any) -> dict:
            call_id = uuid.uuid4().hex
            run_id = ctx.deps.session.current_run_id
            started = monotonic()
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
                if _check_cancel(ctx.deps.cancel_session_id):
                    return {"status": "cancelled", "summary": "工具已被用户取消"}
                result = func()
            except Exception as exc:
                duration_ms = round((monotonic() - started) * 1000)
                trace_event(
                    ctx.deps.trace,
                    {
                        "event": "tool_error",
                        "run_id": run_id,
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "error": repr(exc),
                        "duration_ms": duration_ms,
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
                    "duration_ms": round((monotonic() - started) * 1000),
                    **summarize_tool_result(result),
                },
            )
            if isinstance(result, dict) and result.get("status") == "needs_confirmation":
                raise FilterConfirmationRequired(result, tool_name, args)
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
            force_rerun: bool = False,
        ) -> dict:
            """检查数据收集率、缺失、异常概况与格式问题。export=true 时生成可下载的 CSV 结果表。"""
            args = {
                "points": points,
                "export": export,
                "start": start,
                "end": end,
                "force_rerun": force_rerun,
            }
            return traced_tool(ctx, "check_data", args, lambda: check_data_impl(ctx.deps, **args))

        @agent.tool
        def analyze_rainfall(
            ctx: RunContext[AgentDeps],
            time_range: list[str] | None = None,
            output: str = "all",
            rainfall_gap_hours: int = 12,
            export: bool = False,
        ) -> dict:
            """分析降雨日统计、降雨场次和降雨输出。output: all/daily/events/charts。export=true 时生成可下载的 CSV 结果表和 PNG 图表。"""
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
            """统计降雨事件期间各点位响应指标；event_ids 未给时返回 needs_input。export=true 时生成可下载的 CSV 结果表。"""
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
            """分析排污规律并生成旱天特征曲线底料。export=true 时生成可下载的 CSV 结果表和各点位旱天特征曲线 PNG 图。"""
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
            """计算指定降雨事件的 RDII 指标；event_ids 未给时返回 needs_input。export=true 时生成可下载的 CSV 结果表和 RDII 曲线 PNG 图。"""
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
            """评估运行风险。scope: all/dry/rainy。export=true 时生成可下载的 CSV 结果表。"""
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

        return _ReportIntentAgent(_FilterConfirmationAgent(agent))
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run `pip install -r requirements.txt`.") from exc
