from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core import (
    _ReportScopeGuardedAgent,
    _report_args_from_message,
    _should_direct_generate_report,
    needs_pending_report_scope_completion,
    needs_report_scope_confirmation,
)
from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories


def test_generic_report_request_asks_when_history_has_mixed_scopes() -> None:
    history = [
        "看一下全网旱天风险。",
        "再加上第 6 场降雨的雨天风险。",
        "W4 和 W6 的排污规律如何，输出图表？",
    ]

    assert needs_report_scope_confirmation("根据上述分析撰写分析报告？", history)


def test_explicit_report_request_does_not_ask_again() -> None:
    history = [
        "看一下全网旱天风险。",
        "W4 和 W6 的排污规律如何，输出图表？",
    ]

    assert not needs_report_scope_confirmation(
        "旱天数据范围选择3月10号之后的数据，采用第6场降雨，报告包含19个点位，要全部章节的内容",
        history,
    )


def test_generic_report_request_asks_without_carryable_history() -> None:
    assert needs_report_scope_confirmation("生成分析报告。", [])


def test_non_report_request_is_not_intercepted() -> None:
    assert not needs_report_scope_confirmation("再看一下 W4 的排污规律。", [])


def test_explicit_report_request_is_direct_generate_candidate() -> None:
    history = ["W4 和 W6 的排污规律如何，输出图表？"]

    assert _should_direct_generate_report(
        "旱天数据范围选择3月10号之后的数据，采用第6场降雨，报告包含19个点位，要全部章节的内容",
        history,
    )


def test_partial_scope_reply_after_report_confirmation_keeps_confirmation() -> None:
    history = [
        "先检查一下数据质量。",
        "没大问题的话，给我全网旱天排污规律。",
        "单独比较W1和W4的排污特征并输出。",
        "生成分析报告。",
    ]

    assert needs_report_scope_confirmation(history[-1], history[:-1])
    assert needs_pending_report_scope_completion("跳过雨天部分，也我只要旱天相关的，降雨分析都不要。", history)
    assert not _should_direct_generate_report("跳过雨天部分，也我只要旱天相关的，降雨分析都不要。", history)


def test_complete_scope_reply_after_report_confirmation_directly_generates() -> None:
    history = [
        "先检查一下数据质量。",
        "没大问题的话，给我全网旱天排污规律。",
        "单独比较W1和W4的排污特征并输出。",
        "生成分析报告。",
        "跳过雨天部分，也我只要旱天相关的，降雨分析都不要。",
    ]

    assert _should_direct_generate_report("要所有关于旱天的分析，雨天的不要，包含所有点位，全时段。", history)


def test_report_args_parse_common_scope_markers() -> None:
    args = _report_args_from_message(
        "旱天数据范围选择3月10号之后的数据，采用第6场降雨，报告包含19个点位，要全部章节的内容"
    )

    assert args == {
        "points": None,
        "start": "2026-03-10",
        "end": None,
        "sections": None,
        "event_ids": [6],
    }


def test_report_args_parse_dry_only_sections() -> None:
    args = _report_args_from_message("跳过雨天部分，也我只要旱天相关的，降雨分析都不要。")

    assert args["sections"] == ["监测概况", "旱天排污规律统计分析", "旱天风险"]


def test_report_args_parse_all_dry_no_rain_sections() -> None:
    args = _report_args_from_message("要所有关于旱天的分析，雨天的不要，包含所有点位，全时段。")

    assert args["points"] is None
    assert args["sections"] == ["监测概况", "旱天排污规律统计分析", "旱天风险"]


def test_report_args_parse_point_attached_to_chinese_text() -> None:
    args = _report_args_from_message("生成W1的数据分析报告。")

    assert args["points"] == ["W1"]


def test_report_args_parse_full_month_without_month_name() -> None:
    args = _report_args_from_message("报告覆盖全月，降雨时间采用第6场降雨，报告雨天和旱天都要包括")

    assert args["start"] == "2026-03-01"
    assert args["end"] == "2026-03-31"
    assert args["event_ids"] == [6]


def test_report_scope_reply_lists_rainfall_events_before_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _make_deps(tmp_path)
    deps.current_project_id = "project-1"
    deps.current_batch_id = "workspace-1"
    deps.session.user_prompt_history = ["出一份完整的分析报告"]

    class RainfallRunner:
        def run(self, _request):
            return SimpleNamespace(
                data={
                    "events": [
                        {
                            "event_id": 1,
                            "start_time": "2026-03-07 10:00",
                            "end_time": "2026-03-07 16:00",
                            "total_mm": 8.5,
                        },
                        {
                            "event_id": 2,
                            "start_time": "2026-03-12 09:00",
                            "end_time": "2026-03-12 13:00",
                            "total_mm": 5.2,
                        },
                    ]
                }
            )

    deps.analysis_runner = RainfallRunner()
    agent = _ReportScopeGuardedAgent(_FailingInnerAgent())

    options = agent.run_sync(
        "所有，告诉我可以采用哪些降雨事件",
        deps=deps,
        message_history=[],
    )

    assert "已按全网、全时段、全部章节理解" in options.output
    assert "第 1 场" in options.output
    assert "第 2 场" in options.output
    assert "只要旱天" not in options.output
    captured: dict = {}

    def fake_generate_report(_deps: AgentDeps, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "报告生成完成",
            "artifacts": ["report.docx"],
            "data": {},
        }

    monkeypatch.setattr("agent.core.generate_report_impl", fake_generate_report)
    generated = agent.run_sync(
        "第 1 场",
        deps=deps,
        message_history=[],
    )

    assert generated.output.startswith("报告已生成。")
    assert captured["points"] is None
    assert captured["sections"] is None
    assert captured["event_ids"] == [1]


class _FailingInnerAgent:
    def run_sync(self, *_args, **_kwargs):
        raise AssertionError("confirmed report scope should not be delegated back to the inner agent")


def _make_deps(root: Path) -> AgentDeps:
    paths = Paths.from_root(root)
    ensure_directories(paths)
    return AgentDeps(
        paths=paths,
        settings=AgentSettings(model="test", base_url=None, api_key=None),
        logger=logging.getLogger("test.report_scope_guard"),
        session=SessionState(),
        project_notes="",
    )


def test_confirmed_filter_resume_generates_report_without_reasking_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _make_deps(tmp_path)
    filter_path = deps.paths.filter_result
    filter_path.write_bytes(b"confirmed-filter")
    deps.session.pending_filter_result_path = str(filter_path)
    deps.session.pending_filter_result_params = {}
    deps.session.pending_filter_result_request = "所有点位和时段，所有旱天分析模块，不用分析降雨"
    deps.session.user_prompt_history = [
        "出一份完整的分析报告",
        "所有点位和时段，所有旱天分析模块，不用分析降雨",
    ]
    captured: dict = {}

    def fake_generate_report(_deps: AgentDeps, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "报告生成完成",
            "artifacts": ["var/outputs/全网_全时段_分析报告.docx"],
            "data": {"result_destinations": []},
        }

    monkeypatch.setattr("agent.core.generate_report_impl", fake_generate_report)

    result = _ReportScopeGuardedAgent(_FailingInnerAgent()).run_sync("确认", deps=deps, message_history=[])

    assert result.output.startswith("报告已生成。")
    assert captured == {
        "points": None,
        "start": None,
        "end": None,
        "sections": ["监测概况", "旱天排污规律统计分析", "旱天风险"],
        "event_ids": None,
    }
    assert deps.session.pending_filter_result_request is None
