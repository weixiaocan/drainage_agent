"""Session analysis scope: what the user restricted the conversation to, kept by code instead of model memory."""

from __future__ import annotations

from typing import Any

from agent.deps import AgentDeps, SessionState
from agent.tools.report_tool import REPORT_RAINFALL_SECTIONS, REPORT_RAINY_RISK_SECTIONS, _section_requested
from agent.types import ToolResult, needs_input, ok

SCOPE_FIELDS = ("weather", "points", "time")
POINT_TOOLS = {"check_data", "analyze_event_response", "analyze_patterns", "analyze_rdii", "assess_risk", "generate_report"}
TIME_TOOLS = {"check_data", "analyze_patterns", "assess_risk", "generate_report"}


def describe_scope(session: SessionState) -> str:
    weather = "只看旱天" if session.weather_scope == "dry" else "未限定"
    points = "、".join(session.focus_points) if session.focus_points else "未限定（全网）"
    if session.time_window:
        start, end = session.time_window
        time = f"{start or '数据起点'} 至 {end or '数据终点'}"
    else:
        time = "未限定（全部覆盖时段）"
    return f"口径：{weather}；点位：{points}；时间窗：{time}"


def set_analysis_scope_impl(
    deps: AgentDeps,
    weather: str | None = None,
    points: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    clear: list[str] | None = None,
) -> ToolResult:
    session = deps.session
    for item in clear or []:
        if item not in SCOPE_FIELDS:
            return needs_input("clear", f"clear 只能包含 {', '.join(SCOPE_FIELDS)}。")
        if item == "weather":
            session.weather_scope = None
        elif item == "points":
            session.focus_points = []
        else:
            session.time_window = []
    if weather is not None:
        if weather not in {"dry", "all"}:
            return needs_input("weather", "weather 只能是 dry（只看旱天）或 all（不限口径）。")
        session.weather_scope = "dry" if weather == "dry" else None
    if points:
        session.focus_points = [str(point).strip().upper() for point in points if str(point).strip()]
    if start is not None or end is not None:
        session.time_window = [start, end]
    return ok(f"当前会话分析范围：{describe_scope(session)}。", scope=describe_scope(session))


def apply_session_scope(deps: AgentDeps, tool_name: str, args: dict[str, Any]) -> tuple[list[str], ToolResult | None]:
    """Fill arguments the model left empty from the session scope; explicit arguments always win.

    Mutates ``args`` in place (tool lambdas read the same dict). Returns notes to append to the
    tool summary, or a result that replaces the call when it would break the dry-weather scope.
    """
    session = deps.session
    notes: list[str] = []
    if session.focus_points and tool_name in POINT_TOOLS and "points" in args and not args["points"]:
        args["points"] = list(session.focus_points)
        notes.append(f"点位按会话范围补全为 {'、'.join(session.focus_points)}")
    if (
        session.time_window and tool_name in TIME_TOOLS
        and args.get("start") is None and args.get("end") is None
    ):
        args["start"], args["end"] = session.time_window
        notes.append(f"时间窗按会话范围补全为 {args['start'] or '数据起点'} 至 {args['end'] or '数据终点'}")
    if session.weather_scope == "dry":
        if tool_name == "assess_risk" and args.get("scope") == "all":
            args["scope"] = "dry"
            notes.append("会话口径为只看旱天，已只评估旱天风险")
        if tool_name == "generate_report":
            sections = args.get("sections")
            if not sections or _section_requested(
                sections, REPORT_RAINFALL_SECTIONS | REPORT_RAINY_RISK_SECTIONS | {"事件响应", "响应", "RDII"}
            ):
                return notes, needs_input(
                    "sections",
                    "会话口径为只看旱天：请用户选择生成纯旱天报告，或先解除旱天口径再生成含雨天内容的报告。",
                    summary="当前会话口径为只看旱天，报告请求包含降雨或雨天内容，需要用户确认。",
                    options=[{"sections": ["监测概况", "旱天排污规律统计分析", "旱天风险"]}, {"clear": ["weather"]}],
                )
    if notes:
        notes.append("如用户要求其他范围，先调用 set_analysis_scope 修改")
    return notes, None
