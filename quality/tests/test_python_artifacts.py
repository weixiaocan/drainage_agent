import json
import os

import pandas as pd
import pytest
from openpyxl import Workbook
from PIL import Image

from agent.python_artifacts import UnsafeArtifact, create_input_snapshot, validate_and_receive_artifacts


def test_snapshot_copies_only_requested_authorized_resources(tmp_path) -> None:
    batch = tmp_path / "batch"
    standard = batch / "standard"
    standard.mkdir(parents=True)
    (standard / "flow.csv").write_text("timestamp,flow\n2026-01-01,1\n", encoding="utf-8")
    (standard / "rainfall.csv").write_text("secret", encoding="utf-8")
    snapshot = create_input_snapshot(batch, tmp_path / "jobs", project_id="p", batch_id="b",
                                     resources=["confirmed_flow"], snapshot_id="job-1")
    assert [item.resource for item in snapshot.files] == ["confirmed_flow"]
    assert (snapshot.job_root / "input" / "flow.csv").is_file()
    assert not (snapshot.job_root / "input" / "rainfall.csv").exists()
    manifest = json.loads((snapshot.job_root / "input" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == "p"
    assert len(manifest["files"][0]["sha256"]) == 64


def test_snapshot_rejects_unknown_resource_and_symlink(tmp_path) -> None:
    batch = tmp_path / "batch"
    (batch / "standard").mkdir(parents=True)
    with pytest.raises(ValueError, match="unsupported"):
        create_input_snapshot(batch, tmp_path / "jobs", project_id="p", batch_id="b",
                              resources=["host_path"])


def test_valid_artifacts_are_parsed_hashed_and_received(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    pd.DataFrame({"x": [1]}).to_csv(output / "table.csv", index=False)
    (output / "data.json").write_text('{"ok": true}', encoding="utf-8")
    Image.new("RGB", (1, 1)).save(output / "chart.png")
    pd.DataFrame({"x": [1]}).to_excel(output / "book.xlsx", index=False)
    artifacts = validate_and_receive_artifacts(output, tmp_path / "exports", overwrite=False)
    assert {item.name for item in artifacts} == {"table.csv", "data.json", "chart.png", "book.xlsx"}
    assert all(len(item.sha256) == 64 for item in artifacts)


@pytest.mark.parametrize("name,content", [
    ("bad.json", "not json"), ("fake.png", "not png"), ("script.py", "print(1)"),
])
def test_malformed_or_unapproved_artifact_is_rejected(tmp_path, name, content) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / name).write_text(content, encoding="utf-8")
    with pytest.raises(UnsafeArtifact):
        validate_and_receive_artifacts(output, tmp_path / "exports", overwrite=False)


def test_formula_injection_and_overwrite_are_rejected(tmp_path) -> None:
    output = tmp_path / "output"
    exports = tmp_path / "exports"
    output.mkdir()
    exports.mkdir()
    (output / "bad.csv").write_text("x\n=CMD()\n", encoding="utf-8")
    with pytest.raises(UnsafeArtifact, match="formula"):
        validate_and_receive_artifacts(output, exports, overwrite=False)
    (output / "bad.csv").write_text("x\n1\n", encoding="utf-8")
    (exports / "bad.csv").write_text("old", encoding="utf-8")
    with pytest.raises(UnsafeArtifact, match="overwrite"):
        validate_and_receive_artifacts(output, exports, overwrite=False)


def test_excel_formula_and_file_count_limit_are_rejected(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    workbook = Workbook()
    workbook.active["A1"] = "=1+1"
    workbook.save(output / "formula.xlsx")
    with pytest.raises(UnsafeArtifact, match="formulas"):
        validate_and_receive_artifacts(output, tmp_path / "exports", overwrite=False)
    (output / "formula.xlsx").unlink()
    for index in range(2):
        (output / f"{index}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(UnsafeArtifact, match="count"):
        validate_and_receive_artifacts(output, tmp_path / "exports", overwrite=False, max_files=1)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs extra privileges")
def test_symlink_artifact_is_rejected(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "secret"
    target.write_text("secret", encoding="utf-8")
    (output / "leak.csv").symlink_to(target)
    with pytest.raises(UnsafeArtifact, match="non-link"):
        validate_and_receive_artifacts(output, tmp_path / "exports", overwrite=False)
