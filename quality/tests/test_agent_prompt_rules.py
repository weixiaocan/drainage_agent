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


def test_prompt_requires_fresh_result_reuse_and_stale_rerun() -> None:
    prompt = read_prompt()
    assert "`list_results`" in prompt
    assert "`fresh=true`" in prompt
    assert "直接复用" in prompt
    assert "禁止重复调用" in prompt


def test_prompt_documents_v2_workflow_order() -> None:
    prompt = read_prompt()
    expected_order = (
        "`data_filter → check_data → analyze_rainfall → "
        "analyze_event_response → analyze_rdii → analyze_patterns → assess_risk`"
    )
    assert expected_order in prompt
    assert "范围不明确时先问清楚再调用" in prompt
    assert "“完整”明确表示全网、全部数据覆盖时段、全部章节" in prompt
    assert "generate_report(points=null, start=null, end=null, sections=null, event_ids=null)" in prompt
    assert "不要再次询问范围" in prompt
    assert "只能输出面向用户的最终问题" in prompt
    assert "禁止展示参数推断、工具选择和内部规划过程" in prompt
    assert "禁止在调用前单独跑" in prompt
    assert "失败时告知原因并停止" in prompt
    assert "不要编造" in prompt


def test_prompt_documents_routing_rules() -> None:
    prompt = read_prompt()
    assert "数据质量" in prompt
    assert "`check_data`" in prompt
    assert "`data_filter`" in prompt
    assert "`analyze_rainfall`" in prompt
    assert "`run_python`" in prompt
    assert "默认 `export=false`" in prompt
    assert "输出/导出/保存/落盘/生成文件" in prompt


def test_prompt_does_not_delegate_data_coverage_guard_to_agent() -> None:
    prompt = read_prompt()

    assert "数据覆盖" in prompt
    assert all(
        tool in prompt
        for tool in (
            "`analyze_event_response`",
            "`analyze_rdii`",
            "`assess_risk`",
            "`analyze_patterns`",
        )
    )
    assert "点位无覆盖时明确告知" in prompt
    assert "不调分析工具，不猜测原因" in prompt
    assert "剔除无覆盖点位并说明理由" in prompt
    assert "年份必须从当前任务对应的数据时间范围推断" in prompt
    assert "流量监测数据年份为准" in prompt
    assert "降雨数据年份为准" in prompt
    assert "若跨多个年份而无法唯一确定，再向用户询问" in prompt
    assert "不要为了确定流量任务的年份而调用 `analyze_rainfall`" in prompt
    assert "降雨事件存在" in prompt
    assert "推荐替代事件前必须验证" in prompt


def test_prompt_avoids_redundant_scope_and_baseline_confirmations() -> None:
    prompt = read_prompt()

    assert "合法的 `W数字` 形式直接视为监测点位编号" in prompt
    assert "未指定时间范围表示使用该数据的完整可用覆盖时段" in prompt
    assert "缺少旱天筛选基线时直接调用 `data_filter`" in prompt
    assert "禁止询问用户是否需要执行筛选" in prompt


def test_prompt_defines_full_month_against_available_data() -> None:
    prompt = read_prompt()

    assert "用户只说“全月”但没有指定月份" in prompt
    assert "数据仅覆盖一个自然月" in prompt
    assert "直接按该数据月份的完整可用范围分析" in prompt


def test_prompt_rejects_direct_comparison_across_mismatched_scopes() -> None:
    prompt = read_prompt()

    assert "比较两个分析结果前必须核对" in prompt
    assert "不具备直接可比性" in prompt
    assert "禁止直接给出高低、优劣或排名结论" in prompt
    assert "请求用户选择一个统一口径后再比较" in prompt


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
    }


def test_prompt_requires_needs_input_for_event_ids() -> None:
    prompt = read_prompt()
    assert "`status=needs_input`" in prompt
    assert "`event_ids`" in prompt
    assert "`options`" in prompt


def test_prompt_does_not_treat_discovered_events_as_user_selection() -> None:
    prompt = read_prompt()
    assert "工具发现的可用场次不等于用户已选择场次" in prompt
    assert "列出所有可用场次" in prompt


def test_prompt_requires_report_scope_and_nonempty_rainy_risk() -> None:
    prompt = read_prompt()
    assert "`points`" in prompt
    assert "`start/end`" in prompt
    assert "`sections`" in prompt
    assert "`event_ids`" in prompt


def test_prompt_documents_exception_and_quality_reminders() -> None:
    prompt = read_prompt()
    assert "有效天数" in prompt
    assert "剔除比例" in prompt
    assert "缺失率" in prompt
    assert "工具返回 `error`" in prompt


def test_prompt_requires_valid_readable_markdown_tables() -> None:
    prompt = read_prompt()

    assert "每条记录单独一行" in prompt
    assert "禁止并排拼接两张表" in prompt


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


def test_prompt_requires_public_professional_terms() -> None:
    prompt = read_prompt()
    assert "最大充满度" in prompt
    assert "溢流风险值" in prompt
    assert "禁止使用“装满率”" in prompt
    assert "禁止向用户展示 `max_fullness`、`overflow_value`" in prompt
    assert "负流量的成因" in prompt
    assert "不得写成已确认原因" in prompt
    assert "largest_monitoring_covered_event_id" in prompt


def test_run_python_prompt_matches_sandbox_prelude() -> None:
    import re

    prompt = read_prompt()
    section = prompt[prompt.index("## run_python"):prompt.index("## 回复风格")]
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
