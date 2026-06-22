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
        "analyze_event_response -> analyze_patterns -> assess_risk -> generate_report`"
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
