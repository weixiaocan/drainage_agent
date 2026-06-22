from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from eval.run_eval import fresh_root, preserve_artifacts, tool_seq


def test_fresh_root_copies_prompt_without_copying_env(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    (project / "templates").mkdir()
    (project / "agent" / "prompts").mkdir(parents=True)
    (project / "agent" / "prompts" / "system.md").write_text("system prompt", encoding="utf-8")
    (project / ".env").write_text("SECRET=value", encoding="utf-8")
    root = tmp_path / "isolated"
    root.mkdir()
    monkeypatch.setattr("eval.run_eval.PROJECT", project)

    fresh_root(root)

    assert (root / "agent" / "prompts" / "system.md").read_text(encoding="utf-8") == "system prompt"
    assert not (root / ".env").exists()
    assert all((root / name).is_dir() for name in ("outputs", "workspace", "logs"))


def test_tool_seq_reads_only_supplied_messages() -> None:
    call = SimpleNamespace(part_kind="tool-call", tool_name="check_data", args={"points": ["W1"]})
    text = SimpleNamespace(part_kind="text", content="done")
    messages = [SimpleNamespace(parts=[call, text])]

    assert tool_seq(messages) == [{"tool": "check_data", "args": {"points": ["W1"]}}]


def test_preserve_artifacts_replaces_stale_case_directory(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    root = tmp_path / "isolated"
    for name in ("outputs", "workspace", "logs"):
        (root / name).mkdir(parents=True)
    (root / "logs" / "trace.jsonl").write_text("trace", encoding="utf-8")
    stale = project / "eval" / "artifacts" / "E001"
    stale.mkdir(parents=True)
    (stale / "stale.txt").write_text("stale", encoding="utf-8")
    monkeypatch.setattr("eval.run_eval.PROJECT", project)

    destination = preserve_artifacts(root, "E001")

    assert not (destination / "stale.txt").exists()
    assert (destination / "logs" / "trace.jsonl").read_text(encoding="utf-8") == "trace"
