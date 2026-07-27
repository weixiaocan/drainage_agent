from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from analysis.jobs import BackgroundJobService
from analysis.runs import AnalysisRequest, AnalysisRunner
from analysis.io.standard import STANDARD_FLOW_COLUMNS, STANDARD_FLOW_UNITS
from agent.tools.analysis_runs import submit_data_quality_analysis
from web.projects import ProjectRepository


def _write_standard_flow(batch_workspace: Path, *, rows: int = 2) -> None:
    standard = batch_workspace / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 3, 7)
    records = []
    for index in range(rows):
        timestamp = (start + timedelta(minutes=index)).isoformat()
        records.append(
            f"{timestamp},D1,W1,{2.5 + index / 10},1.2,0.4"
        )
    (standard / "flow.csv").write_text(
        "\n".join([",".join(STANDARD_FLOW_COLUMNS), *records]) + "\n",
        encoding="utf-8",
    )
    (standard / "manifest.json").write_text(
        json.dumps(
            {
                "contract_version": 1,
                "kind": "standard_flow",
                "columns": STANDARD_FLOW_COLUMNS,
                "units": STANDARD_FLOW_UNITS,
                "file": "flow.csv",
            }
        ),
        encoding="utf-8",
    )


def _wait_for_terminal(
    jobs: BackgroundJobService,
    project_id: str,
    batch_id: str,
    job_id: str,
) -> object:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = jobs.get(project_id, batch_id, job_id)
        assert job is not None
        if job.status in {"succeeded", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("background job did not finish")


def test_submit_persists_job_id_and_real_data_quality_result(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("后台作业项目")
    batch = projects.create_batch(project.id, "数据质量批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    runner = AnalysisRunner(database, files_root)
    jobs = BackgroundJobService(database, runner, max_workers=1)

    submitted = jobs.submit(
        AnalysisRequest(project.id, batch.id, "data_quality")
    )

    assert submitted.job_id
    assert submitted.project_id == project.id
    assert submitted.batch_id == batch.id
    assert submitted.request == AnalysisRequest(
        project.id, batch.id, "data_quality"
    )
    completed = _wait_for_terminal(
        jobs, project.id, batch.id, submitted.job_id
    )
    assert completed.status == "succeeded"
    assert completed.result_run_id
    assert runner.current(project.id, batch.id, "data_quality").run_id == (
        completed.result_run_id
    )
    jobs.shutdown()


def test_failed_precondition_is_persisted_without_sensitive_traceback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("缺少输入项目")
    batch = projects.create_batch(project.id, "缺少标准数据")
    jobs = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )

    submitted = jobs.submit(
        AnalysisRequest(project.id, batch.id, "data_quality")
    )
    failed = _wait_for_terminal(jobs, project.id, batch.id, submitted.job_id)

    assert failed.status == "failed"
    assert failed.step == "分析失败"
    assert failed.progress == 100
    assert "请先" in failed.error_summary
    assert "Traceback" not in failed.error_summary
    assert str(tmp_path) not in failed.error_summary
    jobs.shutdown()


def test_reused_analysis_result_completes_a_new_job_with_same_run_id(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("复用项目")
    batch = projects.create_batch(project.id, "复用批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    runner = AnalysisRunner(database, files_root)
    jobs = BackgroundJobService(database, runner, max_workers=1)
    request = AnalysisRequest(project.id, batch.id, "data_quality")

    first = jobs.submit(request)
    first_done = _wait_for_terminal(jobs, project.id, batch.id, first.job_id)
    second = jobs.submit(request)
    second_done = _wait_for_terminal(
        jobs, project.id, batch.id, second.job_id
    )

    assert first.job_id != second.job_id
    assert second_done.status == "succeeded"
    assert second_done.result_run_id == first_done.result_run_id
    assert len(jobs.list_for_batch(project.id, batch.id)) == 2
    jobs.shutdown()


def test_queries_are_strictly_scoped_to_project_and_batch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    north = projects.create("北区")
    south = projects.create("南区")
    north_batch = projects.create_batch(north.id, "北区批次")
    south_batch = projects.create_batch(south.id, "南区批次")
    _write_standard_flow(
        projects.batch_workspace(north.id, north_batch.id)
    )
    jobs = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    submitted = jobs.submit(
        AnalysisRequest(north.id, north_batch.id, "data_quality")
    )
    _wait_for_terminal(jobs, north.id, north_batch.id, submitted.job_id)

    assert jobs.get(south.id, north_batch.id, submitted.job_id) is None
    assert jobs.get(north.id, south_batch.id, submitted.job_id) is None
    assert jobs.list_for_batch(south.id, north_batch.id) == []
    assert jobs.list_for_batch(north.id, south_batch.id) == []
    jobs.shutdown()


def test_restart_preserves_terminal_jobs_and_marks_queued_jobs_interrupted(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("重启项目")
    completed_batch = projects.create_batch(project.id, "已完成")
    _write_standard_flow(
        projects.batch_workspace(project.id, completed_batch.id)
    )
    first_service = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    completed = first_service.submit(
        AnalysisRequest(project.id, completed_batch.id, "data_quality")
    )
    completed = _wait_for_terminal(
        first_service, project.id, completed_batch.id, completed.job_id
    )

    queued_batches = []
    queued_jobs = []
    for index in range(12):
        batch = projects.create_batch(project.id, f"排队-{index}")
        _write_standard_flow(
            projects.batch_workspace(project.id, batch.id),
            rows=5000,
        )
        queued_batches.append(batch)
        queued_jobs.append(
            first_service.submit(
                AnalysisRequest(project.id, batch.id, "data_quality")
            )
        )
    first_service.shutdown(wait=False, cancel_futures=True)
    queued = next(
        job
        for batch, submitted in zip(queued_batches, queued_jobs)
        if (
            job := first_service.get(
                project.id, batch.id, submitted.job_id
            )
        ).status == "queued"
    )

    restarted = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    preserved = restarted.get(
        project.id, completed_batch.id, completed.job_id
    )
    interrupted = restarted.get(
        queued.project_id, queued.batch_id, queued.job_id
    )

    assert preserved.status == "succeeded"
    assert preserved.result_run_id == completed.result_run_id
    assert interrupted.status == "failed"
    assert interrupted.step == "应用重启后停止"
    assert "重新提交" in interrupted.error_summary
    restarted.shutdown()


def test_restart_marks_a_running_job_failed_and_late_worker_cannot_overwrite_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("运行中重启")
    batch = projects.create_batch(project.id, "长作业")
    _write_standard_flow(
        projects.batch_workspace(project.id, batch.id),
        rows=100000,
    )
    first_service = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    submitted = first_service.submit(
        AnalysisRequest(project.id, batch.id, "data_quality")
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        running = first_service.get(
            project.id, batch.id, submitted.job_id
        )
        if running.status == "running":
            break
        time.sleep(0.001)
    assert running.status == "running"
    first_service.shutdown(wait=False)

    restarted = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    interrupted = restarted.get(
        project.id, batch.id, submitted.job_id
    )
    assert interrupted.status == "failed"
    assert interrupted.step == "应用重启后停止"

    first_service.shutdown()
    still_interrupted = restarted.get(
        project.id, batch.id, submitted.job_id
    )
    assert still_interrupted.status == "failed"
    assert still_interrupted.result_run_id is None
    restarted.shutdown()


def test_job_serialization_retains_the_complete_analysis_request(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("完整请求")
    batch = projects.create_batch(project.id, "参数批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    jobs = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    request = AnalysisRequest(
        project.id,
        batch.id,
        "data_quality",
        points=["W1"],
        start="2026-03-07T00:00:00",
        end="2026-03-07T00:01:00",
        force_rerun=True,
    )

    submitted = jobs.submit(request)
    persisted = jobs.get(project.id, batch.id, submitted.job_id)

    assert asdict(persisted.request) == asdict(request)
    assert persisted.created_at
    jobs.shutdown()


def test_agent_submits_through_the_shared_background_job_service(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("Agent 后台项目")
    batch = projects.create_batch(project.id, "Agent 后台批次")
    _write_standard_flow(projects.batch_workspace(project.id, batch.id))
    jobs = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )

    response = submit_data_quality_analysis(
        jobs,
        project_id=project.id,
        batch_id=batch.id,
        points=["W1"],
    )

    assert response["status"] == "ok"
    assert response["data"]["job_id"]
    completed = _wait_for_terminal(
        jobs,
        project.id,
        batch.id,
        response["data"]["job_id"],
    )
    assert completed.status == "succeeded"
    jobs.shutdown()


def test_web_submits_queries_lists_and_opens_background_result(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from quality.tests.test_web_app import FakeAgent, make_deps
    from web.app import create_app

    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
        background_job_workers=1,
    )
    with TestClient(app) as client:
        project = client.post(
            "/api/projects", json={"name": "Web 后台项目"}
        ).json()
        batch = client.post(
            f"/api/projects/{project['id']}/batches",
            json={"name": "Web 后台批次"},
        ).json()
        _write_standard_flow(
            app.state.projects.batch_workspace(project["id"], batch["id"])
        )
        endpoint = (
            f"/api/projects/{project['id']}/batches/{batch['id']}"
            "/analysis-jobs/data_quality"
        )

        response = client.post(endpoint, json={"points": ["W1"]})

        assert response.status_code == 202
        submitted = response.json()
        assert submitted["job_id"]
        assert submitted["status"] in {"queued", "running", "succeeded"}
        job_endpoint = (
            f"/api/projects/{project['id']}/batches/{batch['id']}"
            f"/analysis-jobs/{submitted['job_id']}"
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            body = client.get(job_endpoint).json()
            if body["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert body["status"] == "succeeded"
        assert body["progress"] == 100
        assert body["result_url"]
        result = client.get(body["result_url"])
        assert result.status_code == 200
        assert result.json()["run_id"] == body["result_run_id"]
        listed = client.get(
            f"/api/projects/{project['id']}/batches/{batch['id']}"
            "/analysis-jobs"
        )
        assert [job["job_id"] for job in listed.json()] == [
            submitted["job_id"]
        ]

        other = client.post(
            "/api/projects", json={"name": "其他项目"}
        ).json()
        assert client.get(
            job_endpoint.replace(project["id"], other["id"])
        ).status_code == 404
        assert client.get(
            body["result_url"].replace(project["id"], other["id"])
        ).status_code == 404


def test_local_executor_never_exceeds_its_configured_concurrency(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files_root = tmp_path / "projects"
    projects = ProjectRepository(database, files_root)
    project = projects.create("有限并发")
    jobs = BackgroundJobService(
        database,
        AnalysisRunner(database, files_root),
        max_workers=1,
    )
    batch = projects.create_batch(project.id, "并发批次")
    _write_standard_flow(
        projects.batch_workspace(project.id, batch.id),
        rows=20000,
    )
    submitted = [
        jobs.submit(
            AnalysisRequest(
                project.id,
                batch.id,
                "data_quality",
                force_rerun=True,
            )
        )
        for _index in range(8)
    ]

    maximum_running = 0
    saw_queued = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        states = jobs.list_for_batch(project.id, batch.id)
        running = sum(state.status == "running" for state in states)
        queued = sum(state.status == "queued" for state in states)
        maximum_running = max(maximum_running, running)
        saw_queued |= queued > 0
        if all(state.status in {"succeeded", "failed"} for state in states):
            break
        time.sleep(0.005)

    assert maximum_running <= 1
    assert saw_queued
    assert all(state.status == "succeeded" for state in states)
    run_ids = {state.result_run_id for state in states}
    assert len(run_ids) == 8
    results = [
        jobs.runner.get(project.id, batch.id, run_id)
        for run_id in run_ids
    ]
    assert sorted(result.version for result in results) == list(range(1, 9))
    assert all(
        (
            projects.batch_workspace(project.id, batch.id)
            / result.artifacts[0]
        ).is_file()
        for result in results
    )
    jobs.shutdown()
