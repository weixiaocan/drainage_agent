from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import pandas as pd

import pytest
from pydantic_ai import ModelRetry

from agent.core import reject_internal_monologue


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = PROJECT_ROOT / "agent" / "prompts" / "system.md"
CORE_PATH = PROJECT_ROOT / "agent" / "core" / "__init__.py"


def read_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_final_response_validator_rejects_internal_monologue() -> None:
    with pytest.raises(ModelRetry):
        reject_internal_monologue("现在我有完整数据了。让我整理一下结果，再告诉用户。")
    with pytest.raises(ModelRetry):
        reject_internal_monologue("我注意到一个关键问题，需要向您说明情况并确认下一步。")


def test_final_response_validator_accepts_user_facing_answer() -> None:
    answer = "W1 当前没有流量数据覆盖，请确认点位编号。"
    assert reject_internal_monologue(answer) == answer


def test_prompt_has_the_structured_sections_and_stays_short() -> None:
    prompt = read_prompt()

    for heading in ("## 判断优先级", "## 分析范围：默认值与何时追问", "## 工具使用", "## run_python", "## 回复"):
        assert heading in prompt
    # The previous prompt grew to 12.5k characters through per-failure patches; keep the rewrite compact.
    assert len(prompt) <= 6000


def test_prompt_mentions_every_registered_tool() -> None:
    tree = ast.parse(CORE_PATH.read_text(encoding="utf-8"))
    tools = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(d, ast.Attribute) and d.attr == "tool" for d in node.decorator_list)
    }
    prompt = read_prompt()

    assert [tool for tool in sorted(tools) if f"`{tool}`" not in prompt] == []


def test_prompt_keeps_key_contracts() -> None:
    prompt = read_prompt()

    for phrase in ("`needs_input`", "`needs_confirmation`", "`export=false`", "全局编号",
                   "largest_monitoring_covered_event_id", "最大充满度", "溢流风险值", "`clear`"):
        assert phrase in prompt


def test_dry_report_sections_match_between_prompt_and_tool_description() -> None:
    prompt = read_prompt()
    core = CORE_PATH.read_text(encoding="utf-8")
    sections = ["监测概况", "旱天排污规律统计分析", "旱天风险"]

    assert "“旱天报告”的章节为" + "、".join(sections) in prompt
    assert '"旱天报告"取 ' + str(sections).replace("'", '"') in core


def test_core_registers_exactly_the_documented_tools() -> None:
    tree = ast.parse(CORE_PATH.read_text(encoding="utf-8"))
    registered = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Attribute) and decorator.attr == "tool"
            for decorator in node.decorator_list
        )
    }
    assert registered == {
        "data_filter",
        "check_data",
        "analyze_rainfall",
        "analyze_event_response",
        "analyze_patterns",
        "analyze_rdii",
        "assess_risk",
        "generate_report",
        "list_results",
        "run_python",
        "set_analysis_scope",
    }


def test_tool_call_with_unknown_point_requests_valid_points(monkeypatch) -> None:
    from agent.core import invalid_point_result

    monkeypatch.setattr("agent.core._known_point_ids", lambda deps: {"W1", "W2"})
    monkeypatch.setattr("agent.core.is_full_network", lambda points, deps: points == ["全网"])

    result = invalid_point_result(SimpleNamespace(), ["W999"])

    assert result["status"] == "needs_input"
    assert result["missing"] == "points"
    assert "W999" in result["summary"]
    assert "W1、W2" in result["summary"]
    assert invalid_point_result(SimpleNamespace(), ["W1", "W999"]) is None
    assert invalid_point_result(SimpleNamespace(), ["w1", "W2"]) is None
    assert invalid_point_result(SimpleNamespace(), ["全网"]) is None
    assert invalid_point_result(SimpleNamespace(), None) is None


def test_known_point_ids_falls_back_to_flow_when_site_headers_are_unreadable(
    monkeypatch,
) -> None:
    from agent.core import _known_point_ids

    monkeypatch.setattr(
        "agent.core.io.load_sites", lambda **kwargs: pd.DataFrame({"garbled": ["unknown"]})
    )
    monkeypatch.setattr(
        "agent.core.io.load_flow",
        lambda **kwargs: pd.DataFrame({"point_id": ["W1", "W2"]}),
    )

    assert _known_point_ids(SimpleNamespace(paths=SimpleNamespace(root="."))) == {
        "W1",
        "W2",
    }


