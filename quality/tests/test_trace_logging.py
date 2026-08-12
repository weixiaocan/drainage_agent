from __future__ import annotations

from agent.core.logging_utils import TraceLogger, summarize_tool_result, trace_event


def test_trace_logger_writes_minimal_tool_events(tmp_path) -> None:
    trace = TraceLogger(tmp_path)
    run_id = "run-1"
    trace_event(trace, {"event": "turn_start", "run_id": run_id, "user": "describe data"})
    trace_event(trace, {"event": "tool_call", "run_id": run_id, "tool_name": "check_data", "args": {"points": None}})
    trace_event(
        trace,
        {
            "event": "tool_result",
            "run_id": run_id,
            "tool_name": "check_data",
            **summarize_tool_result(
                {
                    "status": "ok",
                    "summary": "19 points",
                    "artifacts": ["outputs/check.xlsx"],
                    "data": {"large": list(range(100))},
                }
            ),
        },
    )
    trace_event(trace, {"event": "turn_end", "run_id": run_id, "reply": "done"})

    lines = trace.path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 4
    assert '"event": "tool_call"' in lines[1]
    assert '"tool_name": "check_data"' in lines[1]
    assert '"status": "ok"' in lines[2]
    assert '"summary": "19 points"' in lines[2]
    assert '"large"' not in lines[2]


def test_trace_preserves_simple_list_args_and_limits_artifacts(tmp_path) -> None:
    trace = TraceLogger(tmp_path)
    artifacts = [f"outputs/artifact-{index}.txt" for index in range(12)]

    trace_event(
        trace,
        {
            "event": "tool_call",
            "run_id": "run-2",
            "tool_name": "analyze_event_response",
            "args": {"points": ["W1"], "event_ids": [4, 6]},
        },
    )
    trace_event(
        trace,
        {
            "event": "tool_result",
            "run_id": "run-2",
            "tool_name": "analyze_event_response",
            **summarize_tool_result({"status": "ok", "summary": "done", "artifacts": artifacts}),
        },
    )

    lines = trace.path.read_text(encoding="utf-8").splitlines()

    assert '"points": ["W1"]' in lines[0]
    assert '"event_ids": [4, 6]' in lines[0]
    assert '"artifact_count": 12' in lines[1]
    assert '"artifacts_truncated": 2' in lines[1]
    assert "artifact-9.txt" in lines[1]
    assert "artifact-10.txt" not in lines[1]


def test_trace_includes_run_python_stderr_on_error() -> None:
    summary = summarize_tool_result(
        {
            "status": "error",
            "summary": "run_python failed",
            "artifacts": [],
            "data": {
                "returncode": 1,
                "stderr": "Traceback: FileNotFoundError: outputs/result.xlsx",
                "script": "workspace/agent_run.py",
                "stdout": "not needed in trace",
            },
        }
    )

    assert summary["returncode"] == 1
    assert "FileNotFoundError" in summary["stderr"]
    assert summary["script"] == "workspace/agent_run.py"
    assert "stdout" not in summary


def test_trace_redacts_sensitive_keys_recursively(tmp_path) -> None:
    trace = TraceLogger(tmp_path)

    trace_event(
        trace,
        {
            "event": "tool_call",
            "run_id": "run-secret",
            "args": {
                "api_key": "must-not-leak",
                "nested": {"authorization": "Bearer secret"},
            },
        },
    )

    content = trace.path.read_text(encoding="utf-8")
    assert "must-not-leak" not in content
    assert "Bearer secret" not in content
    assert content.count("<redacted>") == 2


def test_trace_redacts_python_code_but_keeps_security_metadata(tmp_path) -> None:
    trace = TraceLogger(tmp_path)
    trace_event(trace, {
        "event": "python_execution_start", "run_id": "run-1",
        "code": "print('secret input')", "code_sha256": "abc",
        "sandbox_image_digest": "sha256:image", "input_snapshot_id": "snap-1",
    })
    content = trace.path.read_text(encoding="utf-8")
    assert "secret input" not in content
    assert "<redacted>" in content
    assert "sha256:image" in content
    assert "snap-1" in content
