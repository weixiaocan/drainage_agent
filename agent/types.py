from __future__ import annotations

from typing import Any, Literal, TypedDict


ToolStatus = Literal["ok", "needs_input", "needs_confirmation", "error"]


class ToolResult(TypedDict, total=False):
    status: ToolStatus
    summary: str
    artifacts: list[str]
    missing: str
    options: list[dict[str, Any]]
    hint: str
    data: dict[str, Any]


class FilterConfirmationRequired(Exception):
    def __init__(self, result: ToolResult, tool_name: str = "data_filter", args: dict[str, Any] | None = None):
        self.result = result
        self.tool_name = tool_name
        self.args = args or {}
        super().__init__(result.get("summary", "filter confirmation required"))


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


def needs_confirmation(
    missing: str,
    hint: str,
    summary: str,
    artifacts: list[str] | None = None,
    options: list[dict[str, Any]] | None = None,
    **data: Any,
) -> ToolResult:
    result: ToolResult = {
        "status": "needs_confirmation",
        "summary": summary,
        "artifacts": artifacts or [],
        "missing": missing,
        "options": options or [],
        "hint": hint,
    }
    if data:
        result["data"] = data
    return result


def error(summary: str, artifacts: list[str] | None = None, **data: Any) -> ToolResult:
    result: ToolResult = {
        "status": "error",
        "summary": summary,
        "artifacts": artifacts or [],
    }
    if data:
        result["data"] = data
    return result