def test_run_python_prompt_matches_sandbox_prelude() -> None:
    import re

    prompt = read_prompt()
    section = prompt[prompt.index("## run_python"):prompt.index("## 回复")]
    prelude = (PROJECT_ROOT / "sandbox_runtime" / "prelude.py").read_text(encoding="utf-8")
    sandbox_functions = set(re.findall(r"^def ([a-z]\w*)\(", prelude, flags=re.M))
    named_functions = set(re.findall(r"`(\w+)\(", section))

    assert named_functions and named_functions <= sandbox_functions
    assert {"load_flow", "save_table"} <= named_functions
    assert "`confirmed_flow`" in section and "`timestamp`" in section
    assert "DataFrame 是否为空" in section


def test_reply_with_numbers_absent_from_context_is_retried() -> None:
    from pydantic_ai import ModelRetry

    from agent.core import reject_ungrounded_numbers

    # Real tool result for W18 was Kz 3.19 / peak-valley 52.06; the reply recalled different values.
    context = "{'point_id': 'W18', 'kz': 3.1912, 'peak_valley_ratio': 52.0643, 'peak_count': 7}"
    fabricated = "W18 第1类，Kz=1.36，峰谷比 2.11，峰值 623.29 L/s，谷值 295.66 L/s。"

    with pytest.raises(ModelRetry):
        reject_ungrounded_numbers(fabricated, context)


def test_reply_quoting_context_or_deriving_a_few_values_passes() -> None:
    from agent.core import reject_ungrounded_numbers

    context = "{'W1': {'rdii_m3': 2691.5912}, 'W6': {'rdii_m3': 2930.3701}, 'collection_rate': 0.9993}"
    quoted = "W1 RDII 2,691.59 m³，W6 2930.37 m³，收集率 99.93%。"
    derived = "W1 2691.59 m³，W6 2930.37 m³，W6 多约 238.78 m³（高出约 8.9%）。"

    assert reject_ungrounded_numbers(quoted, context) == quoted
    assert reject_ungrounded_numbers(derived, context) == derived


def test_grounding_text_counts_history_and_tool_io_but_not_current_turn_text() -> None:
    from pydantic_ai.messages import (
        ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
    )

    from agent.core import grounding_text

    messages = [
        ModelRequest(parts=[UserPromptPart(content="分析 W1")]),
        ModelResponse(parts=[TextPart(content="上一轮结论 Kz 3.38")]),
        ModelRequest(parts=[UserPromptPart(content="再看一下")]),
        ModelResponse(parts=[ToolCallPart(tool_name="analyze_patterns", args={"start": "2026-03-08"})]),
        ModelRequest(parts=[ToolReturnPart(tool_name="analyze_patterns", content={"kz": 4.62})]),
        ModelResponse(parts=[TextPart(content="被拒绝的回复 9.99")]),
        ModelRequest(parts=[RetryPromptPart(content="回复中的数值（如 9.99）找不到")]),
        ModelResponse(parts=[TextPart(content="待校验的回复 7.77")]),
    ]

    text = grounding_text(messages)

    assert "3.38" in text and "2026-03-08" in text and "4.62" in text
    assert "9.99" not in text and "7.77" not in text


@pytest.mark.parametrize(
    "reply",
    [
        "Before generating, I need to confirm the scope, since 现在的范围有两种口径。",
        "Event 6 is 2026-03-15 (小",
        "User wants only dry-weather sections. 好的，我来生成。",
    ],
)
def test_english_replies_are_retried(reply: str) -> None:
    from pydantic_ai import ModelRetry

    from agent.core import reject_english_prose

    with pytest.raises(ModelRetry):
        reject_english_prose(reply)


def test_chinese_replies_with_identifiers_and_code_pass() -> None:
    from agent.core import reject_english_prose

    reply = (
        "## 第 6 场降雨 RDII 风险排序（全部 19 个点位）\n\n"
        "W6 的 RDII 总量为 2930.37 m³，结果为 fresh，可复用；调用了 `analyze_rdii(event_ids=[6], points=None)`。\n"
        "```python\ndf = load_flow()\nprint(df.groupby('point_id')['flow_lps'].mean())\n```"
    )

    assert reject_english_prose(reply) == reply


def test_unparseable_dates_are_detected_before_tools_run() -> None:
    from agent.core import invalid_date_argument

    assert invalid_date_argument({"start": "3月8日", "end": "2026-03-12"}) == ("start", "3月8日")
    assert invalid_date_argument({"time_range": ["2026-03-10", "下旬"]}) == ("time_range", "下旬")
    assert invalid_date_argument({"start": "2026-03-08", "end": None, "time_range": ["2026-03-10", None]}) is None
