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
            "rain": [1, 2, 0, 0, 0, 0],
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


def write_two_point_data(deps: AgentDeps) -> None:
    write_sample_data(deps)
    rows = 60
    pd.DataFrame(
        {
            "数据时间": pd.date_range("2026-01-01", periods=rows, freq="min"),
            "设备编号": ["200"] * rows,
            "流量(L/s)(均值)": [2.0 + i / 100 for i in range(rows)],
            "液位(m)(均值)": [0.4 + i / 1000 for i in range(rows)],
            "流速(m/s)(均值)": [0.5 + i / 1000 for i in range(rows)],
        }
    ).to_csv(deps.paths.flow_dir / "200_W2.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"点位编号": ["W1", "W2"], "管径": [1.0, 1.2]}).to_excel(
        deps.paths.site_info_file,
        index=False,
    )


def sample_two_point_pattern_flow() -> pd.DataFrame:
    frames = []
    for point_id, offset in (("W1", 0.0), ("W2", 1.0)):
        for day in ("2026-01-01", "2026-01-02"):
            timestamps = pd.date_range(day, periods=120, freq="min")
            frames.append(
                pd.DataFrame(
                    {
                        "timestamp": timestamps,
                        "device_id": point_id,
                        "point_id": point_id,
                        "flow_lps": [offset + 1.0 + i / 100 for i in range(120)],
                        "level_m": [offset / 10 + 0.2 + i / 1000 for i in range(120)],
                        "velocity_mps": [0.3] * 120,
                    }
                )
            )
    return pd.concat(frames, ignore_index=True)


def test_tool_status_values_are_v2_only() -> None:
    assert set(get_args(ToolStatus)) == {"ok", "needs_input", "error"}


def test_check_data_success(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)

    check = check_data_impl(deps)

    assert check["status"] == "ok"
    assert deps.paths.combined_xlsx.exists()


def test_full_network_analysis_writes_combined_sheet(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)

    result = check_data_impl(deps, points=["W1", "W2"])

    assert result["status"] == "ok"
    assert result["data"]["result_destinations"] == [
        {
            "kind": "combined_xlsx",
            "path": "outputs/综合分析结果.xlsx",
            "sheet": "数据收集率统计",
        }
    ]
    assert pd.read_excel(deps.paths.combined_xlsx, sheet_name="数据收集率统计").shape[0] == 2


def test_partial_analysis_without_export_does_not_persist_table(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)

    result = check_data_impl(deps, points=["W1"], export=False)

    assert result["status"] == "ok"
    assert result["data"]["result_destinations"] == [
        {"kind": "not_persisted", "path": None, "sheet": None}
    ]
    assert not deps.paths.combined_xlsx.exists()
    assert not list(deps.paths.outputs.glob("*.csv"))


def test_partial_analysis_with_export_writes_csv_not_combined(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)

    result = check_data_impl(deps, points=["W1"], export=True)

    destination = result["data"]["result_destinations"][0]
    assert result["status"] == "ok"
    assert destination == {
        "kind": "csv",
        "path": "outputs/W1_数据收集率统计.csv",
        "sheet": None,
    }
    assert (deps.paths.outputs / "W1_数据收集率统计.csv").exists()
    assert not deps.paths.combined_xlsx.exists()


def test_partial_analysis_does_not_replace_existing_full_network_sheet(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)
    check_data_impl(deps)
    before = pd.read_excel(deps.paths.combined_xlsx, sheet_name="数据收集率统计")

    result = check_data_impl(deps, points=["W1"], export=False)
    after = pd.read_excel(deps.paths.combined_xlsx, sheet_name="数据收集率统计")

    assert result["data"]["result_destinations"][0]["kind"] == "not_persisted"
    pd.testing.assert_frame_equal(after, before)


def test_full_network_patterns_write_combined_and_full_network_pngs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)
    flow = sample_two_point_pattern_flow()
    monkeypatch.setattr("agent.tools.module_tools._load_filtered_dry_flow", lambda *_args, **_kwargs: flow)

    result = analyze_patterns_impl(deps)

    assert result["status"] == "ok"
    assert result["data"]["result_destinations"][0]["kind"] == "combined_xlsx"
    assert "排污规律分析" in pd.ExcelFile(deps.paths.combined_xlsx).sheet_names
    assert (deps.paths.outputs / "特征曲线图" / "W1_流量特征曲线.png").exists()
    assert (deps.paths.outputs / "特征曲线图" / "W2_流量特征曲线.png").exists()


def test_partial_patterns_without_export_write_no_table_or_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)
    flow = sample_two_point_pattern_flow()
    monkeypatch.setattr("agent.tools.module_tools._load_filtered_dry_flow", lambda *_args, **_kwargs: flow[flow["point_id"] == "W1"])

    result = analyze_patterns_impl(deps, points=["W1"], export=False)

    assert result["status"] == "ok"
    assert result["data"]["result_destinations"][0]["kind"] == "not_persisted"
    assert not deps.paths.combined_xlsx.exists()
    assert not list(deps.paths.outputs.glob("*.csv"))
    assert not list(deps.paths.outputs.rglob("*.png"))


def test_partial_patterns_with_export_write_named_csv_and_png_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)
    flow = sample_two_point_pattern_flow()
    monkeypatch.setattr("agent.tools.module_tools._load_filtered_dry_flow", lambda *_args, **_kwargs: flow[flow["point_id"] == "W1"])

    result = analyze_patterns_impl(deps, points=["W1"], export=True)

    assert result["status"] == "ok"
    assert (deps.paths.outputs / "W1_排污规律分析.csv").exists()
    assert (deps.paths.outputs / "W1_排污规律曲线.png").exists()
    assert not deps.paths.combined_xlsx.exists()
    assert not (deps.paths.outputs / "特征曲线图" / "W1_流量特征曲线.png").exists()


