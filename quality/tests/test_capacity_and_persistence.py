from __future__ import annotations

import json

from scripts.generate_synthetic_capacity_data import generate
from web.projects import ProjectRepository


def test_synthetic_generator_is_reproducible_and_has_expected_shape(
    tmp_path,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"

    assert generate(first, points=2, days=1) == 2880
    assert generate(second, points=2, days=1) == 2880
    assert first.read_bytes() == second.read_bytes()
    assert first.read_text(encoding="utf-8").splitlines()[1].split(",")[2] == (
        "SYN001"
    )


def test_sqlite_and_batch_artifacts_survive_repository_recreation(
    tmp_path,
) -> None:
    database = tmp_path / "var" / "drainage.sqlite3"
    files = tmp_path / "var" / "projects"
    first = ProjectRepository(database, files)
    project = first.create("持久化")
    batch = first.create_batch(project.id, "重建验证")
    artifact = first.batch_workspace(project.id, batch.id) / "results/result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

    reopened = ProjectRepository(database, files)

    assert reopened.get(project.id) == project
    assert reopened.get_batch(project.id, batch.id) == batch
    assert reopened.resolve_batch_file(
        project.id, batch.id, "results/result.json"
    ).read_text(encoding="utf-8") == '{"status": "ok"}'


def test_compose_mounts_complete_var_directory_as_named_volume() -> None:
    compose = open("docker-compose.yml", encoding="utf-8").read()

    assert "drainage-state:/app/var" in compose
    assert "drainage-state:" in compose
