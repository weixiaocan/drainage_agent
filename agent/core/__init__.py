from __future__ import annotations

import uuid
from time import monotonic
from pathlib import Path
import re
from dataclasses import replace
from typing import Any

import pandas as pd
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent.deps import AgentDeps
from agent.logging_utils import summarize_tool_result, trace_event
from agent.tools.inspect_tools import list_results_impl
from agent.tools.analysis_tools import (
    analyze_event_response_impl,
    analyze_patterns_impl,
    analyze_rainfall_impl,
    analyze_rdii_impl,
    assess_risk_impl,
    check_data_impl,
)
from agent.tools.filter_tool import confirm_pending_filter_result, data_filter_impl
from agent.tools.report_tool import generate_report_impl
from agent.tools.scope_tool import apply_session_scope, describe_scope, set_analysis_scope_impl, used_range_note
from agent.tools.tool_support import is_full_network
from agent.tools.python_tool import run_python_impl
from agent.types import FilterConfirmationRequired, PythonApprovalRequired, ToolResult, error, needs_input
from analysis import io


_cancel_flags: dict[str, bool] = {}

_INTERNAL_MONOLOGUE_PATTERNS = (
    r"让我(?:先|确认|思考|整理|向用户|调用)",
    r"这里我应该",
    r"我必须.*(?:用户|规则|工具)",
    r"(?:用户要求|用户需要|告知用户)",
    r"(?:规则强调|根据规则|实际上，?规则)",
    r"我认为应该",
    r"我注意到.*(?:问题|需要)",
    r"需要向您说明.*确认下一步",
    r"^用户(?:要|想|说|直接|可能|的意图|没有|尚未)",
    r"我(?:应该|应当)(?:追问|先|直接)",
    r"我此前给出的",
)


def reject_internal_monologue(output: str) -> str:
    """Require model output to contain only the user-facing answer."""
    if any(re.search(pattern, output) for pattern in _INTERNAL_MONOLOGUE_PATTERNS):
        raise ModelRetry("只输出面向用户的最终回答；删除思考、规划、规则复述和自我对话。")
    return output


_CODE_SPAN_PATTERN = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_ENGLISH_PROSE_PATTERN = re.compile(r"[A-Za-z]+(?:[ ,;:'’-]+[A-Za-z]+){3,}")
MIN_CHINESE_RATIO = 0.5


def reject_english_prose(output: str) -> str:
    """Require Chinese replies; code spans, tool names and short identifiers like W1 or RDII are fine.

    Thresholds come from past eval replies: normal answers have a Chinese share of at least 0.67,
    English ones at most 0.59, and no Chinese answer contained four consecutive English words.
    """
    prose = _CODE_SPAN_PATTERN.sub(" ", output)
    chinese = len(re.findall(r"[一-鿿]", prose))
    latin = len(re.findall(r"[A-Za-z]", prose))
    match = _ENGLISH_PROSE_PATTERN.search(prose)
    if match or (chinese + latin and chinese / (chinese + latin) < MIN_CHINESE_RATIO):
        sample = match.group(0)[:60] if match else prose.strip()[:60]
        raise ModelRetry(f"回复必须使用中文，检测到英文内容：“{sample}”。请用中文重写完整回复。")
    return output


_DECIMAL_PATTERN = re.compile(r"(?<![\w.])-?\d[\d,]*\.\d+")
UNGROUNDED_MIN_COUNT = 3
UNGROUNDED_MIN_RATIO = 0.5


def _decimals(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in _DECIMAL_PATTERN.findall(text)]


def _is_grounded(value: float, known: set[float]) -> bool:
    # Allow percent/ratio rescaling and rounding differences against values seen in context.
    for candidate in (value, value / 100, value * 100):
        for other in known:
            if abs(candidate - other) <= max(0.005, abs(other) * 0.005):
                return True
    return False


def _derived_from(value: float, sources: list[float]) -> bool:
    """Whether value is a ratio, product, sum or difference (or percentage) of two grounded reply values."""
    for i, a in enumerate(sources):
        for b in sources[i + 1:]:
            candidates = [a + b, abs(a - b), a * b]
            candidates += [a / b, b / a] if a and b else []
            candidates += [abs(a - b) / b, abs(a - b) / a] if a and b else []
            if _is_grounded(value, set(candidates)):
                return True
    return False


