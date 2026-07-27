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
    assert selected.json() == {"current_project": south_project}
    assert client.get("/api/projects/selection").json() == {
        "current_project": south_project
    }


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


def test_index_exposes_project_workbench(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert 'id="projectForm"' in response.text
    assert 'id="projectList"' in response.text
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
