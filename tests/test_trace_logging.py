from __future__ import annotations

from agent.logging_utils import TraceLogger, summarize_tool_result, trace_event


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