def ungrounded_decimals(output: str, context_text: str) -> list[float]:
    """Decimals in the reply that appear nowhere in the context and cannot be derived from grounded reply values."""
    known = set(_decimals(context_text))
    values = _decimals(output)
    grounded = [value for value in values if _is_grounded(value, known)]
    return [
        value for value in values
        if not _is_grounded(value, known) and not _derived_from(value, grounded)
    ]


def _last_user_prompt_index(messages: list[Any]) -> int:
    return max(
        (index for index, message in enumerate(messages)
         if any(isinstance(part, UserPromptPart) for part in getattr(message, "parts", []))),
        default=-1,
    )


def grounding_text(messages: list[Any]) -> str:
    """Text the reply may legitimately quote numbers from.

    Earlier turns count in full. In the current turn only tool-call arguments and tool results count:
    the reply under validation, earlier rejected attempts and retry prompts quoting the suspect
    numbers are all part of ctx.messages and would otherwise ground the reply in itself.
    """
    current_turn = _last_user_prompt_index(messages)
    chunks: list[str] = []
    for index, message in enumerate(messages):
        for part in getattr(message, "parts", []):
            kind = getattr(part, "part_kind", None)
            if index > current_turn and kind not in {"tool-call", "tool-return"}:
                continue
            content = getattr(part, "content", None)
            if content is not None:
                chunks.append(content if isinstance(content, str) else str(content))
            args = getattr(part, "args", None)
            if args is not None:
                chunks.append(str(args))
    return "\n".join(chunks)


def reject_ungrounded_numbers(output: str, context_text: str, *, used_tools: bool = True) -> str:
    """Retry when reported decimals were never produced by a tool or stated in context.

    When this turn called tools, a few derived values (differences, multiples) are tolerated and only
    a reply dominated by unseen numbers is rejected. When it answered purely from the conversation,
    three unseen decimals are enough: M012 mixed recalled-but-wrong values into an otherwise correct table.
    """
    values = _decimals(output)
    unseen = ungrounded_decimals(output, context_text)
    suspicious = len(unseen) >= UNGROUNDED_MIN_COUNT and (
        not used_tools or len(unseen) >= len(values) * UNGROUNDED_MIN_RATIO
    )
    if suspicious:
        sample = "、".join(f"{value:g}" for value in unseen[:5])
        raise ModelRetry(
            f"回复中的数值（如 {sample}）在当前上下文和工具结果中找不到。"
            "请先调用对应工具获取结果（参数一致的已有结果会直接复用），不要凭记忆给出数值；"
            "然后直接输出修正后的完整回复，不要提及之前的回复或本提示。"
        )
    return output


def current_turn_used_tools(messages: list[Any]) -> bool:
    """Whether any tool was called after the latest user prompt."""
    return any(
        getattr(part, "part_kind", None) == "tool-call"
        for message in messages[_last_user_prompt_index(messages) + 1:]
        for part in getattr(message, "parts", [])
    )


UNVERIFIED_REPLY_NOTE = "\n\n> 提示：本回复未通过系统自动核对，其中的数值或表述请以重新运行分析工具的结果为准。"


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


def _build_compact_summary_message(older_messages: list[ModelMessage]) -> ModelRequest:
    """Summarize compacted turns. Scope constraints live in SessionState and reach the model via the per-turn scope note."""
    texts = [_message_text(message) for message in older_messages]
    content = (
        f"{COMPACT_SUMMARY_MARKER}\n"
        "以下是被压缩的早期对话摘要，其中的数值不完整；需要引用数值时请重新调用工具（参数一致的结果会直接复用）。\n\n"
        f"{_summarize_texts(texts)}"
    )
    return ModelRequest(parts=[UserPromptPart(content=content)])


SCOPE_NOTE_PREFIX = "[当前会话分析范围] "


def attach_scope_note(ctx: RunContext[AgentDeps], messages: list[ModelMessage]) -> list[ModelMessage]:
    """Put the session scope next to the new user prompt instead of before the history.

    The note is appended once per turn and stays in history, so the request prefix never
    changes and the provider's prefix cache keeps hitting when the scope changes.
    """
    last = messages[-1] if messages else None
    if not isinstance(last, ModelRequest):
        return messages
    prompts = [part for part in last.parts if isinstance(part, UserPromptPart)]
    if not prompts or any(str(part.content).startswith(SCOPE_NOTE_PREFIX) for part in prompts):
        return messages
    note = UserPromptPart(content=SCOPE_NOTE_PREFIX + describe_scope(ctx.deps.session))
    return [*messages[:-1], replace(last, parts=[note, *last.parts])]


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


