"""End-to-end agent runs with a scripted model: bad tool input or a crashing tool must not end the turn."""
from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.core import build_agent
from agent.deps import AgentSettings
from quality.tests.test_agent_tools_pytest import make_deps


def _pydantic_agent(deps):
    deps.settings = AgentSettings(model="test", base_url="https://api.example.test/v1", api_key="test-key-not-used")
    wrapper = build_agent(deps)
    return wrapper, wrapper._inner._inner


def _scripted(first_call: ToolCallPart, seen: list):
    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.extend(part for message in messages for part in getattr(message, "parts", []))
        if not any(isinstance(part, (ToolReturnPart, RetryPromptPart)) for part in seen):
            return ModelResponse(parts=[first_call])
        return ModelResponse(parts=[TextPart(content="排污规律分析未能完成，请稍后重试。")])
    return FunctionModel(respond)


def test_crashing_tool_returns_error_instead_of_ending_the_turn(tmp_path: Path, monkeypatch) -> None:
    deps = make_deps(tmp_path)
    monkeypatch.setattr("agent.core.analyze_patterns_impl", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    wrapper, agent = _pydantic_agent(deps)
    seen: list = []
    call = ToolCallPart(tool_name="analyze_patterns", args={"points": None})

    with agent.override(model=_scripted(call, seen)):
        result = wrapper.run_sync("分析全网排污规律", deps=deps, message_history=[])

    returns = [part for part in seen if isinstance(part, ToolReturnPart)]
    assert result.output == "排污规律分析未能完成，请稍后重试。"
    assert returns and returns[0].content["status"] == "error"
    assert "RuntimeError: boom" in returns[0].content["summary"]


def test_unparseable_date_asks_the_model_to_retry(tmp_path: Path, monkeypatch) -> None:
    deps = make_deps(tmp_path)
    called = []
    monkeypatch.setattr("agent.core.analyze_patterns_impl", lambda *a, **k: called.append(k) or {"status": "ok"})
    wrapper, agent = _pydantic_agent(deps)
    seen: list = []
    call = ToolCallPart(tool_name="analyze_patterns", args={"points": ["W1"], "start": "3月8日"})

    with agent.override(model=_scripted(call, seen)):
        wrapper.run_sync("分析 W1 3 月 8 日以后的排污规律", deps=deps, message_history=[])

    retries = [part for part in seen if isinstance(part, RetryPromptPart)]
    assert called == []
    assert retries and "YYYY-MM-DD" in str(retries[0].content)


def test_memory_only_reply_with_wrong_values_is_retried_then_flagged(tmp_path: Path) -> None:
    # Mirrors M012 turn 10: no tool call this turn, correct and recalled-but-wrong values mixed in one table.
    deps = make_deps(tmp_path)
    wrapper, agent = _pydantic_agent(deps)
    history_reply = "W1 日均流量 3342.65 m³/d，流速 0.0328 m/s，最大液位 2.92 m，管径 1.5 m，最大充满度 1.95。"
    fabricated = (
        "| 点位 | 日均流量 | 流速 | 最大液位 | 管径 | 最大充满度 |\n|---|---|---|---|---|---|\n"
        "| W1 | 3634.96 | 0.35 | 2.36 | 1.55 | 1.95 |"
    )
    attempts: list[str] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        attempts.append("call")
        return ModelResponse(parts=[TextPart(content=fabricated)])

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    history = [
        ModelRequest(parts=[UserPromptPart(content="W1 的旱天风险？")]),
        ModelResponse(parts=[TextPart(content=history_reply)]),
    ]
    with agent.override(model=FunctionModel(respond)):
        result = wrapper.run_sync("再整体看风险。", deps=deps, message_history=history)

    assert len(attempts) == 4  # first answer + 3 retries
    assert result.output.startswith(fabricated)
    assert "未通过系统自动核对" in result.output


def test_reply_quoting_history_without_tools_passes_unchanged(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    wrapper, agent = _pydantic_agent(deps)
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    history = [
        ModelRequest(parts=[UserPromptPart(content="W1 的旱天风险？")]),
        ModelResponse(parts=[TextPart(content="W1 日均流量 3342.65 m³/d，最大充满度 1.95，溢流风险值 0.54。")]),
    ]
    quoted = "W1 最大充满度 1.95，溢流风险值 0.54，日均流量 3342.65 m³/d。"
    with agent.override(model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(content=quoted)]))):
        result = wrapper.run_sync("W1 风险再说一下", deps=deps, message_history=history)

    assert result.output == quoted
