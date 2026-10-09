"""Session analysis scope: recorded by a tool, applied to tool calls by code, announced every turn."""
from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.tools.scope_tool import apply_session_scope, describe_scope, set_analysis_scope_impl
from quality.tests.test_agent_tools_pytest import make_deps
from quality.tests.test_tool_failure_handling import _pydantic_agent


def test_scope_is_set_described_and_cleared(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)

    set_analysis_scope_impl(deps, weather="dry", points=["w1", "W6"], start="2026-01-01", end="2026-01-31")
    assert describe_scope(deps.session) == "口径：只看旱天；点位：W1、W6；时间窗：2026-01-01 至 2026-01-31"

    set_analysis_scope_impl(deps, clear=["weather", "time"])
    assert deps.session.weather_scope is None and deps.session.time_window == []
    assert deps.session.focus_points == ["W1", "W6"]


def test_missing_arguments_are_filled_and_explicit_ones_win(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    set_analysis_scope_impl(deps, points=["W1"], start="2026-01-01", end="2026-01-31")

    filled = {"points": None, "start": None, "end": None}
    notes, blocked = apply_session_scope(deps, "analyze_patterns", filled)
    assert blocked is None and notes
    assert filled == {"points": ["W1"], "start": "2026-01-01", "end": "2026-01-31"}

    explicit = {"points": ["W4"], "start": "2026-03-08", "end": None}
    notes, _ = apply_session_scope(deps, "analyze_patterns", explicit)
    assert explicit == {"points": ["W4"], "start": "2026-03-08", "end": None} and notes == []

    rainfall = {"time_range": None}
    apply_session_scope(deps, "analyze_rainfall", rainfall)
    assert rainfall == {"time_range": None}


def test_dry_scope_narrows_risk_and_guards_reports(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    set_analysis_scope_impl(deps, weather="dry")

    risk = {"scope": "all", "points": None, "start": None, "end": None, "event_ids": None}
    notes, blocked = apply_session_scope(deps, "assess_risk", risk)
    assert blocked is None and risk["scope"] == "dry" and any("旱天" in note for note in notes)

    explicit_rainy = {"scope": "rainy", "event_ids": [6]}
    apply_session_scope(deps, "assess_risk", explicit_rainy)
    assert explicit_rainy["scope"] == "rainy"

    _, blocked = apply_session_scope(deps, "generate_report", {"points": None, "sections": None})
    assert blocked["status"] == "needs_input"
    _, allowed = apply_session_scope(
        deps, "generate_report", {"points": None, "sections": ["监测概况", "旱天排污规律统计分析", "旱天风险"]}
    )
    assert allowed is None


def test_agent_applies_recorded_scope_to_a_later_tool_call(tmp_path: Path, monkeypatch) -> None:
    deps = make_deps(tmp_path)
    captured: list[dict] = []
    monkeypatch.setattr(
        "agent.core.assess_risk_impl",
        lambda _deps, **kwargs: captured.append(kwargs) or {"status": "ok", "summary": "风险评估完成。"},
    )
    wrapper, agent = _pydantic_agent(deps)
    seen: list = []
    calls = iter([
        ToolCallPart(tool_name="set_analysis_scope", args={"weather": "dry", "points": ["W1", "W6"]}),
        ToolCallPart(tool_name="assess_risk", args={"scope": "all"}),
    ])

    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.extend(part for message in messages for part in getattr(message, "parts", []))
        nxt = next(calls, None)
        return ModelResponse(parts=[nxt] if nxt else [TextPart(content="已完成风险评估。")])

    with agent.override(model=FunctionModel(respond)):
        wrapper.run_sync("接下来只看旱天、只关注 W1 和 W6，整体看风险", deps=deps, message_history=[])

    assert captured == [{"scope": "dry", "event_ids": None, "points": ["W1", "W6"], "start": None, "end": None, "export": False}]
    risk_return = [p for p in seen if isinstance(p, ToolReturnPart) and p.tool_name == "assess_risk"][0]
    assert "按会话范围" in risk_return.content["summary"]


def test_implicit_years_follow_the_data_before_tools_run(tmp_path: Path) -> None:
    from quality.tests.test_agent_tools_pytest import write_sample_data

    deps = make_deps(tmp_path)
    write_sample_data(deps)  # flow and rainfall data are in 2026
    deps.session.current_user_prompt = "生成 W1 在 1 月 1 日的报告"  # no year typed by the user

    args = {"points": ["W1"], "start": "2024-01-01", "end": "2024-01-01", "sections": None}
    notes, _ = apply_session_scope(deps, "check_data", args)
    assert args["start"].startswith("2026-01-01") and any("年份" in note for note in notes)

    rain_args = {"time_range": ["2024-01-01", "2024-01-02"]}
    apply_session_scope(deps, "analyze_rainfall", rain_args)
    assert all(value.startswith("2026") for value in rain_args["time_range"])

    set_analysis_scope_impl(deps, start="2024-01-01", end="2024-01-31")
    assert all(value.startswith("2026") for value in deps.session.time_window)

    deps.session.current_user_prompt = "看 2024 年 1 月"  # an explicit year is kept
    explicit = {"start": "2024-01-01", "end": "2024-01-31"}
    apply_session_scope(deps, "check_data", explicit)
    assert explicit == {"start": "2024-01-01", "end": "2024-01-31"}


def test_scope_recorded_in_the_same_step_reaches_sibling_tool_calls(tmp_path: Path, monkeypatch) -> None:
    # M008: the model emitted set_analysis_scope and analyze_patterns together; the analysis ran
    # before the window was stored, computed the full period, and the reply labelled it February.
    deps = make_deps(tmp_path)
    captured: list[dict] = []
    monkeypatch.setattr(
        "agent.core.analyze_patterns_impl",
        lambda _deps, **kwargs: captured.append(kwargs) or {"status": "ok", "summary": "排污规律分析完成。"},
    )
    wrapper, agent = _pydantic_agent(deps)
    seen: list = []
    first = [True]

    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.extend(part for message in messages for part in getattr(message, "parts", []))
        if first[0]:
            first[0] = False
            return ModelResponse(parts=[
                ToolCallPart(tool_name="set_analysis_scope", args={"start": "2026-02-01", "end": "2026-02-28"}),
                ToolCallPart(tool_name="analyze_patterns", args={}),
            ])
        return ModelResponse(parts=[TextPart(content="已完成。")])

    with agent.override(model=FunctionModel(respond)):
        wrapper.run_sync("只看 2 月这段的全网排污规律", deps=deps, message_history=[])

    assert captured and (captured[0]["start"], captured[0]["end"]) == ("2026-02-01", "2026-02-28")
    result = [p for p in seen if isinstance(p, ToolReturnPart) and p.tool_name == "analyze_patterns"][0]
    assert "本次实际时间范围：2026-02-01 至 2026-02-28" in result.content["summary"]