class _PythonApprovalAgent:
    """Convert the approval control-flow exception into a terminal turn result."""

    def __init__(self, inner: Any):
        self._inner = inner

    def run_sync(self, message: str, *, deps: AgentDeps,
                 message_history: list[Any] | None = None) -> Any:
        history = list(message_history or [])
        try:
            return self._inner.run_sync(message, deps=deps, message_history=history)
        except PythonApprovalRequired as exc:
            data = exc.result.get("data") or {}
            request_id = str(data.get("request_id") or "")
            code_hash = str(data.get("code_sha256") or "")
            reply = (
                "Python 执行已暂停，等待用户单次审批。\n\n"
                f"请求编号：`{request_id}`\n\n"
                f"代码哈希：`{code_hash}`\n\n"
                "审批完成前，本轮不会继续调用任何工具。"
            )
            saved_history = [
                *history,
                ModelRequest(parts=[UserPromptPart(content=message)]),
                ModelResponse(parts=[TextPart(content=reply)]),
            ]
            return _PreflightResult(reply, saved_history)


def _known_point_ids(deps: AgentDeps) -> set[str]:
    sites = io.load_sites(root=deps.paths.root)
    known = (
        {str(value).upper() for value in sites["point_id"].dropna()}
        if "point_id" in sites.columns
        else set()
    )
    if not known:
        known = {
            str(value).upper()
            for value in sites.to_numpy().ravel()
            if re.fullmatch(r"W\d+", str(value), re.IGNORECASE)
        }
    if known:
        return known
    flow = io.load_flow(root=deps.paths.root)
    if "point_id" in flow.columns:
        known.update(str(value).upper() for value in flow["point_id"].dropna())
    return known


def invalid_date_argument(args: dict[str, Any]) -> tuple[str, Any] | None:
    """First start/end/time_range value that cannot be parsed as a date."""
    candidates = [(key, args.get(key)) for key in ("start", "end")]
    candidates += [("time_range", value) for value in (args.get("time_range") or [])]
    for key, value in candidates:
        if value in (None, ""):
            continue
        try:
            pd.Timestamp(value)
        except (TypeError, ValueError):
            return key, value
    return None


