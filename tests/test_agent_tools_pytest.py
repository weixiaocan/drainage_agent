from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytest

from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories
from agent.tools import inspect_tools
from agent.tools import module_tools as mt
from agent.tools.manifest import record_result
from agent.tools.memory_tool import record_note_impl
from agent.tools.python_tool import run_python_impl


def make_deps(root: Path) -> AgentDeps:
    paths = Paths.from_root(root)
    ensure_directories(paths)
    return AgentDeps(
        paths=paths,
        settings=AgentSettings(model="test", base_url=None, api_key=None),
        logger=logging.getLogger("test.agent_tools"),
        session=SessionState(),
        project_notes="",
    )


def write_sample_data(deps: AgentDeps) -> None:
    deps.paths.flow_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="min"),
            "flow": [1.0, 1.2, 1.3, 1.4, 1.5, 1.7],
            "level": [0.2, 0.21, 0.22, 0.22, 0.23, 0.24],
        }
    ).to_csv(deps.paths.flow_dir / "100_W1.csv", index=False)
    deps.paths.rainfall_file.write_text("timestamp,rain\n2026-01-01 00:00:00,0\n", encoding="utf-8")
    deps.paths.site_info_file.write_bytes(b"sample site info")


def write_combined_workbook(deps: AgentDeps, sheet_names: list[str]) -> None:
    deps.paths.outputs.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(deps.paths.combined_xlsx) as writer:
        for sheet_name in sheet_names:
            pd.DataFrame({"point": ["W1"], "value": [1]}).to_excel(writer, sheet_name=sheet_name, index=False)


def touch_filter_result(deps: AgentDeps) -> None:
    deps.paths.filter_result.parent.mkdir(parents=True, exist_ok=True)
    deps.paths.filter_result.write_text("sample filter result", encoding="utf-8")


def touch_report_template(deps: AgentDeps) -> None:
    deps.paths.templates.mkdir(parents=True, exist_ok=True)
    (deps.paths.templates / "template.docx").write_bytes(b"sample template")


