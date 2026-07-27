from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from quality.tests.test_web_app import FakeAgent, make_deps
from analysis.io import StandardDataStore
from analysis.io.standard import STANDARD_FLOW_COLUMNS, STANDARD_FLOW_UNITS
from web.app import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            tmp_path,
            deps_factory=make_deps,
            agent_factory=lambda _deps: FakeAgent(),
        )
    )


def _create_batch(client: TestClient, project_id: str, name: str) -> dict[str, str]:
    return client.post(
        f"/api/projects/{project_id}/batches",
        json={"name": name},
    ).json()


def _write_source_records(
    tmp_path: Path,
    project_id: str,
    batch_id: str,
    records: list[dict[str, Any]],
) -> Path:
    source = (
        tmp_path
        / "var"
        / "projects"
        / project_id
        / "batches"
        / batch_id
        / "standard"
        / "flow.csv"
    )
    rows = [
        {
            "timestamp": record["timestamp"],
            "device_id": record["values"].get("device_id"),
            "point_id": record["point_id"],
            "flow_lps": record["values"].get("flow_lps"),
            "level_m": record["values"].get("level_m"),
            "velocity_mps": record["values"].get("velocity_mps"),
        }
        for record in records
    ]
    pd.DataFrame(rows, columns=STANDARD_FLOW_COLUMNS).to_csv(
        source,
        index=False,
        encoding="utf-8",
    )
    (source.parent / "manifest.json").write_text(
        json.dumps(
            {
                "contract_version": 1,
                "kind": "standard_flow",
                "columns": STANDARD_FLOW_COLUMNS,
                "units": STANDARD_FLOW_UNITS,
                "file": "flow.csv",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source


def test_derived_batch_requires_two_sources_from_the_same_project(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    north = client.post("/api/projects", json={"name": "北区"}).json()
    south = client.post("/api/projects", json={"name": "南区"}).json()
    north_batch = _create_batch(client, north["id"], "北区一月")
    south_batch = _create_batch(client, south["id"], "南区一月")

    too_few = client.post(
        f"/api/projects/{north['id']}/derived-batches",
        json={"name": "北区派生", "source_batch_ids": [north_batch["id"]]},
    )
    cross_project = client.post(
        f"/api/projects/{north['id']}/derived-batches",
        json={
            "name": "混合派生",
            "source_batch_ids": [north_batch["id"], south_batch["id"]],
        },
    )

    assert too_few.status_code == 400
    assert too_few.json()["detail"] == "派生分析批次至少需要两个来源批次"
    assert cross_project.status_code == 404
    assert cross_project.json()["detail"] == "来源分析批次不存在于当前监测项目"
    assert client.get(f"/api/projects/{north['id']}/batches").json() == [
        north_batch
    ]


def test_derived_batch_requires_confirmed_standard_data_for_every_source(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "北区"}).json()
    first = _create_batch(client, project["id"], "来源一")
    second = _create_batch(client, project["id"], "来源二")

    response = client.post(
        f"/api/projects/{project['id']}/derived-batches",
        json={
            "name": "缺少标准数据",
            "source_batch_ids": [first["id"], second["id"]],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "来源批次标准数据尚未确认生成"
    assert client.get(f"/api/projects/{project['id']}/batches").json() == [
        first,
        second,
    ]


def test_web_user_can_merge_non_conflicting_sources_without_modifying_them(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "北区"}).json()
    january = _create_batch(client, project["id"], "一月")
    february = _create_batch(client, project["id"], "二月")
    january_source = _write_source_records(
        tmp_path,
        project["id"],
        january["id"],
        [
            {
                "point_id": "W1",
                "timestamp": "2026-01-01T00:00:00",
                "values": {"flow_lps": 1.25},
            }
        ],
    )
    february_source = _write_source_records(
        tmp_path,
        project["id"],
        february["id"],
        [
            {
                "point_id": "W2",
                "timestamp": "2026-02-01T00:00:00",
                "values": {"flow_lps": 2.5},
            }
        ],
    )
    original_sources = (
        january_source.read_bytes(),
        february_source.read_bytes(),
    )

    created = client.post(
        f"/api/projects/{project['id']}/derived-batches",
        json={
            "name": "一二月合并",
            "source_batch_ids": [january["id"], february["id"]],
        },
    )

    assert created.status_code == 201
    derived = created.json()
    assert client.get(
        f"/api/projects/{project['id']}/batches/{derived['id']}/sources"
    ).json() == [january, february]
    merged = StandardDataStore(tmp_path / "var" / "projects").load_flow(
        project["id"],
        derived["id"],
    )
    assert merged["point_id"].tolist() == ["W1", "W2"]
    assert merged["flow_lps"].tolist() == [1.25, 2.5]
    manifest = json.loads(
        (
            tmp_path
            / "var"
            / "projects"
            / project["id"]
            / "batches"
            / derived["id"]
            / "standard"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["source_batch_ids"] == [january["id"], february["id"]]
    assert january_source.read_bytes() == original_sources[0]
    assert february_source.read_bytes() == original_sources[1]


def test_duplicate_and_value_conflicts_stop_merge_and_report_scope(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "北区"}).json()
    first = _create_batch(client, project["id"], "来源一")
    second = _create_batch(client, project["id"], "来源二")
    _write_source_records(
        tmp_path,
        project["id"],
        first["id"],
        [
            {
                "point_id": "W1",
                "timestamp": "2026-03-01T00:00:00",
                "values": {"flow_lps": 1.0},
            },
            {
                "point_id": "W2",
                "timestamp": "2026-03-02T00:00:00",
                "values": {"flow_lps": 2.0},
            },
        ],
    )
    _write_source_records(
        tmp_path,
        project["id"],
        second["id"],
        [
            {
                "point_id": "W1",
                "timestamp": "2026-03-01T00:00:00",
                "values": {"flow_lps": 1.0},
            },
            {
                "point_id": "W2",
                "timestamp": "2026-03-02T00:00:00",
                "values": {"flow_lps": 3.0},
            },
        ],
    )

    response = client.post(
        f"/api/projects/{project['id']}/derived-batches",
        json={
            "name": "冲突合并",
            "source_batch_ids": [first["id"], second["id"]],
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "status": "conflicts",
        "conflicts": {
            "count": 2,
            "duplicate_count": 1,
            "value_conflict_count": 1,
            "point_ids": ["W1", "W2"],
            "time_start": "2026-03-01T00:00:00",
            "time_end": "2026-03-02T00:00:00",
        },
        "conflict_items": [
            {
                "point_id": "W1",
                "timestamp": "2026-03-01T00:00:00",
                "kind": "duplicate",
                "source_batch_ids": [first["id"], second["id"]],
            },
            {
                "point_id": "W2",
                "timestamp": "2026-03-02T00:00:00",
                "kind": "value_conflict",
                "source_batch_ids": [first["id"], second["id"]],
            },
        ],
    }
    assert client.get(f"/api/projects/{project['id']}/batches").json() == [
        first,
        second,
    ]


def test_engineer_must_choose_a_source_for_every_conflict_before_merge(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "北区"}).json()
    first = _create_batch(client, project["id"], "来源一")
    second = _create_batch(client, project["id"], "来源二")
    shared_identities = [
        ("W1", "2026-03-01T00:00:00"),
        ("W2", "2026-03-02T00:00:00"),
    ]
    _write_source_records(
        tmp_path,
        project["id"],
        first["id"],
        [
            {"point_id": point, "timestamp": timestamp, "values": {"flow_lps": value}}
            for (point, timestamp), value in zip(
                shared_identities,
                [1.0, 2.0],
                strict=True,
            )
        ],
    )
    _write_source_records(
        tmp_path,
        project["id"],
        second["id"],
        [
            {"point_id": point, "timestamp": timestamp, "values": {"flow_lps": value}}
            for (point, timestamp), value in zip(
                shared_identities,
                [1.0, 3.0],
                strict=True,
            )
        ],
    )

    resolved = client.post(
        f"/api/projects/{project['id']}/derived-batches",
        json={
            "name": "人工选源合并",
            "source_batch_ids": [first["id"], second["id"]],
            "conflict_resolutions": [
                {
                    "point_id": "W1",
                    "timestamp": "2026-03-01T00:00:00",
                    "source_batch_id": first["id"],
                },
                {
                    "point_id": "W2",
                    "timestamp": "2026-03-02T00:00:00",
                    "source_batch_id": second["id"],
                },
            ],
        },
    )

    assert resolved.status_code == 201
    derived = resolved.json()
    assert client.get(
        f"/api/projects/{project['id']}/batches/{derived['id']}/sources"
    ).json() == [first, second]
    merged = StandardDataStore(tmp_path / "var" / "projects").load_flow(
        project["id"],
        derived["id"],
    )
    assert merged["point_id"].tolist() == ["W1", "W2"]
    assert merged["flow_lps"].tolist() == [1.0, 3.0]
    manifest = json.loads(
        (
            tmp_path
            / "var"
            / "projects"
            / project["id"]
            / "batches"
            / derived["id"]
            / "standard"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["conflict_resolutions"] == [
        {
            "point_id": "W1",
            "timestamp": "2026-03-01T00:00:00",
            "source_batch_id": first["id"],
        },
        {
            "point_id": "W2",
            "timestamp": "2026-03-02T00:00:00",
            "source_batch_id": second["id"],
        },
    ]


def test_index_exposes_derived_batch_creation_and_conflict_resolution(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert 'id="derivedBatchForm"' in response.text
    assert 'id="derivedBatchSources"' in response.text
    assert 'id="derivedConflictPanel"' in response.text
    assert "/derived-batches" in response.text
    assert "conflict_resolutions" in response.text
