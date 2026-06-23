from __future__ import annotations

import ast
from pathlib import Path


PROMPT_PATH = Path(__file__).resolve().parents[1] / "agent" / "prompts" / "system.md"
CORE_PATH = Path(__file__).resolve().parents[1] / "agent" / "core.py"


def read_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_requires_fresh_result_reuse_and_stale_rerun() -> None:
    prompt = read_prompt()
    assert "`list_results`" in prompt
    assert "`fresh=true`" in prompt
    assert "直接复用" in prompt
    assert "禁止重复调用" in prompt
    assert "重跑对应工具" in prompt


def test_prompt_documents_v2_workflow_order() -> None:
    prompt = read_prompt()
    expected_order = (
        "`data_filter -> check_data -> analyze_rainfall -> "
        "analyze_event_response -> analyze_rdii -> analyze_patterns -> assess_risk -> generate_report`"
    )
    assert expected_order in prompt
    assert "不要编造编号" in prompt


def test_prompt_documents_routing_rules() -> None:
    prompt = read_prompt()
    assert "数据质量" in prompt
    assert "`check_data`" in prompt
    assert "`data_filter`" in prompt
    assert "`analyze_rainfall`" in prompt
    assert "`run_python`" in prompt


def test_prompt_does_not_delegate_data_coverage_guard_to_agent() -> None:
    prompt = read_prompt()

    assert "数据覆盖前置检查" in prompt
    assert "必须先用 `check_data` 确认相关点位在该时段有数据覆盖" not in prompt
    assert all(
        tool in prompt
        for tool in (
            "`analyze_event_response`",
            "`analyze_rdii`",
            "`assess_risk`",
            "`analyze_patterns`",
        )
    )
    assert "该时段/该点位无数据，无法分析" in prompt
    assert "不要调用分析工具，也不要猜测或编造“可能的原因”" in prompt
    assert "明确剔除无数据覆盖的点位并说明理由" in prompt
    assert "用户只给出月日而未给年份时，禁止自行补年份" in prompt
    assert "先不传 `time_range` 调用 `analyze_rainfall`" in prompt


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
        "record_note",
    }


def test_prompt_requires_needs_input_for_event_ids() -> None:
    prompt = read_prompt()
    assert "`status=needs_input`" in prompt
    assert "`event_ids`" in prompt
    assert "`options`" in prompt


def test_prompt_documents_exception_and_quality_reminders() -> None:
    prompt = read_prompt()
    assert "有效天数少" in prompt
    assert "剔除比例高" in prompt
    assert "缺失率高" in prompt
    assert "格式错误" in prompt
    assert "工具返回 `error`" in prompt


def test_run_python_prompt_documents_paths_schema_and_empty_data_guard() -> None:
    prompt = read_prompt()

    assert "当前工作目录是 `WORKSPACE_DIR`" in prompt
    assert "`timestamp`" in prompt
    assert "不要尝试从 `analysis.io` 导入" in prompt
    assert "DataFrame 是否为空" in prompt
    assert "禁止调用 `run_python` 猜测或重复读取" in prompt
