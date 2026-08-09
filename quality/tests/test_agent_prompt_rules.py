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


def test_dry_report_intent_uses_existing_report_tool_without_model(
    monkeypatch,
) -> None:
    from agent.core import DRY_REPORT_SECTIONS, _ReportIntentAgent

    called = {}
    monkeypatch.setattr(
        "agent.core.generate_report_impl",
        lambda deps, **kwargs: called.update(kwargs) or {
            "status": "ok",
            "summary": "报告已生成",
        },
    )

    class UnexpectedModel:
        def run_sync(self, *args, **kwargs):
            raise AssertionError("明确的旱天报告不应等待模型再次路由")

    result = _ReportIntentAgent(UnexpectedModel()).run_sync(
        "生成旱天分析报告",
        deps=SimpleNamespace(),
        message_history=[],
    )

    assert called["sections"] == DRY_REPORT_SECTIONS
    assert "旱天分析报告已生成" in result.output
    assert len(result.all_messages()) == 2


def test_dry_report_with_rdii_requests_scope_confirmation(monkeypatch) -> None:
    from agent.core import _ReportIntentAgent

    monkeypatch.setattr(
        "agent.core.generate_report_impl",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("口径冲突时不应生成报告")
        ),
    )

    class UnexpectedModel:
        def run_sync(self, *args, **kwargs):
            raise AssertionError("明确可识别的口径冲突不应交给模型猜测")

    result = _ReportIntentAgent(UnexpectedModel()).run_sync(
        "生成 W1 旱天分析报告，并包含 RDII 分析。",
        deps=SimpleNamespace(),
        message_history=[],
    )

    assert "口径冲突" in result.output
    assert "旱天报告" in result.output
    assert "雨天/RDII" in result.output


def test_all_invalid_point_ids_are_rejected_before_model(monkeypatch) -> None:
    from agent.core import _InvalidPointAgent

    monkeypatch.setattr("agent.core._known_point_ids", lambda deps: {"W1", "W2"})

    class UnexpectedModel:
        def run_sync(self, *args, **kwargs):
            raise AssertionError("全无效点位不应交给模型猜测")

    result = _InvalidPointAgent(UnexpectedModel()).run_sync(
        "你直接告诉我 W999 的数据质量怎么样。",
        deps=SimpleNamespace(),
        message_history=[],
    )

    assert "W999" in result.output
    assert "不是有效点位" in result.output
    assert "W1、W2" in result.output


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


def test_run_python_prompt_documents_paths_schema_and_empty_data_guard() -> None:
    prompt = read_prompt()

    assert "`WORKSPACE_DIR`" in prompt
    assert "`timestamp`" in prompt
    assert "DataFrame 是否为空" in prompt