def invalid_point_result(deps: AgentDeps, points: list[str] | None) -> ToolResult | None:
    """Reject tool calls whose `points` are all absent from the project data.

    Partially invalid lists pass through: analysis tools already exclude uncovered points and report them.
    """
    if not points:
        return None
    known = _known_point_ids(deps)
    if not known:
        return None
    invalid = [
        str(point).strip()
        for point in points
        if str(point).strip().upper() not in known and not is_full_network([str(point)], deps)
    ]
    if len(invalid) < len(points):
        return None
    valid = sorted(known, key=lambda value: int(value[1:]) if value[1:].isdigit() else value)
    return needs_input(
        "points",
        "请用户从有效点位中重新指定，不要自行替换为其他点位。",
        summary=f"{'、'.join(invalid)} 不是有效点位编号，当前数据中不存在。有效点位：{'、'.join(valid)}。",
        options=[{"point_id": value} for value in valid],
    )


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
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.settings import ModelSettings

        provider_kwargs = {}
        if deps.settings.base_url:
            provider_kwargs["base_url"] = deps.settings.base_url
        if deps.settings.api_key:
            provider_kwargs["api_key"] = deps.settings.api_key
        try:
            model = OpenAIChatModel(deps.settings.model, provider=OpenAIProvider(**provider_kwargs))
        except Exception as exc:
            raise RuntimeError(
                "OpenAI-compatible model initialization failed. Check AGENT_API_KEY/AGENT_BASE_URL/AGENT_MODEL in .env."
            ) from exc

        agent = Agent(
            model,
            deps_type=AgentDeps,
            # instructions, not system_prompt: a system prompt lives in the first history message
            # and is lost once compact_history drops that message.
            instructions=load_system_prompt(deps.paths.root),
            model_settings=ModelSettings(request_limit=100, timeout=90),
            retries={"output": 3, "tools": 2},
            capabilities=[ProcessHistory(compact_history), ProcessHistory(attach_scope_note)],
        )

        @agent.output_validator
        def validate_user_facing_output(ctx: RunContext[AgentDeps], output: str) -> str:
            try:
                output = reject_internal_monologue(output)
                output = reject_english_prose(output)
                return reject_ungrounded_numbers(
                    output, grounding_text(ctx.messages),
                    used_tools=current_turn_used_tools(ctx.messages),
                )
            except ModelRetry:
                # Out of retries: deliver the reply with a warning instead of failing the whole turn.
                if ctx.retry >= ctx.max_retries:
                    return output + UNVERIFIED_REPLY_NOTE
                raise

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
            invalid_date = invalid_date_argument(args)
            if invalid_date:
                raise ModelRetry(
                    f"参数 {invalid_date[0]}={invalid_date[1]!r} 不是可识别的日期。"
                    "请改用 YYYY-MM-DD 格式（年份按数据时间范围推断）后重新调用。"
                )
            try:
                if _check_cancel(ctx.deps.cancel_session_id):
                    return {"status": "cancelled", "summary": "工具已被用户取消"}
                scope_notes, scope_result = apply_session_scope(ctx.deps, tool_name, args)
                result = scope_result or invalid_point_result(ctx.deps, args.get("points")) or func()
                scope_notes += used_range_note(tool_name, args)
                if scope_notes and isinstance(result, dict):
                    result = {**result, "summary": f"{result.get('summary', '')}（{'；'.join(scope_notes)}）"}
            except (FilterConfirmationRequired, PythonApprovalRequired):
                raise
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
                # A crashing tool must not end the conversation; report it as a failed tool result.
                return error(f"{tool_name} 执行出错：{type(exc).__name__}: {exc}")
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
            if isinstance(result, dict) and result.get("status") == "needs_approval":
                raise PythonApprovalRequired(result, args)
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
            """按内置模板生成 DOCX 报告；所需的筛选、降雨、规律和风险结果由工具内部按章节补齐。
            sections 可选：监测概况、降雨分析、旱天排污规律统计分析、旱天风险、雨天风险、污水系统运行风险分析；
            null 表示全部章节。"旱天报告"取 ["监测概况", "旱天排污规律统计分析", "旱天风险"]。"""
            args = {
                "points": points,
                "start": start,
                "end": end,
                "sections": sections,
                "event_ids": event_ids,
            }
            return traced_tool(ctx, "generate_report", args, lambda: generate_report_impl(ctx.deps, **args))

        # sequential: a scope recorded in the same step must be in place before sibling tool calls read it.
        @agent.tool(sequential=True)
        def set_analysis_scope(
            ctx: RunContext[AgentDeps],
            weather: str | None = None,
            points: list[str] | None = None,
            start: str | None = None,
            end: str | None = None,
            clear: list[str] | None = None,
        ) -> dict:
            """记录或修改本会话的分析范围，之后未指定的工具参数按此补全。
            用户限定口径（weather="dry" 只看旱天，"all" 不限）、点位或时间窗时调用；
            用户改用全网、全时段或解除旱天口径时，用 clear 清除对应项（"weather"/"points"/"time"）。
            只按用户的明确要求修改或清除；范围内没有数据时不要清除或改动范围，告诉用户实际覆盖时段并请用户决定。"""
            args = {"weather": weather, "points": points, "start": start, "end": end, "clear": clear}
            return traced_tool(ctx, "set_analysis_scope", args, lambda: set_analysis_scope_impl(ctx.deps, **args))

        @agent.tool
        def list_results(ctx: RunContext[AgentDeps]) -> dict:
            """列出已有结果、manifest 与新鲜度。"""
            return traced_tool(ctx, "list_results", {}, lambda: list_results_impl(ctx.deps))

        @agent.tool
        def run_python(
            ctx: RunContext[AgentDeps], purpose: str, code: str,
            inputs: list[str], outputs: list[str], overwrite: bool = False,
        ) -> dict:
            """在隔离沙箱中执行经策略判定的长尾 Python 分析。"""
            args = {"purpose": purpose, "code": code, "inputs": inputs,
                    "outputs": outputs, "overwrite": overwrite}
            return traced_tool(ctx, "run_python", args, lambda: run_python_impl(ctx.deps, **args))

        return _PythonApprovalAgent(_FilterConfirmationAgent(agent))
    except ImportError as exc:
        raise RuntimeError("pydantic-ai is not installed. Run `pip install -r requirements.txt`.") from exc
