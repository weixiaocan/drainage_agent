"""Workspace state shared by import, filter, file and chat routes."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI


def archive_chat_and_clear_analysis(
    app: FastAPI,
    project_id: str,
    batch_id: str,
) -> None:
    database = app.state.root / "var" / "drainage.sqlite3"
    archived_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS archived_agent_sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                history_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO archived_agent_sessions
            SELECT session_id, project_id, batch_id, history_json,
                   state_json, created_at, updated_at, ?
            FROM agent_sessions
            WHERE project_id = ? AND batch_id = ?
            """,
            (archived_at, project_id, batch_id),
        )
        for table in (
            "agent_sessions",
            "current_analysis_results",
            "analysis_runs",
            "background_jobs",
            "report_drafts",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE project_id = ? AND batch_id = ?",
                (project_id, batch_id),
            )
        run_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT run_id FROM agent_runs
                WHERE project_id = ? AND batch_id = ?
                """,
                (project_id, batch_id),
            )
        ]
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            connection.execute(
                f"DELETE FROM agent_run_steps WHERE run_id IN ({placeholders})",
                run_ids,
            )
        connection.execute(
            "DELETE FROM agent_runs WHERE project_id = ? AND batch_id = ?",
            (project_id, batch_id),
        )
    root = app.state.projects.batch_workspace(project_id, batch_id)
    for directory in ("results", "jobs", "sessions"):
        target = (root / directory).resolve()
        if target.is_relative_to(root.resolve()) and target.is_dir():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
    exports_dir = (root / "exports").resolve()
    if exports_dir.is_dir() and exports_dir.is_relative_to(root.resolve()):
        for path in sorted(exports_dir.rglob("*")):
            if path.is_file() and path.name != "筛选结果.xlsx":
                path.unlink()
        for subdir in sorted(exports_dir.rglob("*"), reverse=True):
            if subdir.is_dir() and not any(subdir.iterdir()):
                subdir.rmdir()

def invalidate_derived_state(
    app: FastAPI,
    project_id: str,
    batch_id: str,
) -> None:
    """Clear outputs derived from monitoring data, preserving auxiliary inputs."""
    archive_chat_and_clear_analysis(app, project_id, batch_id)
    database = app.state.root / "var" / "drainage.sqlite3"
    with sqlite3.connect(database) as connection:
        for table in (
            "current_analysis_baselines",
            "analysis_baselines",
            "filter_results",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE project_id = ? AND batch_id = ?",
                (project_id, batch_id),
            )
    root = app.state.projects.batch_workspace(project_id, batch_id)
    baseline_root = (root / "baseline").resolve()
    if (
        baseline_root.is_relative_to(root.resolve())
        and baseline_root.is_dir()
    ):
        shutil.rmtree(baseline_root)
    baseline_root.mkdir(parents=True, exist_ok=True)
    filters_root = (root / "exports" / "filters").resolve()
    if (
        filters_root.is_relative_to(root.resolve())
        and filters_root.is_dir()
    ):
        shutil.rmtree(filters_root)

def current_workspace_artifacts(
    app: FastAPI,
    project_id: str,
    workspace_id: str,
) -> list[dict[str, Any]]:
    root = app.state.projects.batch_workspace(project_id, workspace_id)
    files: list[dict[str, Any]] = []
    current_results = {
        algorithm: app.state.analysis_runner.current(
            project_id, workspace_id, algorithm
        )
        for algorithm in (
            "data_quality", "patterns", "rainfall",
            "event_response", "rdii", "risk",
        )
    }
    labels = {
        "data_quality": "数据质量结果",
        "patterns": "排污规律结果",
        "rainfall": "降雨分析结果",
        "event_response": "降雨响应结果",
        "rdii": "RDII 分析结果",
        "risk": "风险分析结果",
    }
    for algorithm, result in current_results.items():
        if result is None:
            continue
        for artifact in result.artifacts:
            path = root / artifact
            if path.is_file():
                files.append({
                    "path": artifact,
                    "name": labels[algorithm],
                    "size": path.stat().st_size,
                })
    baseline = app.state.filter_baselines.current_baseline(
        project_id, workspace_id
    )
    if baseline is not None:
        path = root / baseline.artifact
        if path.is_file():
            files.append({
                "path": baseline.artifact,
                "name": "当前筛选结果",
                "size": path.stat().st_size,
            })
    for draft in app.state.report_templates.list_drafts(
        project_id, workspace_id
    )[-1:]:
        for artifact, label in (
            (draft.docx, "当前报告初稿"),
            (draft.workbook, "当前综合结果表"),
        ):
            path = root / artifact
            if path.is_file():
                files.append({
                    "path": artifact,
                    "name": label,
                    "size": path.stat().st_size,
                })
    return files
