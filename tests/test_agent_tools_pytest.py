from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import get_args

import pandas as pd
import pytest

from analysis import io
from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories
from agent.tools.inspect_tools import list_results_impl
from agent.tools.manifest import record_result
from agent.tools.memory_tool import record_note_impl
from agent.tools.module_tools import (
    analyze_event_response_impl,
    analyze_patterns_impl,
    analyze_rainfall_impl,
    analyze_rdii_impl,
    assess_risk_impl,
    check_data_impl,
    data_filter_impl,
    generate_report_impl,
)
from agent.tools.python_tool import run_python_impl
from agent.types import ToolStatus


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
    rows = 60
    pd.DataFrame(
        {
            "数据时间": pd.date_range("2026-01-01", periods=rows, freq="min"),
            "设备编号": ["100"] * rows,
            "流量(L/s)(均值)": [1.0 + i / 100 for i in range(rows)],
            "液位(m)(均值)": [0.2 + i / 1000 for i in range(rows)],
            "流速(m/s)(均值)": [0.3 + i / 1000 for i in range(rows)],
        }
    ).to_csv(deps.paths.flow_dir / "100_W1.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="h"),
            "rain": [0, 1, 2, 0, 0, 0],
        }
    ).to_csv(deps.paths.rainfall_file, index=False)
    pd.DataFrame({"点位编号": ["W1"], "管径": [1.0]}).to_excel(deps.paths.site_info_file, index=False)


def write_filter_sample_data(deps: AgentDeps) -> None:
    deps.paths.flow_dir.mkdir(parents=True, exist_ok=True)
    rows = 4 * 1440
    pd.DataFrame(
        {
            "数据时间": pd.date_range("2026-01-01", periods=rows, freq="min"),
            "设备编号": ["100"] * rows,
            "流量(L/s)(均值)": [1.0] * rows,
            "液位(m)(均值)": [0.2] * rows,
            "流速(m/s)(均值)": [0.3] * rows,
        }
    ).to_csv(deps.paths.flow_dir / "100_W1.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=4, freq="D"),
            "rain": [0.0, 0.0, 3.0, 0.0],
        }
    ).to_csv(deps.paths.rainfall_file, index=False)
    pd.DataFrame({"点位编号": ["W1"], "管径": [1.0]}).to_excel(deps.paths.site_info_file, index=False)


def test_tool_status_values_are_v2_only() -> None:
    assert set(get_args(ToolStatus)) == {"ok", "needs_input", "error"}


def test_check_data_success(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)

    check = check_data_impl(deps)

    assert check["status"] == "ok"
    assert deps.paths.combined_xlsx.exists()


def test_data_filter_writes_pipeline_style_filter_result(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    deps = make_deps(tmp_path)
    write_filter_sample_data(deps)
    raw_flow = io.load_flow(root=deps.paths.root)

    result = data_filter_impl(deps)
    filtered_flow = io.load_filtered_flow(root=deps.paths.root)

    assert result["status"] == "ok"
    assert len(raw_flow) == 4 * 1440
    assert deps.paths.filter_result.exists()
    assert result["data"]["selected"] == {"W1": ["2026-01-02"]}
    assert len(filtered_flow) == 1440
    assert set(filtered_flow["timestamp"].dt.strftime("%Y-%m-%d")) == {"2026-01-02"}

    wb = load_workbook(deps.paths.filter_result)
    ws = wb["筛选结果"]
    headers = [cell.value for cell in ws[1]]
    assert headers[:5] == ["点位编号", "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert headers[-1] == "筛选说明"
    assert ws.cell(row=2, column=1).value == "当天雨量"
    assert ws.cell(row=3, column=1).value == "W1"
    assert str(ws.cell(row=3, column=3).fill.start_color.index).upper().endswith("92D050")


def test_rainfall_and_event_needs_input(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)

    rain = analyze_rainfall_impl(deps)
    response = analyze_event_response_impl(deps)

    assert rain["status"] == "ok"
    assert response["status"] == "needs_input"
    assert response["missing"] == "event_ids"
    assert response["options"]


def test_event_response_rdii_and_risk_with_event_ids(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    analyze_rainfall_impl(deps)

    response = analyze_event_response_impl(deps, event_ids=[1])
    rdii = analyze_rdii_impl(deps, event_ids=[1])
    risk = assess_risk_impl(deps, scope="all", event_ids=[1])

    assert response["status"] == "ok"
    assert rdii["status"] == "ok"
    assert risk["status"] == "ok"


def test_event_response_impl_marks_no_monitoring_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    analyze_rainfall_impl(deps)
    monkeypatch.setattr("agent.tools.module_tools.analyze_event_response", lambda *_args, **_kwargs: pd.DataFrame())

    response = analyze_event_response_impl(deps, event_ids=[4], points=["W1"])

    assert response["status"] == "ok"
    assert response["data"]["no_data"] is True
    assert response["data"]["event_ids"] == [4]
    assert "无时间重叠" in response["summary"]
    assert deps.session.unavailable_event_ids == [4]


def test_report_refuses_event_without_monitoring_coverage(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    deps.session.selected_event_ids = [4]
    deps.session.unavailable_event_ids = [4]

    report = generate_report_impl(deps, sections=["RDII", "雨天风险"], event_ids=[4])

    assert report["status"] == "error"
    assert "无时间重叠" in report["summary"]
    assert not (deps.paths.outputs / "分析报告.docx").exists()


def test_patterns_and_report_success(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)

    patterns = analyze_patterns_impl(deps)
    report = generate_report_impl(deps, sections=["数据体检", "排污规律", "风险评估"])

    assert patterns["status"] == "ok"
    assert report["status"] == "ok"
    assert (deps.paths.outputs / "分析报告.docx").exists()


def test_list_results_exposes_fresh_manifest_metadata(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    artifact = deps.paths.outputs / "sample.xlsx"
    artifact.write_text("sample", encoding="utf-8")
    record_result(deps, "data_filter", [artifact.relative_to(deps.paths.root).as_posix()], params={})

    result = list_results_impl(deps)
    item = result["data"]["manifest"]["data_filter"]

    assert result["status"] == "ok"
    assert item["fresh"] is True
    assert item["params"] == {}


def test_record_note_success(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    result = record_note_impl(deps, "sample note")
    assert result["status"] == "ok"
    assert "sample note" in deps.paths.notes.read_text(encoding="utf-8")


def test_run_python_success_with_analysis_io(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    result = run_python_impl(deps, "df = load_flow()\nprint(len(df))\n(WORKSPACE_DIR / 'out.txt').write_text(str(len(df)), encoding='utf-8')")
    assert result["status"] == "ok"
    assert (deps.paths.workspace / "out.txt").read_text(encoding="utf-8") == "60"


def test_run_python_returns_error_without_crashing(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    result = run_python_impl(deps, "raise RuntimeError('kernel boom')")
    assert result["status"] == "error"
    assert "kernel boom" in result["data"]["stderr"]


def test_run_python_forces_utf8_stdout(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)

    result = run_python_impl(deps, "print('RDII 单位：m³')")

    assert result["status"] == "ok"
    assert "RDII 单位：m³" in result["data"]["stdout"]


def test_run_python_timeout_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = make_deps(tmp_path)
    monkeypatch.setattr("agent.tools.python_tool.TIMEOUT_SECONDS", 1)

    started = time.monotonic()
    result = run_python_impl(deps, "import time\ntime.sleep(30)")
    elapsed = time.monotonic() - started

    assert result["status"] == "error"
    assert elapsed < 10
    assert result["data"]["script"]
