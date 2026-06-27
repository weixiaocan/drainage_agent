from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_TRACE_STRING = 2000
MAX_TRACE_LIST_ITEMS = 20
MAX_TRACE_DICT_ITEMS = 50
MAX_TRACE_ARTIFACTS = 10


def setup_logging(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"agent-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    return log_file


class TraceLogger:
    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / f"trace-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

    def write(self, event: dict[str, Any]) -> None:
        event = {"ts": datetime.now().isoformat(timespec="seconds"), **event}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _trace_safe(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_TRACE_STRING else value[:MAX_TRACE_STRING] + "...<truncated>"
    if isinstance(value, Path):
        return str(value)
    if depth >= 3:
        return f"<{type(value).__name__}>"
    if isinstance(value, (list, tuple)):
        items = [_trace_safe(item, depth=depth + 1) for item in value[:MAX_TRACE_LIST_ITEMS]]
        if len(value) > MAX_TRACE_LIST_ITEMS:
            items.append(f"...<{len(value) - MAX_TRACE_LIST_ITEMS} more>")
        return items
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): _trace_safe(item, depth=depth + 1)
            for key, item in items[:MAX_TRACE_DICT_ITEMS]
            if key != "data"
        }
        if len(items) > MAX_TRACE_DICT_ITEMS:
            result["..."] = f"<{len(items) - MAX_TRACE_DICT_ITEMS} more>"
        return result
    return str(value)


def _summarize_artifacts(artifacts: Any) -> dict[str, Any]:
    if not isinstance(artifacts, list):
        return {"artifacts": _trace_safe(artifacts), "artifact_count": 0}
    visible = [_trace_safe(item) for item in artifacts[:MAX_TRACE_ARTIFACTS]]
    summary: dict[str, Any] = {
        "artifacts": visible,
        "artifact_count": len(artifacts),
    }
    if len(artifacts) > MAX_TRACE_ARTIFACTS:
        summary["artifacts_truncated"] = len(artifacts) - MAX_TRACE_ARTIFACTS
    return summary


def summarize_tool_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__, "summary": _trace_safe(result)}
    summary: dict[str, Any] = {
        "status": result.get("status"),
        "summary": result.get("summary"),
        **_summarize_artifacts(result.get("artifacts", [])),
    }
    for key in ("missing", "hint", "options"):
        if key in result:
            summary[key] = result[key]
    data = result.get("data")
    if result.get("status") == "error" and isinstance(data, dict):
        for key in ("returncode", "stderr", "script"):
            if key in data:
                summary[key] = data[key]
    return _trace_safe(summary)


def trace_event(trace: Any | None, event: dict[str, Any]) -> None:
    if trace is None:
        return
    trace.write(_trace_safe(event))
