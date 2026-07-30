from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from quality.tests.test_web_app import FakeAgent, make_deps
from quality.tests.test_filter_baselines import write_standard_flow
from analysis.runs import AnalysisRequest
from agent.tools.module_tools import generate_report_impl
from web.app import _select_chat_artifacts, create_app
from web.projects import ProjectRepository


def test_created_project_is_retrievable_after_repository_restart(tmp_path: Path) -> None:
    database = tmp_path / "state" / "drainage.sqlite3"
    files = tmp_path / "projects"

    created = ProjectRepository(database, files).create("北区汛期监测")
    reopened = ProjectRepository(database, files)

    assert reopened.get(created.id) == created
    assert reopened.list() == [created]


def test_web_user_can_create_list_view_and_switch_projects(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)

    north = client.post("/api/projects", json={"name": "北区汛期监测"})
    south = client.post("/api/projects", json={"name": "南区旱天监测"})

    assert north.status_code == 201
    assert south.status_code == 201
    north_project = north.json()
    south_project = south.json()
    assert client.get("/api/projects").json() == [north_project, south_project]
    assert client.get(f"/api/projects/{north_project['id']}").json() == north_project

    selected = client.put(f"/api/projects/{south_project['id']}/selection")

    assert selected.status_code == 200
    workspace = selected.json()["current_workspace"]
    assert selected.json() == {
        "current_project": south_project,
        "current_workspace": workspace,
    }
    assert workspace["project_id"] == south_project["id"]
    assert client.get("/api/projects/selection").json() == {
        "current_project": south_project,
        "current_workspace": workspace,
    }

    reselected = client.put(f"/api/projects/{south_project['id']}/selection")
    assert reselected.json()["current_workspace"]["id"] == workspace["id"]