def ok_runner(payload: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    def _runner(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return payload

    return _runner


def raising_runner(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("kernel boom")


MODULE_TOOL_CASES = [
    (
        "run_data_stats",
        mt.run_data_stats_impl,
        "data_stats_run",
        {},
        {"stats_df": pd.DataFrame({"point": ["W1"]})},
        lambda deps: None,
    ),
    (
        "run_data_filter",
        mt.run_data_filter_impl,
        "data_filter_run",
        {},
        {"selected": {"W1": ["2026-01-01"]}},
        lambda deps: None,
    ),
    (
        "run_rainfall_analysis",
        mt.run_rainfall_analysis_impl,
        "rainfall_analysis_run",
        {},
        {
            "daily_rain": pd.DataFrame({"rain": [1.0]}),
            "event_rain": pd.DataFrame({"event": [1], "start": ["2026-01-01"]}),
        },
        lambda deps: None,
    ),
    (
        "run_dry_analysis",
        mt.run_dry_analysis_impl,
        "dry_analysis_run",
        {},
        {
            "dry_curve_data": {"W1": [1.0]},
            "statistics": pd.DataFrame({"point": ["W1"]}),
            "day_num": pd.DataFrame({"point": ["W1"]}),
        },
        touch_filter_result,
    ),
    (
        "run_event_stats",
        mt.run_event_stats_impl,
        "event_stats_run",
        {"event_ids": [1]},
        {"event_stats": pd.DataFrame({"event": [1]})},
        lambda deps: write_combined_workbook(deps, ["场次降雨统计"]),
    ),
    (
        "run_pattern_analysis",
        mt.run_pattern_analysis_impl,
        "pattern_analysis_run",
        {},
        {"pattern_df": pd.DataFrame({"point": ["W1"]}), "chart_count": {"flow_charts": 1, "level_charts": 1}},
        lambda deps: write_combined_workbook(deps, ["特征曲线_W1"]),
    ),
    (
        "run_rdii_analysis",
        mt.run_rdii_analysis_impl,
        "rdii_analysis_run",
        {"event_ids": [1]},
        {
            "max_level": pd.DataFrame({"point": ["W1"]}),
            "avg_flow": pd.DataFrame({"point": ["W1"]}),
            "rdii_total": pd.DataFrame({"point": ["W1"]}),
        },
        lambda deps: write_combined_workbook(deps, ["特征曲线_W1", "场次降雨统计"]),
    ),
    (
        "run_risk_analysis",
        mt.run_risk_analysis_impl,
        "risk_analysis_run",
        {},
        {"dry_risk": pd.DataFrame({"point": ["W1"]}), "rainy_risk": pd.DataFrame({"point": ["W1"]})},
        lambda deps: write_combined_workbook(deps, ["旱天分析"]),
    ),
    (
        "run_report_assembler",
        mt.run_report_assembler_impl,
        "report_assembler_run",
        {},
        {"stats": {"tables_filled": 1, "images_inserted": 1}, "output_file": "report.docx"},
        lambda deps: (
            write_combined_workbook(deps, ["旱天分析", "排污规律分析", "旱天风险"]),
            touch_report_template(deps),
        ),
    ),
]


@pytest.mark.parametrize("tool_name,tool_func,runner_attr,kwargs,payload,prepare", MODULE_TOOL_CASES)
def test_module_tools_run_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    tool_func: Callable[..., dict[str, Any]],
    runner_attr: str,
    kwargs: dict[str, Any],
    payload: dict[str, Any],
    prepare: Callable[[AgentDeps], None],
) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    prepare(deps)
    monkeypatch.setattr(mt, "_has_sheet", lambda _deps, _sheet: True)
    monkeypatch.setattr(mt, "_has_sheet_prefix", lambda _deps, _prefix: True)
    monkeypatch.setattr(mt, runner_attr, ok_runner(payload))
    result = tool_func(deps, **kwargs)
    assert result["status"] == "ok", tool_name
    assert tool_name in mt.load_manifest(deps)["results"]


@pytest.mark.parametrize(
    "tool_func,kwargs",
    [
        (mt.run_dry_analysis_impl, {}),
        (mt.run_event_stats_impl, {"event_ids": [1]}),
        (mt.run_pattern_analysis_impl, {}),
        (mt.run_rdii_analysis_impl, {"event_ids": [1]}),
        (mt.run_risk_analysis_impl, {}),
        (mt.run_report_assembler_impl, {}),
    ],
)
def test_module_tools_block_when_prerequisites_are_missing(
    tmp_path: Path,
    tool_func: Callable[..., dict[str, Any]],
    kwargs: dict[str, Any],
) -> None:
    deps = make_deps(tmp_path)
    result = tool_func(deps, **kwargs)
    assert result["status"] == "blocked"
    assert result["hint"]


@pytest.mark.parametrize("tool_name,tool_func,runner_attr,kwargs,payload,prepare", MODULE_TOOL_CASES)
def test_module_tools_return_error_when_runner_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    tool_func: Callable[..., dict[str, Any]],
    runner_attr: str,
    kwargs: dict[str, Any],
    payload: dict[str, Any],
    prepare: Callable[[AgentDeps], None],
) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    prepare(deps)
    monkeypatch.setattr(mt, "_has_sheet", lambda _deps, _sheet: True)
    monkeypatch.setattr(mt, "_has_sheet_prefix", lambda _deps, _prefix: True)
    monkeypatch.setattr(mt, runner_attr, raising_runner)
    result = tool_func(deps, **kwargs)
    assert result["status"] == "error", tool_name
    assert "kernel boom" in result["summary"]


def test_describe_data_success_with_sample_data(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    result = inspect_tools.describe_data_impl(deps)
    assert result["status"] == "ok"
    assert result["data"]["flow_file_count"] == 1


def test_describe_data_returns_error_when_sample_data_is_missing(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    result = inspect_tools.describe_data_impl(deps)
    assert result["status"] == "error"
    assert result["data"]["data"]["problems"]


def test_list_results_success_with_sample_workbook(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_combined_workbook(deps, ["sample"])
    result = inspect_tools.list_results_impl(deps)
    assert result["status"] == "ok"
    assert deps.paths.combined_xlsx.relative_to(deps.paths.root).as_posix() in result["data"]["results"]


def test_list_results_exposes_fresh_manifest_metadata(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    artifact = deps.paths.outputs / "sample.xlsx"
    artifact.write_text("sample", encoding="utf-8")
    record_result(
        deps,
        "run_data_filter",
        [artifact.relative_to(deps.paths.root).as_posix()],
        params={"missing_rate_threshold": 0.1},
    )

    result = inspect_tools.list_results_impl(deps)
    item = result["data"]["manifest"]["run_data_filter"]

    assert result["status"] == "ok"
    assert item["fresh"] is True
    assert item["params"] == {"missing_rate_threshold": 0.1}
    assert item["generated_at"]
    assert item["artifacts"] == ["outputs/sample.xlsx"]


def test_stale_prerequisite_blocks_downstream_tool(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    touch_filter_result(deps)
    record_result(deps, "run_data_filter", [deps.paths.filter_result.relative_to(deps.paths.root).as_posix()])
    with deps.paths.rainfall_file.open("a", encoding="utf-8") as f:
        f.write("2026-01-02 00:00:00,1\n")

    result = mt.run_dry_analysis_impl(deps)

    assert result["status"] == "blocked"
    assert "过期" in result["missing"]
    assert "run_data_filter" in result["hint"]


def test_new_filter_parameter_is_recorded_in_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    monkeypatch.setattr(mt, "data_filter_run", ok_runner({"selected": {"W1": ["2026-01-01"]}}))

    result = mt.run_data_filter_impl(deps, missing_rate_threshold=0.15)
    manifest = mt.load_manifest(deps)

    assert result["status"] == "ok"
    assert manifest["results"]["run_data_filter"]["params"] == {"missing_rate_threshold": 0.15}


def test_list_results_survives_bad_manifest(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    deps.paths.manifest.write_text("{not valid json", encoding="utf-8")
    result = inspect_tools.list_results_impl(deps)
    assert result["status"] == "ok"
    assert result["data"]["manifest"] == {}


def test_record_note_success(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    result = record_note_impl(deps, "sample note")
    assert result["status"] == "ok"
    assert "sample note" in deps.paths.notes.read_text(encoding="utf-8")


def test_record_note_empty_note_is_noop(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    before = deps.paths.notes.read_text(encoding="utf-8")
    result = record_note_impl(deps, "   ")
    assert result["status"] == "ok"
    assert deps.paths.notes.read_text(encoding="utf-8") == before


def test_run_python_success_with_workspace_artifact(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    result = run_python_impl(deps, "print(DATA_DIR.name)\n(WORKSPACE_DIR / 'out.txt').write_text('ok', encoding='utf-8')")
    assert result["status"] == "ok"
    assert (deps.paths.workspace / "out.txt").read_text(encoding="utf-8") == "ok"


def test_run_python_returns_error_without_crashing(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    result = run_python_impl(deps, "raise RuntimeError('kernel boom')")
    assert result["status"] == "error"
    assert "kernel boom" in result["data"]["stderr"]


def test_run_python_timeout_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = make_deps(tmp_path)
    monkeypatch.setattr("agent.tools.python_tool.TIMEOUT_SECONDS", 1)

    started = time.monotonic()
    result = run_python_impl(deps, "import time\ntime.sleep(30)")
    elapsed = time.monotonic() - started

    assert result["status"] == "error"
    assert elapsed < 10
    assert result["data"]["script"]
