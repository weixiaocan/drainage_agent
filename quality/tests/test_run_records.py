from __future__ import annotations

from agent.run_records import RunRecorder


def test_run_recorder_persists_safe_tool_steps(tmp_path) -> None:
    recorder = RunRecorder(tmp_path / "runs.sqlite3")
    recorder.start(
        run_id="run-1",
        project_id="project-a",
        batch_id="batch-a",
        session_id="session-a",
        model="test-model",
    )
    recorder.write(
        {
            "event": "tool_call",
            "run_id": "run-1",
            "tool_name": "check_data",
            "args": {"points": ["W1"]},
        }
    )
    recorder.write(
        {
            "event": "tool_result",
            "run_id": "run-1",
            "tool_name": "check_data",
            "status": "ok",
            "artifacts": ["results/check.xlsx"],
        }
    )
    recorder.finish(
        "run-1",
        status="succeeded",
        reply="完成",
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    detail = recorder.get("project-a", "batch-a", "run-1")

    assert detail is not None
    assert detail["status"] == "succeeded"
    assert detail["total_tokens"] == 15
    assert [step["event"] for step in detail["steps"]] == [
        "tool_call",
        "tool_result",
    ]
    assert detail["steps"][0]["args"] == {"points": ["W1"]}


def test_run_recorder_never_returns_cross_batch_run(tmp_path) -> None:
    recorder = RunRecorder(tmp_path / "runs.sqlite3")
    recorder.start(
        run_id="run-1",
        project_id="project-a",
        batch_id="batch-a",
        session_id="session-a",
        model="test-model",
    )
    recorder.finish("run-1", status="succeeded")

    assert recorder.get("project-a", "batch-b", "run-1") is None