def test_project_file_download_is_confined_to_requested_project(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    north = client.post("/api/projects", json={"name": "北区"}).json()
    south = client.post("/api/projects", json={"name": "南区"}).json()

    north_upload = client.post(
        f"/api/projects/{north['id']}/files",
        files=[("files", ("记录.txt", b"north", "text/plain"))],
    )
    south_upload = client.post(
        f"/api/projects/{south['id']}/files",
        files=[("files", ("记录.txt", b"south", "text/plain"))],
    )

    assert north_upload.status_code == 200
    assert south_upload.status_code == 200
    assert client.get(
        f"/api/projects/{north['id']}/files/记录.txt"
    ).content == b"north"
    assert client.get(
        f"/api/projects/{south['id']}/files/记录.txt"
    ).content == b"south"

    escaped = client.get(
        f"/api/projects/{north['id']}/files/..%2F{south['id']}%2F记录.txt"
    )
    assert escaped.status_code == 403


def test_project_artifact_list_only_returns_current_tracked_results(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            tmp_path,
            deps_factory=make_deps,
            agent_factory=lambda _deps: FakeAgent(),
        )
    )
    project = client.post("/api/projects", json={"name": "北区"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    root = (
        tmp_path
        / "var"
        / "projects"
        / project["id"]
        / "batches"
        / workspace_id
    )
    artifact = root / "results" / "data_quality" / "run-1" / "result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    (root / "standard" / "flow.csv").write_text("ignored", encoding="utf-8")

    response = client.get(f"/api/projects/{project['id']}/workspace/artifacts")

    assert response.status_code == 200
    assert response.json() == {"workspace_id": workspace_id, "files": []}


def test_existing_project_restores_current_data_state(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "北区"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    standard = (
        tmp_path / "var" / "projects" / project["id"]
        / "batches" / workspace_id / "standard"
    )
    standard.mkdir(parents=True, exist_ok=True)
    (standard / "flow.csv").write_text("flow", encoding="utf-8")
    (standard / "manifest.json").write_text(
        '{"sources":[{"filename":"W1.csv"},{"filename":"W2.csv"}]}',
        encoding="utf-8",
    )
    (standard / "rainfall.csv").write_text("rain", encoding="utf-8")
    (standard / "auxiliary_manifest.json").write_text(
        '{"rainfall":"rain.csv"}',
        encoding="utf-8",
    )

    response = client.get(f"/api/projects/{project['id']}/workspace/state")

    assert response.status_code == 200
    assert response.json()["has_data"] is True
    assert response.json()["flow"] == {
        "present": True,
        "file_count": 2,
    }
    assert response.json()["rainfall"] == {
        "present": True,
        "filename": "rain.csv",
    }
    assert response.json()["sites"]["present"] is False


def test_reimport_reset_archives_chat_and_clears_current_artifacts(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "北区"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    root = (
        tmp_path / "var" / "projects" / project["id"]
        / "batches" / workspace_id
    )
    (root / "standard").mkdir(parents=True, exist_ok=True)
    (root / "standard" / "flow.csv").write_text("old", encoding="utf-8")
    app.state.conversations.repository.save(
        "session-1",
        project["id"],
        workspace_id,
        [{"role": "user", "content": "旧问题"}],
        app.state.deps.session,
    )

    response = client.post(f"/api/projects/{project['id']}/workspace/reset")

    assert response.status_code == 200
    assert not (root / "standard" / "flow.csv").exists()
    assert (root / "standard").is_dir()
    database = tmp_path / "var" / "drainage.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE session_id = 'session-1'"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM archived_agent_sessions
            WHERE session_id = 'session-1'
            """
        ).fetchone()[0] == 1


def test_revised_filter_archives_chat_and_clears_derived_results(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "筛选修改"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    root = app.state.projects.batch_workspace(project["id"], workspace_id)
    write_standard_flow(root)
    base_url = f"/api/projects/{project['id']}/batches/{workspace_id}"
    first_filter = client.post(
        f"{base_url}/filters",
        json={"expected_rows_per_day": 1},
    ).json()
    first_baseline = client.post(
        f"{base_url}/filters/{first_filter['filter_id']}/confirmation",
        json={"confirm": True},
    )
    assert first_baseline.status_code == 200
    app.state.conversations.repository.save(
        "filter-session",
        project["id"],
        workspace_id,
        [{"role": "user", "content": "旧筛选数据问题"}],
        app.state.deps.session,
    )
    (root / "results").mkdir(exist_ok=True)
    (root / "results" / "old-result.json").write_text("{}", encoding="utf-8")
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / "old-report.docx").write_text("old", encoding="utf-8")
    downloaded = client.get(
        f"{base_url}/filters/{first_filter['filter_id']}/download"
    )
    revision = client.post(
        f"{base_url}/filters/{first_filter['filter_id']}/revisions",
        files={
            "file": (
                "modified.xlsx",
                downloaded.content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    ).json()

    confirmed = client.post(
        f"{base_url}/filters/{revision['filter_id']}/confirmation",
        json={"confirm": True},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["derived_state_reset"] is True
    assert (root / "standard" / "flow.csv").is_file()
    assert not (root / "results" / "old-result.json").exists()
    assert not (root / "reports" / "old-report.docx").exists()
    state = client.get(f"/api/projects/{project['id']}/workspace/state").json()
    assert state["filter"]["filter_id"] == revision["filter_id"]
    with sqlite3.connect(tmp_path / "var" / "drainage.sqlite3") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM agent_sessions
            WHERE session_id = 'filter-session'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM archived_agent_sessions
            WHERE session_id = 'filter-session'
            """
        ).fetchone()[0] == 1


def test_reconfirming_same_filter_keeps_current_chat(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "重复确认"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    root = app.state.projects.batch_workspace(project["id"], workspace_id)
    write_standard_flow(root)
    base_url = f"/api/projects/{project['id']}/batches/{workspace_id}"
    candidate = client.post(
        f"{base_url}/filters",
        json={"expected_rows_per_day": 1},
    ).json()
    first = client.post(
        f"{base_url}/filters/{candidate['filter_id']}/confirmation",
        json={"confirm": True},
    )
    assert first.status_code == 200
    app.state.conversations.repository.save(
        "keep-session",
        project["id"],
        workspace_id,
        [{"role": "user", "content": "保留这条消息"}],
        app.state.deps.session,
    )

    repeated = client.post(
        f"{base_url}/filters/{candidate['filter_id']}/confirmation",
        json={"confirm": True},
    )

    assert repeated.status_code == 200
    assert repeated.json()["derived_state_reset"] is False
    with sqlite3.connect(tmp_path / "var" / "drainage.sqlite3") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM agent_sessions
            WHERE session_id = 'keep-session'
            """
        ).fetchone()[0] == 1


def test_project_downloads_offer_all_files_and_results_only(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "项目下载"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    root = app.state.projects.batch_workspace(project["id"], workspace_id)
    write_standard_flow(root)
    run = client.post(
        f"/api/projects/{project['id']}/batches/{workspace_id}"
        "/analysis-runs/data_quality",
        json={"points": ["W1"], "force_rerun": False},
    )
    assert run.status_code == 200

    all_files = client.get(f"/api/projects/{project['id']}/downloads/all")
    results = client.get(
        f"/api/projects/{project['id']}/downloads/results"
    )

    assert all_files.status_code == 200
    assert results.status_code == 200
    assert all_files.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(all_files.content)) as archive:
        all_names = set(archive.namelist())
    with zipfile.ZipFile(io.BytesIO(results.content)) as archive:
        result_names = set(archive.namelist())
    result_path = run.json()["artifacts"][0]
    assert "project.json" in all_names
    assert "standard/flow.csv" in all_names
    assert result_path in all_names
    assert result_path in result_names
    assert "standard/flow.csv" not in result_names


def test_chat_response_contains_only_files_generated_by_that_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "回答文件"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    write_standard_flow(
        app.state.projects.batch_workspace(project["id"], workspace_id)
    )

    def run_with_artifact(**kwargs: object) -> SimpleNamespace:
        result = app.state.analysis_runner.run(
            AnalysisRequest(
                project_id=project["id"],
                batch_id=workspace_id,
                algorithm="data_quality",
            )
        )
        return SimpleNamespace(
            session_id=str(kwargs["session_id"]),
            run_id=result.run_id,
            reply="分析完成",
        )

    monkeypatch.setattr(app.state.conversations, "run", run_with_artifact)
    response = client.post(
        "/api/chat",
        json={
            "message": "检查数据",
            "session_id": "answer-session",
            "project_id": project["id"],
            "batch_id": workspace_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["artifacts"] == [
        {
            "path": response.json()["artifacts"][0]["path"],
            "name": "数据质量结果",
            "size": response.json()["artifacts"][0]["size"],
        }
    ]
    second = client.post(
        "/api/chat",
        json={
            "message": "再描述一次",
            "session_id": "answer-session",
            "project_id": project["id"],
            "batch_id": workspace_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["artifacts"] == []


def test_report_chat_only_surfaces_report_and_comprehensive_workbook() -> None:
    current = [
        {"path": "results/rainfall/run/result.json", "name": "降雨分析结果", "size": 1},
        {"path": "results/rdii/run/result.json", "name": "RDII 分析结果", "size": 1},
        {"path": "reports/1-id/report_draft.docx", "name": "当前报告初稿", "size": 1},
        {
            "path": "reports/1-id/comprehensive_results.xlsx",
            "name": "当前综合结果表",
            "size": 1,
        },
    ]

    selected = _select_chat_artifacts(current, set())

    assert [item["name"] for item in selected] == [
        "当前报告初稿",
        "当前综合结果表",
    ]


def test_report_request_runs_required_analysis_when_project_has_no_results(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "自动报告"}).json()
    selected = client.put(f"/api/projects/{project['id']}/selection").json()
    workspace_id = selected["current_workspace"]["id"]
    root = app.state.projects.batch_workspace(project["id"], workspace_id)
    write_standard_flow(root)
    base_url = f"/api/projects/{project['id']}/batches/{workspace_id}"
    candidate = client.post(
        f"{base_url}/filters",
        json={"expected_rows_per_day": 1},
    ).json()
    assert client.post(
        f"{base_url}/filters/{candidate['filter_id']}/confirmation",
        json={"confirm": True},
    ).status_code == 200

    result = generate_report_impl(
        app.state.deps,
        sections=["数据体检", "排污规律"],
    )

    assert result["status"] == "ok"
    assert app.state.analysis_runner.current(
        project["id"], workspace_id, "data_quality"
    ) is not None
    assert app.state.analysis_runner.current(
        project["id"], workspace_id, "patterns"
    ) is not None
    assert len(result["artifacts"]) == 2


def test_project_upload_rejects_executable_and_preserves_existing_file(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "上传安全"}).json()
    endpoint = f"/api/projects/{project['id']}/files"

    rejected = client.post(
        endpoint,
        files=[
            (
                "files",
                ("payload.exe", b"MZ", "application/octet-stream"),
            )
        ],
    )
    first = client.post(
        endpoint,
        files=[("files", ("记录.txt", b"original", "text/plain"))],
    )
    duplicate = client.post(
        endpoint,
        files=[("files", ("记录.txt", b"replacement", "text/plain"))],
    )

    assert rejected.status_code == 400
    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert (
        app.state.projects.workspace(project["id"]) / "记录.txt"
    ).read_bytes() == b"original"


def test_batch_artifacts_require_matching_project_and_batch(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "隔离"}).json()
    first = client.post(
        f"/api/projects/{project['id']}/batches", json={"name": "第一批"}
    ).json()
    second = client.post(
        f"/api/projects/{project['id']}/batches", json={"name": "第二批"}
    ).json()
    artifact = (
        app.state.projects.batch_workspace(project["id"], first["id"])
        / "results"
        / "result.json"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("first", encoding="utf-8")

    allowed = client.get(
        f"/api/projects/{project['id']}/batches/{first['id']}"
        "/files/results/result.json"
    )
    wrong_batch = client.get(
        f"/api/projects/{project['id']}/batches/{second['id']}"
        "/files/../"
        f"{first['id']}/results/result.json"
    )
    legacy_bypass = client.get(
        f"/api/projects/{project['id']}/files/batches/{first['id']}"
        "/results/result.json"
    )

    assert allowed.status_code == 200
    assert allowed.text == "first"
    assert wrong_batch.status_code in {403, 404}
    assert legacy_bypass.status_code == 403


def test_index_exposes_project_workbench(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert 'id="projectForm"' in response.text
    assert 'list="projectNames"' in response.text
    assert 'id="projectNames"' in response.text
    assert 'id="projectSelect"' not in response.text
    assert 'id="projectList"' not in response.text
    assert "当前项目：" not in response.text
    assert "height: 100dvh" in response.text
    assert "grid-template-rows: auto minmax(0, 1fr) auto" in response.text
    assert "overscroll-behavior: contain" in response.text
    assert "创建或打开项目" in response.text
    assert 'fetch("/api/projects"' in response.text
    assert 'method: "PUT"' in response.text


def test_web_projects_survive_application_restart(tmp_path: Path) -> None:
    first_app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )
    created = TestClient(first_app).post(
        "/api/projects",
        json={"name": "长期监测项目"},
    ).json()

    restarted_app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )

    assert TestClient(restarted_app).get("/api/projects").json() == [created]
