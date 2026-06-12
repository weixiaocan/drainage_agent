from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from agent.deps import AgentDeps
from agent.tools.manifest import data_fingerprint, load_manifest
from agent.types import ToolResult, ok


def _excel_sheets(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        with pd.ExcelFile(path) as xls:
            return list(xls.sheet_names)
    except Exception:
        return []


def list_results_impl(deps: AgentDeps) -> ToolResult:
    manifest = load_manifest(deps)
    current_fp = data_fingerprint(deps)["digest"]
    artifacts: list[str] = []
    results: dict[str, Any] = {}

    for path in sorted(deps.paths.outputs.rglob("*")):
        if path.is_file():
            rel = path.relative_to(deps.paths.root).as_posix()
            artifacts.append(rel)
            item: dict[str, Any] = {"mtime": path.stat().st_mtime, "size": path.stat().st_size}
            if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
                item["sheets"] = _excel_sheets(path)
            results[rel] = item

    manifest_results = {}
    for name, item in manifest.get("results", {}).items():
        manifest_results[name] = {
            **item,
            "fresh": item.get("data_fingerprint") == current_fp,
        }

    stale = [name for name, item in manifest_results.items() if not item.get("fresh")]
    summary = f"outputs 中发现 {len(artifacts)} 个文件。"
    if stale:
        summary += " 过期结果: " + ", ".join(stale)
    elif manifest_results:
        summary += " manifest 中的结果均为 fresh。"
    return ok(summary, artifacts=artifacts, results=results, manifest=manifest_results)
