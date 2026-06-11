from __future__ import annotations

from pathlib import Path


PROMPT_PATH = Path(__file__).resolve().parents[1] / "agent" / "prompts" / "system.md"


def read_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_requires_fresh_result_reuse_and_stale_rerun() -> None:
    prompt = read_prompt()
    assert "`list_results`" in prompt
    assert "`fresh=true`" in prompt
    assert "直接复用" in prompt
    assert "禁止再次调用对应的生成工具" in prompt
    assert "重跑对应工具" in prompt
    assert "用户指定了新参数" in prompt


def test_prompt_documents_full_pipeline_order() -> None:
    prompt = read_prompt()
    expected_order = (
        "`run_data_filter -> run_dry_analysis -> run_rainfall_analysis -> "
        "run_event_stats -> run_pattern_analysis -> run_risk_analysis -> run_report_assembler`"
    )
    assert expected_order in prompt
    assert "不要编造编号" in prompt


def test_prompt_documents_routing_rules() -> None:
    prompt = read_prompt()
    assert "数据质量" in prompt
    assert "`run_data_stats`" in prompt
    assert "旱天数据情况" in prompt
    assert "完整数据筛选" in prompt
    assert "轻量统计" in prompt
    assert "单个点位" in prompt
    assert "`run_python`" in prompt


def test_prompt_requires_plot_confirmation_and_boundary_honesty() -> None:
    prompt = read_prompt()
    assert "绘图" in prompt
    assert "确认前只允许调用 `describe_data` 和 `list_results`" in prompt
    assert "只要图" in prompt
    assert "图加分析结论" in prompt
    assert "在用户回答前禁止调用 `run_python`" in prompt
    assert "拓扑" in prompt
    assert "管段关联" in prompt
    assert "当前不支持" in prompt
    assert "不要用 `run_python` 硬凑结论" in prompt


def test_prompt_documents_exception_and_quality_reminders() -> None:
    prompt = read_prompt()
    assert "有效天数少" in prompt
    assert "剔除比例高" in prompt
    assert "缺失率高" in prompt
    assert "格式错误" in prompt
    assert "工具返回 `error`" in prompt
    assert "不要在明显异常或格式错误时静默继续" in prompt


def test_prompt_documents_rainfall_range_semantics() -> None:
    prompt = read_prompt()
    assert "`rainfall_range`" in prompt
    assert "`events`" in prompt
    assert "范围参数用于缩小摘要和暴露给用户的产物" in prompt
    assert "底层 runner 可能仍会计算必要中间结果" in prompt