def test_partial_patterns_do_not_overwrite_full_network_sheet_or_fixed_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = make_deps(tmp_path)
    write_two_point_data(deps)
    flow = sample_two_point_pattern_flow()
    monkeypatch.setattr("agent.tools.module_tools._load_filtered_dry_flow", lambda *_args, **_kwargs: flow)
    analyze_patterns_impl(deps)
    sheet_before = pd.read_excel(deps.paths.combined_xlsx, sheet_name="排污规律分析")
    fixed_png = deps.paths.outputs / "特征曲线图" / "W1_流量特征曲线.png"
    png_before = fixed_png.read_bytes()

    monkeypatch.setattr("agent.tools.module_tools._load_filtered_dry_flow", lambda *_args, **_kwargs: flow[flow["point_id"] == "W1"])
    analyze_patterns_impl(deps, points=["W1"], export=True)
    sheet_after = pd.read_excel(deps.paths.combined_xlsx, sheet_name="排污规律分析")

    pd.testing.assert_frame_equal(sheet_after, sheet_before)
    assert fixed_png.read_bytes() == png_before
    assert (deps.paths.outputs / "W1_排污规律曲线.png").exists()


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

    response = analyze_event_response_impl(deps, event_ids=[1], points=["W9"])

    assert response["status"] == "needs_input"
    assert response["missing"] == "data_coverage"
    assert "该时段/该点位无数据，无法分析" in response["summary"]
    assert deps.session.unavailable_event_ids == [1]


def test_event_response_guard_uses_real_timestamps_for_partial_coverage(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    pd.DataFrame(
        {
            "数据时间": pd.date_range("2026-02-01", periods=60, freq="min"),
            "设备编号": ["200"] * 60,
            "流量(L/s)(均值)": [2.0] * 60,
            "液位(m)(均值)": [0.4] * 60,
            "流速(m/s)(均值)": [0.5] * 60,
        }
    ).to_csv(deps.paths.flow_dir / "200_W2.csv", index=False, encoding="utf-8-sig")
    analyze_rainfall_impl(deps)

    result = analyze_event_response_impl(deps, event_ids=[1], points=["W1", "W2"])

    assert result["status"] == "ok"
    assert result["data"]["covered_points"] == ["W1"]
    assert result["data"]["excluded_points"] == [
        {"point_id": "W2", "reason": "该时段/该点位无数据，无法分析"}
    ]
    assert [row["point_id"] for row in result["data"]["table"]] == ["W1"]


@pytest.mark.parametrize("tool_name", ["event_response", "rdii", "risk"])
def test_event_tools_guard_before_analysis_when_no_point_has_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    analyze_rainfall_impl(deps)
    monkeypatch.setattr(
        "agent.tools.module_tools._event_data_coverage",
        lambda *_args, **_kwargs: (
            pd.DataFrame(),
            pd.DataFrame(),
            [],
            [{"point_id": "W1", "reason": "该时段/该点位无数据，无法分析"}],
        ),
    )

    if tool_name == "event_response":
        result = analyze_event_response_impl(deps, event_ids=[1], points=["W1"])
    elif tool_name == "rdii":
        result = analyze_rdii_impl(deps, event_ids=[1], points=["W1"])
    else:
        result = assess_risk_impl(deps, scope="rainy", event_ids=[1])

    assert result["status"] == "needs_input"
    assert result["missing"] == "data_coverage"
    assert "该时段/该点位无数据，无法分析" in result["summary"]


@pytest.mark.parametrize("tool_name", ["event_response", "rdii", "risk"])
def test_event_tools_exclude_uncovered_points_and_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    deps = make_deps(tmp_path)
    write_sample_data(deps)
    analyze_rainfall_impl(deps)
    covered_flow = io.load_flow(points=["W1"], root=deps.paths.root)
    events = pd.DataFrame(
        {
            "event_id": [1],
            "start_time": ["2026-01-01 00:00"],
            "end_time": ["2026-01-01 01:00"],
            "rain_level": ["小雨"],
        }
    )
    excluded = [{"point_id": "W2", "reason": "该时段/该点位无数据，无法分析"}]
    monkeypatch.setattr(
        "agent.tools.module_tools._event_data_coverage",
        lambda *_args, **_kwargs: (covered_flow, events, ["W1"], excluded),
    )

    if tool_name == "event_response":
        result = analyze_event_response_impl(deps, event_ids=[1], points=["W1", "W2"])
    elif tool_name == "rdii":
        monkeypatch.setattr("agent.tools.module_tools._load_filtered_dry_flow", lambda *_args, **_kwargs: covered_flow)
        monkeypatch.setattr("agent.tools.module_tools.build_dry_curves", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            "agent.tools.module_tools.analyze_rdii",
            lambda *_args, **_kwargs: {
                "rdii_total": pd.DataFrame([{"event_id": 1, "point_id": "W1", "rdii_total_m3": 1.0}]),
                "rdii_curve_data": {},
            },
        )
        result = analyze_rdii_impl(deps, event_ids=[1], points=["W1", "W2"])
    else:
        monkeypatch.setattr(
            "agent.tools.module_tools._dry_inputs",
            lambda *_args, **_kwargs: (pd.DataFrame(), pd.DataFrame(), {}),
        )
        result = assess_risk_impl(deps, scope="rainy", event_ids=[1])

    assert result["status"] == "ok"
    assert result["data"]["covered_points"] == ["W1"]
    assert result["data"]["excluded_points"] == excluded
    assert "剔除无覆盖点位 ['W2']" in result["summary"]


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
