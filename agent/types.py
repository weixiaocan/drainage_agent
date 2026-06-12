from __future__ import annotations

from typing import Any, Literal, TypedDict


ToolStatus = Literal["ok", "needs_input", "error"]


class ToolResult(TypedDict, total=False):
    status: ToolStatus
    summary: str
    artifacts: list[str]
    missing: str
    options: list[dict[str, Any]]
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


def needs_input(
    missing: str,
    hint: str,
    summary: str | None = None,
    options: list[dict[str, Any]] | None = None,
) -> ToolResult:
    return {
        "status": "needs_input",
        "summary": summary or f"需要用户选择: {missing}",
        "artifacts": [],
        "missing": missing,
        "options": options or [],
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
