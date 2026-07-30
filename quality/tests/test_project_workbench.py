from __future__ import annotations

from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from quality.tests.test_web_app import FakeAgent, make_deps
from web.app import create_app
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


def test_project_artifact_list_only_returns_selected_project_workspace(
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
    assert response.json() == {
        "workspace_id": workspace_id,
        "files": [
            {
                "path": "results/data_quality/run-1/result.json",
                "name": "result.json",
                "size": 2,
            }
        ],
    }


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
