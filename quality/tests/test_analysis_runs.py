from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.io.standard import STANDARD_FLOW_COLUMNS, STANDARD_FLOW_UNITS
from analysis.runs import (
    AnalysisPreconditionError,
    AnalysisRequest,
    AnalysisRunner,
)
from agent.tools.analysis_runs import run_data_quality_analysis
from web.projects import ProjectRepository


def _write_standard_flow(batch_workspace: Path) -> None:
    standard = batch_workspace / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    (standard / "flow.csv").write_text(
        "\n".join(
            [
                ",".join(STANDARD_FLOW_COLUMNS),
                "2026-03-07T00:00:00,D1,W1,2.5,1.2,0.4",
                "2026-03-07T00:01:00,D1,W1,2.6,1.2,0.4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (standard / "manifest.json").write_text(
        json.dumps(
            {
                "contract_version": 1,
                "kind": "standard_flow",
                "columns": STANDARD_FLOW_COLUMNS,
                "units": STANDARD_FLOW_UNITS,
                "source_import_id": "import-1",
                "source_sha256": "source-sha",
                "source_encoding": "utf-8",
                "mapping": {},
                "source_units": {},
                "file": "flow.csv",
            }
        ),
        encoding="utf-8",
    )


def test_confirmed_standard_data_runs_quality_check_and_creates_versioned_result(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("北区监测")
    batch = projects.create_batch(project.id, "三月流量")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    runner = AnalysisRunner(database, files_root)

    result = runner.run(
        AnalysisRequest(
            project_id=project.id,
            batch_id=batch.id,
            algorithm="data_quality",
        )
    )

    assert result.status == "succeeded"
    assert result.version == 1
    assert result.reused is False
    assert result.identity["baseline"] == {"kind": "none", "identity": None}
    assert result.data["table"] == [
        {
            "point_id": "W1",
            "record_count": 2,
            "monitoring_days": 1,
            "theoretical_count": 1440,
            "collection_rate": 2 / 1440,
        }
    ]
    assert result.artifacts == [
        f"results/data_quality/{result.run_id}/result.json"
    ]
    artifact = (
        projects.batch_workspace(project.id, batch.id) / result.artifacts[0]
    )
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["data"] == result.data
    assert runner.current(project.id, batch.id, "data_quality") == result


def test_missing_confirmed_standard_data_never_falls_back_to_raw_files(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("待确认项目")
    batch = projects.create_batch(project.id, "待确认批次")
    raw = projects.batch_workspace(project.id, batch.id) / "inputs" / "raw-1"
    raw.mkdir(parents=True)
    (raw / "flow.csv").write_text(
        "timestamp,point_id,flow_lps\n2026-03-07,W9,9\n",
        encoding="utf-8",
    )
    legacy = tmp_path / "resources" / "data"
    legacy.mkdir(parents=True)
    (legacy / "flow.csv").write_text(
        "timestamp,point_id,flow_lps\n2026-03-07,W8,8\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AnalysisPreconditionError,
        match="请先导入并确认当前分析批次的标准数据",
    ):
        AnalysisRunner(database, files_root).run(
            AnalysisRequest(
                project_id=project.id,
                batch_id=batch.id,
                algorithm="data_quality",
            )
        )


def test_identical_identity_reuses_result_and_explicit_rerun_creates_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("复用项目")
    batch = projects.create_batch(project.id, "复用批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    runner = AnalysisRunner(database, files_root)
    request = AnalysisRequest(
        project_id=project.id,
        batch_id=batch.id,
        algorithm="data_quality",
        points=["W1", "W1"],
    )

    first = runner.run(request)
    reused = runner.run(request)
    rerun = runner.run(
        AnalysisRequest(
            project_id=project.id,
            batch_id=batch.id,
            algorithm="data_quality",
            points=["W1"],
            force_rerun=True,
        )
    )

    assert reused.run_id == first.run_id
    assert reused.version == 1
    assert reused.reused is True
    assert rerun.run_id != first.run_id
    assert rerun.version == 2
    assert rerun.reused is False
    assert runner.current(project.id, batch.id, "data_quality") == rerun


def test_normalized_parameters_define_identity_and_changes_create_new_current_result(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("身份项目")
    batch = projects.create_batch(project.id, "身份批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    runner = AnalysisRunner(database, files_root)

    first = runner.run(
        AnalysisRequest(
            project_id=project.id,
            batch_id=batch.id,
            algorithm="data_quality",
            points=["W1"],
            start="2026-03-07",
        )
    )
    equivalent = runner.run(
        AnalysisRequest(
            project_id=project.id,
            batch_id=batch.id,
            algorithm="data_quality",
            points=["W1", "W1"],
            start="2026-03-07T00:00:00",
        )
    )
    changed = runner.run(
        AnalysisRequest(
            project_id=project.id,
            batch_id=batch.id,
            algorithm="data_quality",
            points=[],
            start="2026-03-07T00:00:00",
        )
    )

    assert equivalent.run_id == first.run_id
    assert equivalent.reused is True
    assert changed.run_id != first.run_id
    assert changed.version == 2
    assert runner.current(project.id, batch.id, "data_quality") == changed


def test_standard_input_change_creates_a_new_current_result_without_overwrite(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("输入身份项目")
    batch = projects.create_batch(project.id, "输入身份批次")
    workspace = projects.batch_workspace(project.id, batch.id)
    _write_standard_flow(workspace)
    runner = AnalysisRunner(database, files_root)
    first = runner.run(
        AnalysisRequest(project.id, batch.id, "data_quality")
    )
    first_artifact = workspace / first.artifacts[0]

    with (workspace / "standard" / "flow.csv").open(
        "a",
        encoding="utf-8",
    ) as flow:
        flow.write("2026-03-07T00:02:00,D1,W1,2.7,1.2,0.4\n")
    changed = runner.run(
        AnalysisRequest(project.id, batch.id, "data_quality")
    )

    assert changed.version == 2
    assert changed.identity["standard_input"] != first.identity["standard_input"]
    assert changed.data["table"][0]["record_count"] == 3
    assert first_artifact.is_file()
    assert json.loads(first_artifact.read_text(encoding="utf-8"))["version"] == 1
    assert runner.current(project.id, batch.id, "data_quality") == changed


def test_runner_rejects_cross_project_and_cross_batch_access(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    north = projects.create("北区")
    south = projects.create("南区")
    north_batch = projects.create_batch(north.id, "北区批次")
    south_batch = projects.create_batch(south.id, "南区批次")
    _write_standard_flow(projects.batch_workspace(north.id, north_batch.id))
    _write_standard_flow(projects.batch_workspace(south.id, south_batch.id))
    runner = AnalysisRunner(database, files_root)

    with pytest.raises(
        LookupError,
        match="分析批次不存在或不属于当前监测项目",
    ):
        runner.run(
            AnalysisRequest(
                project_id=north.id,
                batch_id=south_batch.id,
                algorithm="data_quality",
            )
        )


def test_web_runs_data_quality_through_the_analysis_runner(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from quality.tests.test_web_app import FakeAgent, make_deps
    from web.app import create_app

    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Web 项目"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "Web 批次"},
    ).json()
    _write_standard_flow(
        app.state.projects.batch_workspace(project["id"], batch["id"])
    )

    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        "/analysis-runs/data_quality",
        json={"points": ["W1"], "force_rerun": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["version"] == 1
    assert body["data"]["table"][0]["collection_rate"] == 2 / 1440
    assert app.state.analysis_runner.current(
        project["id"],
        batch["id"],
        "data_quality",
    ).run_id == body["run_id"]


def test_web_workbench_exposes_data_quality_run_controls(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from quality.tests.test_web_app import FakeAgent, make_deps
    from web.app import create_app

    response = TestClient(
        create_app(
            tmp_path,
            deps_factory=make_deps,
            agent_factory=lambda _deps: FakeAgent(),
        )
    ).get("/")

    assert response.status_code == 200
    assert 'id="runDataQualityButton"' not in response.text
    assert 'id="dataQualityResult"' not in response.text
    assert 'data-prompt="检查当前数据质量"' in response.text


def test_agent_adapter_uses_the_same_analysis_runner_result(tmp_path: Path) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("Agent 项目")
    batch = projects.create_batch(project.id, "Agent 批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    runner = AnalysisRunner(database, files_root)
    web_equivalent = runner.run(
        AnalysisRequest(
            project_id=project.id,
            batch_id=batch.id,
            algorithm="data_quality",
            points=["W1"],
        )
    )

    tool_result = run_data_quality_analysis(
        runner,
        project_id=project.id,
        batch_id=batch.id,
        points=["W1"],
    )

    assert tool_result["status"] == "ok"
    assert tool_result["data"]["run_id"] == web_equivalent.run_id
    assert tool_result["data"]["reused"] is True
    assert tool_result["data"]["version"] == 1
    assert tool_result["data"]["table"] == web_equivalent.data["table"]
