from __future__ import annotations

from typing import Any, Literal, TypedDict


ToolStatus = Literal["ok", "blocked", "error"]


class ToolResult(TypedDict, total=False):
    status: ToolStatus
    summary: str
    artifacts: list[str]
    missing: str
    hint: str
    data: dict[str, Any]


def ok(summary: str, artifacts: list[str] | None = None, **data: Any) -> ToolResult:
    result: ToolResult = {
        "status": "ok",
        "summary": summary,
        "artifacts": artifacts or [],
    }
    if data:
        result["data"] = data
    return result


def blocked(missing: str, hint: str, summary: str | None = None) -> ToolResult:
    return {
        "status": "blocked",
        "summary": summary or f"缺少前置结果: {missing}",
        "artifacts": [],
        "missing": missing,
        "hint": hint,
    }


def error(summary: str, artifacts: list[str] | None = None, **data: Any) -> ToolResult:
    result: ToolResult = {
        "status": "error",
        "summary": summary,
        "artifacts": artifacts or [],
    }
    if data:
        result["data"] = data
    return result

