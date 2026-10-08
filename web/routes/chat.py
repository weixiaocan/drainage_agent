"""Agent chat, cancellation, Python approval and run record routes."""

from __future__ import annotations

from copy import copy
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException

from agent.deps import AgentDeps, Paths
from agent.python_execution_service import execute_persisted_request
from web.chat_downloads import (
    requests_file_download,
    select_chat_artifacts,
    select_requested_downloads,
    workspace_file_paths,
)
from web.schemas import CancelRequest, ChatRequest, ChatResponse, PythonApprovalCommand
from web.uploads import message_with_attachments
from web.workspace import current_workspace_artifacts


def register(app: FastAPI) -> None:
    @app.post("/api/cancel")
    def cancel_run(request: CancelRequest) -> dict[str, str]:
        from agent.core import request_cancel
        request_cancel(request.session_id)
        return {"status": "cancelled"}

    def _approval_data(request: Any) -> dict[str, Any]:
        return {
            "request_id": request.request_id,
            "purpose": request.purpose,
            "code": request.code,
            "code_sha256": request.code_sha256,
            "reasons": list(request.policy_reasons),
            "capabilities": list(request.requested_capabilities),
            "affected_paths": list(request.affected_paths),
            "status": request.status,
            "expires_at": request.expires_at,
            "network": "none",
            "inputs": list(request.inputs),
            "outputs": list(request.outputs),
            "overwrite": request.overwrite,
            "artifacts": list(request.artifacts),
            "stdout": request.stdout,
            "stderr": request.stderr,
            "error": request.error,
        }

    def _python_execution_deps(request: Any) -> AgentDeps:
        scoped = copy(app.state.deps)
        batch_root = app.state.projects.batch_workspace(request.project_id, request.batch_id)
        scoped.paths = Paths(
            root=batch_root, data=batch_root / "inputs", outputs=batch_root / "exports",
            workspace=batch_root / "sessions", logs=app.state.deps.paths.logs,
            templates=batch_root / "inputs" / "templates",
        )
        scoped.current_project_id = request.project_id
        scoped.current_batch_id = request.batch_id
        scoped.cancel_session_id = request.session_id
        return scoped

    @app.get("/api/projects/{project_id}/batches/{batch_id}/python-executions/{request_id}")
    def get_python_execution(project_id: str, batch_id: str, request_id: str) -> dict[str, Any]:
        request = app.state.deps.python_execution_requests.get(request_id)
        if request is None or request.project_id != project_id or request.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="Python 执行请求不存在")
        return _approval_data(request)

    @app.post("/api/projects/{project_id}/batches/{batch_id}/python-executions/{request_id}/approve")
    def approve_python_execution(project_id: str, batch_id: str, request_id: str,
                                 command: PythonApprovalCommand) -> dict[str, Any]:
        pending = app.state.deps.python_execution_requests.get(request_id)
        if pending is None or pending.project_id != project_id or pending.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="Python 执行请求不存在")
        if app.state.deps.python_sandbox is None or app.state.deps.sandbox_jobs_root is None:
            raise HTTPException(status_code=503, detail="Python 沙箱服务未配置，请求尚未批准")
        try:
            request = app.state.deps.python_execution_requests.approve(
                request_id, project_id=project_id, batch_id=batch_id,
                session_id=command.session_id, code_sha256=command.code_sha256,
                approved_capabilities=command.approved_capabilities,
            )
            app.state.run_records.write({
                "event": "python_execution_approved", "run_id": request.run_id,
                "job_id": request.request_id, "status": request.status,
                "args": {"code_sha256": request.code_sha256,
                         "approved_capabilities": list(request.approved_capabilities)},
            })
            finished = execute_persisted_request(_python_execution_deps(request), request)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _approval_data(finished)

    @app.post("/api/projects/{project_id}/batches/{batch_id}/python-executions/{request_id}/reject")
    def reject_python_execution(project_id: str, batch_id: str, request_id: str,
                                command: PythonApprovalCommand) -> dict[str, Any]:
        current = app.state.deps.python_execution_requests.get(request_id)
        if current is None or current.project_id != project_id or current.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="Python 执行请求不存在")
        if current.session_id != command.session_id or current.code_sha256 != command.code_sha256:
            raise HTTPException(status_code=409, detail="审批上下文与请求不匹配")
        try:
            rejected = app.state.deps.python_execution_requests.reject(request_id)
            app.state.run_records.write({
                "event": "python_execution_rejected", "run_id": rejected.run_id,
                "job_id": rejected.request_id, "status": rejected.status,
            })
            return _approval_data(rejected)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        project_id = request.project_id
        batch_id = request.batch_id
        if not project_id or not batch_id:
            raise HTTPException(
                status_code=409,
                detail="请先选择当前监测项目和分析批次",
            )
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(
                status_code=404,
                detail="当前监测项目或分析批次不存在",
            )
        try:
            workspace_root = app.state.projects.batch_workspace(project_id, batch_id)
            agent_message = message_with_attachments(
                message,
                workspace_root,
                request.attachment_paths,
            )
            before_paths = {
                item["path"]
                for item in current_workspace_artifacts(app, project_id, batch_id)
            }
            before_files = workspace_file_paths(workspace_root)
            turn = app.state.conversations.run(
                project_id=project_id,
                batch_id=batch_id,
                message=agent_message,
                session_id=request.session_id,
                debug=request.debug,
                model_id=request.model_id,
            )
            artifacts = select_chat_artifacts(
                current_workspace_artifacts(app, project_id, batch_id),
                before_paths,
            )
            if not artifacts and requests_file_download(message):
                artifacts = select_requested_downloads(
                    workspace_root,
                    before_files,
                    turn.reply,
                )
            return ChatResponse(
                session_id=turn.session_id,
                run_id=turn.run_id,
                reply=turn.reply,
                artifacts=artifacts,
                python_approval=(
                    _approval_data(pending)
                    if (pending := app.state.deps.python_execution_requests.for_run(
                        turn.run_id, project_id, batch_id
                    )) is not None and pending.status == "awaiting_approval"
                    else None
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            app.state.deps.logger.exception("Web agent turn failed")
            raise HTTPException(
                status_code=500, detail=f"Agent 调用失败: {exc}"
            ) from exc

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/agent-runs"
    )
    def list_agent_runs(
        project_id: str, batch_id: str, limit: int = 100
    ) -> list[dict[str, object]]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        return [
            asdict(record)
            for record in app.state.run_records.list(
                project_id, batch_id, limit=limit
            )
        ]

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/agent-runs/{run_id}"
    )
    def get_agent_run(
        project_id: str, batch_id: str, run_id: str
    ) -> dict[str, object]:
        record = app.state.run_records.get(project_id, batch_id, run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Agent 运行记录不存在")
        return record
