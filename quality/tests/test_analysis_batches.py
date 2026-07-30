from __future__ import annotations

from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from quality.tests.test_web_app import FakeAgent, make_deps
from web.app import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            tmp_path,
            deps_factory=make_deps,
            agent_factory=lambda _deps: FakeAgent(),
        )
    )


def test_web_user_can_create_list_view_and_switch_analysis_batches(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "北区监测"}).json()

    first = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "三月旱天分析"},
    )
    second = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "四月雨天分析"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_batch = first.json()
    second_batch = second.json()
    assert client.get(f"/api/projects/{project['id']}/batches").json() == [
        first_batch,
        second_batch,
    ]
    assert client.get(
        f"/api/projects/{project['id']}/batches/{first_batch['id']}"
    ).json() == first_batch

    selected = client.put(
        f"/api/projects/{project['id']}/batches/{second_batch['id']}/selection"
    )

    assert selected.status_code == 200
    assert selected.json() == {"current_batch": second_batch}
    assert client.get(
        f"/api/projects/{project['id']}/batches/selection"
    ).json() == {"current_batch": second_batch}


def test_analysis_batch_cannot_be_accessed_or_selected_across_projects(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    north = client.post("/api/projects", json={"name": "北区"}).json()
    south = client.post("/api/projects", json={"name": "南区"}).json()
    north_batch = client.post(
        f"/api/projects/{north['id']}/batches",
        json={"name": "北区三月分析"},
    ).json()

    cross_project_read = client.get(
        f"/api/projects/{south['id']}/batches/{north_batch['id']}"
    )
    cross_project_selection = client.put(
        f"/api/projects/{south['id']}/batches/{north_batch['id']}/selection"
    )

    assert cross_project_read.status_code == 404
    assert cross_project_selection.status_code == 404
    assert client.get(f"/api/projects/{south['id']}/batches").json() == []
    assert client.get(
        f"/api/projects/{south['id']}/batches/selection"
    ).json() == {"current_batch": None}


def test_new_batches_have_isolated_empty_workspaces_across_two_projects(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    north = client.post("/api/projects", json={"name": "北区"}).json()
    south = client.post("/api/projects", json={"name": "南区"}).json()
    north_first = client.post(
        f"/api/projects/{north['id']}/batches",
        json={"name": "北区第一批"},
    ).json()
    south_batch = client.post(
        f"/api/projects/{south['id']}/batches",
        json={"name": "南区第一批"},
    ).json()

    north_first_root = (
        tmp_path / "var" / "projects" / north["id"] / "batches" / north_first["id"]
    )
    marker = north_first_root / "inputs" / "source.csv"
    marker.write_text("north-only", encoding="utf-8")
    north_second = client.post(
        f"/api/projects/{north['id']}/batches",
        json={"name": "北区第二批"},
    ).json()

    batch_roots = [
        north_first_root,
        tmp_path / "var" / "projects" / north["id"] / "batches" / north_second["id"],
        tmp_path / "var" / "projects" / south["id"] / "batches" / south_batch["id"],
    ]
    expected_directories = {
        "inputs",
        "standard",
        "baseline",
        "results",
        "sessions",
        "jobs",
    }
    for batch_root in batch_roots:
        assert {path.name for path in batch_root.iterdir()} == expected_directories

    assert marker.read_text(encoding="utf-8") == "north-only"
    assert not (batch_roots[1] / "inputs" / marker.name).exists()
    assert not (batch_roots[2] / "inputs" / marker.name).exists()


def test_index_exposes_project_first_workbench(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert 'id="batchForm"' not in response.text
    assert 'id="batchList"' not in response.text
    assert "分析批次" not in response.text
    assert "输入新名称创建项目" in response.text
    assert "/batches" in response.text
    assert "/selection" in response.text
