from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from eval.eval_stage2.run_eval import completed_case_ids, fresh_root, normalize_case, preserve_artifacts, tool_seq
from eval.eval_stage2.view import load_results, render_report


def test_fresh_root_copies_prompt_without_copying_env(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    (project / "templates").mkdir()
    (project / "agent" / "prompts").mkdir(parents=True)
    (project / "agent" / "prompts" / "system.md").write_text("system prompt", encoding="utf-8")
    (project / ".env").write_text("SECRET=value", encoding="utf-8")
    root = tmp_path / "isolated"
    root.mkdir()
    monkeypatch.setattr("eval.eval_stage2.run_eval.PROJECT", project)

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
    stale = project / "eval" / "eval_stage2" / "artifacts" / "E001"
    stale.mkdir(parents=True)
    (stale / "stale.txt").write_text("stale", encoding="utf-8")
    monkeypatch.setattr("eval.eval_stage2.run_eval.STAGE_DIR", project / "eval" / "eval_stage2")

    destination = preserve_artifacts(root, "E001")

    assert not (destination / "stale.txt").exists()
    assert (destination / "logs" / "trace.jsonl").read_text(encoding="utf-8") == "trace"


def test_normalize_multiturn_case_preserves_key_turns() -> None:
    case = normalize_case({
        "id": "M001",
        "category": "指代",
        "turns": [
            {"prompt": "先看 W1", "expect": "调用工具"},
            {"prompt": "W6 呢", "expect": "继承上下文", "key": True},
        ],
    })

    assert [turn["key"] for turn in case["turns"]] == [False, True]
    assert case["turns"][1]["expect"] == "继承上下文"


def test_multiturn_view_skips_meta_and_renders_turns(tmp_path: Path) -> None:
    source = tmp_path / "results.jsonl"
    destination = tmp_path / "report.html"
    source.write_text(
        "\n".join([
            '{"_meta":{"model":"test-model","case_count":1}}',
            '{"id":"M001","category":"指代","turns":['
            '{"n":1,"run_id":"run-1","prompt":"先看 W1","expect":"调用工具","key":true,'
            '"output":"完成","tool_calls":[{"tool":"check_data","args":"{\\"points\\":[\\"W1\\"]}"}]}],'
            '"trace":"trace.jsonl","error":null}',
        ]),
        encoding="utf-8",
    )

    meta, rows = load_results(source)
    count = render_report(source, destination)
    html = destination.read_text(encoding="utf-8")

    assert meta["model"] == "test-model"
    assert rows[0]["turns"][0]["tool_calls"][0]["args"] == {"points": ["W1"]}
    assert count == 1
    assert "逐轮人工判定" not in html
    assert "M001" in html and "先看 W1" in html and "调用工具" in html


def test_completed_case_ids_ignores_meta_and_partial_line(tmp_path: Path) -> None:
    pending = tmp_path / "results.jsonl.tmp"
    pending.write_text(
        '{"_meta":{"case_count":2}}\n{"id":"M001","turns":[]}\n{"id":',
        encoding="utf-8",
    )

    assert completed_case_ids(pending) == {"M001"}
