from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = PROJECT_ROOT / "agent" / "prompts" / "system.md"
CORE_PATH = PROJECT_ROOT / "agent" / "core" / "__init__.py"


def read_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


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
    assert "禁止自行补年份" in prompt
    assert "先不传 `time_range`" in prompt
    assert "降雨事件存在" in prompt
    assert "推荐替代事件前必须验证" in prompt


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


def test_run_python_prompt_documents_paths_schema_and_empty_data_guard() -> None:
    prompt = read_prompt()

    assert "`WORKSPACE_DIR`" in prompt
    assert "`timestamp`" in prompt
    assert "DataFrame 是否为空" in prompt
