from __future__ import annotations

from pathlib import Path

import pytest

from analysis.baselines import FilterBaselineService, FilterRequest
from analysis.jobs import BackgroundJobService
from analysis.runs import AnalysisInputRequired, AnalysisRequest, AnalysisRunner
from agent.tools.core_analysis import submit_core_analysis
from quality.tests.test_filter_baselines import write_standard_flow
from web.projects import ProjectRepository


def _confirmed_batch(tmp_path: Path):
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("核心分析项目")
    batch = projects.create_batch(project.id, "核心分析批次")
    workspace = projects.batch_workspace(project.id, batch.id)
    write_standard_flow(workspace)
    baselines = FilterBaselineService(database, files_root)
    filtered = baselines.run_filter(
        FilterRequest(project.id, batch.id, expected_rows_per_day=1)
    )
    baselines.confirm(project.id, batch.id, filtered.filter_id)
    return database, files_root, project, batch, workspace


def _write_rainfall(workspace: Path) -> None:
    (workspace / "standard" / "rainfall.csv").write_text(
        "\n".join(
            [
                "timestamp,rain_mm",
                "2026-03-02T00:00:00,3.0",
                "2026-03-02T01:00:00,2.0",
                "2026-03-04T00:00:00,12.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_sites(workspace: Path) -> None:
    (workspace / "standard" / "sites.csv").write_text(
        "point_id,diameter_m,well_depth_m,pipe_type\n"
        "W1,1.0,2.0,污水管\n",
        encoding="utf-8",
    )


def test_runner_executes_pattern_analysis_against_confirmed_baseline(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    runner = AnalysisRunner(database, files_root)

    result = runner.run(
        AnalysisRequest(
            project.id,
            batch.id,
            "patterns",
            points=["W1"],
        )
    )

    assert result.status == "succeeded"
    assert result.identity["baseline"]["kind"] == "confirmed_filter"
    assert result.data["table"][0]["point_id"] == "W1"
    assert result.version == 1
    assert (
        workspace / f"results/patterns/{result.run_id}/result.json"
    ).is_file()


def test_runner_executes_rainfall_from_current_batch_standard_data(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    _write_rainfall(workspace)

    result = AnalysisRunner(database, files_root).run(
        AnalysisRequest(project.id, batch.id, "rainfall")
    )

    assert result.data["daily"] == [
        {"date": "2026-03-02", "rain_mm": 5.0, "is_rainy": True},
        {"date": "2026-03-04", "rain_mm": 12.0, "is_rainy": True},
    ]
    assert [event["event_id"] for event in result.data["events"]] == [1, 2]


def test_event_analysis_returns_structured_requirement_for_event_ids(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    _write_rainfall(workspace)

    with pytest.raises(AnalysisInputRequired) as raised:
        AnalysisRunner(database, files_root).run(
            AnalysisRequest(project.id, batch.id, "event_response")
        )

    assert raised.value.field == "event_ids"
    assert "降雨场次" in str(raised.value)


def test_runner_preserves_event_ids_and_point_scope_for_event_response(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    _write_rainfall(workspace)

    result = AnalysisRunner(database, files_root).run(
        AnalysisRequest(
            project.id,
            batch.id,
            "event_response",
            points=["W1"],
            event_ids=[1],
        )
    )

    assert result.data["table"] == [
        {
            "point_id": "W1",
            "场次1_最大液位(m)": 1.0,
            "场次1_平均流量(m³/d)": 172.8,
            "场次1_峰值流量(L/s)": 2.0,
        }
    ]
    assert result.identity["parameters"]["event_ids"] == [1]


def test_runner_executes_rdii_with_selected_events_and_confirmed_baseline(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    _write_rainfall(workspace)

    result = AnalysisRunner(database, files_root).run(
        AnalysisRequest(
            project.id,
            batch.id,
            "rdii",
            points=["W1"],
            event_ids=[1],
        )
    )

    assert result.data["table"] == [{"point_id": "W1", "场次1": 0.0}]


def test_runner_executes_dry_risk_with_batch_site_information(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    _write_sites(workspace)
    runner = AnalysisRunner(database, files_root)

    first = runner.run(
        AnalysisRequest(
            project.id,
            batch.id,
            "risk",
            points=["W1"],
            scope="dry",
        )
    )
    reused = runner.run(
        AnalysisRequest(
            project.id,
            batch.id,
            "risk",
            points=["W1"],
            scope="dry",
        )
    )

    assert first.data["dry_risk"][0]["point_id"] == "W1"
    assert first.data["dry_risk"][0]["diameter_m"] == 1.0
    assert first.identity["parameters"]["scope"] == "dry"
    assert reused.run_id == first.run_id
    assert reused.reused is True


def test_agent_core_adapter_returns_needs_input_or_shared_background_job(
    tmp_path: Path,
) -> None:
    database, files_root, project, batch, workspace = _confirmed_batch(
        tmp_path
    )
    _write_rainfall(workspace)
    runner = AnalysisRunner(database, files_root)
    jobs = BackgroundJobService(database, runner, max_workers=1)

    missing = submit_core_analysis(
        jobs,
        runner,
        AnalysisRequest(project.id, batch.id, "event_response"),
    )
    submitted = submit_core_analysis(
        jobs,
        runner,
        AnalysisRequest(
            project.id,
            batch.id,
            "event_response",
            event_ids=[1],
        ),
    )

    assert missing["status"] == "needs_input"
    assert missing["missing"] == "event_ids"
    assert submitted["status"] == "ok"
    assert submitted["data"]["job_id"]
    jobs.shutdown()
