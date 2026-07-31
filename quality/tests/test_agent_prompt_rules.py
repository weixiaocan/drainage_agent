from __future__ import annotations

import ast
from pathlib import Path


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
    assert "重跑对应工具" in prompt


def test_prompt_documents_v2_workflow_order() -> None:
    prompt = read_prompt()
    expected_order = (
        "`data_filter -> check_data -> analyze_rainfall -> "
        "analyze_event_response -> analyze_rdii -> analyze_patterns -> assess_risk`"
    )
    assert expected_order in prompt
    assert "报告范围明确时，本轮只调用 `generate_report`" in prompt
    assert "禁止在出报告前单独调用 `data_filter`、`check_data`、`analyze_patterns`" in prompt
    assert "查询实际时间范围" in prompt
    assert "报告范围明确的判定必须结合本轮请求和已有对话上下文" in prompt
    assert "直接调用 `generate_report`，不要反问" in prompt
    assert "报告范围不明确时才先询问，不要无脑一律先问" in prompt
    assert "先询问报告要包含哪些点位、哪段时间、哪些模块/章节" in prompt
    assert "上文混有多个点位/时间/模块范围但本轮没有选择" in prompt
    assert "绝对禁止调用 `generate_report`" in prompt
    assert "报告包含19个点位、3月10号之后、第6场降雨、全部章节" in prompt
    assert "明确报告请求不得预调任何分析工具" in prompt
    assert "生成W1的数据分析报告" in prompt
    assert "报告覆盖全月，降雨采用第6场，雨天和旱天都包括" in prompt
    assert "`generate_report` 返回 `error` 时" in prompt
    assert "立即把失败原因告诉用户并停止本轮" in prompt
    assert "禁止继续调用 `run_python`、`analyze_patterns`、`list_results`" in prompt
    assert "不要编造编号" in prompt


def test_prompt_documents_routing_rules() -> None:
    prompt = read_prompt()
    assert "数据质量" in prompt
    assert "`check_data`" in prompt
    assert "`data_filter`" in prompt
    assert "`analyze_rainfall`" in prompt
    assert "`run_python`" in prompt
    assert "所有非报告分析工具默认 `export=false`" in prompt
    assert "`check_data`、`analyze_patterns`、`assess_risk`、`analyze_event_response`、`analyze_rdii`" in prompt
    assert "只有用户明确要求“输出”“存下来”“导出”“保存成文件”“落盘”“生成 CSV/Excel/图片文件”“输出为文件”“输出图表”“把结果导出/保存”时，才设置 `export=true`" in prompt
    assert "用户说“看一下”“分析一下”“比较一下”“给我结论”“给我结果”“给出覆盖率/缺失情况”只表示在对话中展示结论或摘要" in prompt
    assert "不等于落盘，不得设置 `export=true`" in prompt
    assert "单独分析不写综合表" in prompt
    assert "只有 `generate_report` 成功生成报告时" in prompt
    assert "与报告同 scope 命名的综合表" in prompt
    assert "综合表与报告内容一一对应" in prompt


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
    assert "降雨事件存在不等于有流量监测数据覆盖" in prompt
    assert "未经验证不得宣称“有覆盖”" in prompt
    assert "只能说明“可进一步检查”" in prompt


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
    assert "点位范围传给 `points`" in prompt
    assert "时间范围传给 `start/end`" in prompt
    assert "不得省略后退回全网或全时段" in prompt
    assert "默认生成全套标准章节，包含雨天风险" in prompt
    assert "自动使用报告时间范围内识别到的全部降雨场次" in prompt


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
