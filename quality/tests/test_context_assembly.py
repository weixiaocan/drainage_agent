"""What the model receives each request: rules survive compaction, and the scope does not break the cached prefix."""
from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.core import COMPACT_THRESHOLD, SCOPE_NOTE_PREFIX
from agent.tools.scope_tool import set_analysis_scope_impl
from quality.tests.test_tool_failure_handling import _pydantic_agent
from quality.tests.test_agent_tools_pytest import make_deps


def _recording_model(requests: list):
    def respond(messages, info: AgentInfo) -> ModelResponse:
        requests.append((list(messages), info.instructions))
        return ModelResponse(parts=[TextPart(content="好的。")])
    return FunctionModel(respond)


def test_static_rules_are_sent_after_history_compaction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("agent.core.load_system_prompt", lambda root: "STATIC RULES")
    deps = make_deps(tmp_path)
    wrapper, agent = _pydantic_agent(deps)
    history = []
    for i in range(COMPACT_THRESHOLD):
        history += [ModelRequest(parts=[UserPromptPart(content=f"问题 {i}")]),
                    ModelResponse(parts=[TextPart(content=f"回答 {i}")])]
    requests: list = []

    with agent.override(model=_recording_model(requests)):
        wrapper.run_sync("继续", deps=deps, message_history=history)

    messages, instructions = requests[-1]
    assert len(messages) < len(history)  # compaction ran
    assert "STATIC RULES" in instructions


def test_scope_change_keeps_earlier_requests_as_an_unchanged_prefix(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    wrapper, agent = _pydantic_agent(deps)
    requests: list = []

    with agent.override(model=_recording_model(requests)):
        first = wrapper.run_sync("看看全网", deps=deps, message_history=[])
        set_analysis_scope_impl(deps, weather="dry", points=["W1", "W6"])
        wrapper.run_sync("接着看", deps=deps, message_history=first.all_messages())

    (turn1, instructions1), (turn2, instructions2) = requests
    assert instructions1 == instructions2
    assert turn2[: len(turn1)] == turn1
    notes = [part.content for part in turn2[-1].parts
             if isinstance(part, UserPromptPart) and part.content.startswith(SCOPE_NOTE_PREFIX)]
    assert notes == [SCOPE_NOTE_PREFIX + "口径：只看旱天；点位：W1、W6；时间窗：未限定（全部覆盖时段）"]
