from __future__ import annotations

import uuid

from agent.deps import AgentDeps
from agent.python_execution_policy import PythonExecutionPolicy
from agent.python_execution_service import execute_persisted_request
from agent.types import ToolResult


def run_python_impl(
    deps: AgentDeps,
    purpose: str,
    code: str,
    inputs: list[str],
    outputs: list[str],
    overwrite: bool = False,
) -> ToolResult:
    if ".md" in code.lower() and "报告" in code:
        return _result("denied", "报告必须通过 generate_report 生成 DOCX；run_python 禁止生成 Markdown 报告。")
    decision = PythonExecutionPolicy().evaluate(
        code=code, inputs=inputs, outputs=outputs, overwrite=overwrite,
    )
    repository = deps.python_execution_requests
    project_id = deps.current_project_id or ""
    batch_id = deps.current_batch_id or ""
    session_id = deps.cancel_session_id
    run_id = deps.session.current_run_id or uuid.uuid4().hex
    if repository is None or not all((project_id, batch_id, session_id)):
        return _result("failed", "Python 安全执行服务未配置，已拒绝在主应用进程中执行。")
    request = repository.create(
        project_id=project_id, batch_id=batch_id, session_id=session_id, run_id=run_id,
        purpose=purpose, code=code, policy_decision=decision.action,
        inputs=inputs, outputs=outputs, overwrite=overwrite,
        policy_reasons=decision.reasons,
        requested_capabilities=decision.capabilities,
        affected_paths=decision.affected_paths,
    )
    common = {
        "request_id": request.request_id,
        "code_sha256": request.code_sha256,
        "policy_reasons": list(decision.reasons),
        "capabilities": list(decision.capabilities),
        "affected_paths": list(decision.affected_paths),
        "network": "none",
    }
    if decision.action == "deny":
        return _result("denied", "Python 执行策略拒绝了危险操作。", **common)
    if decision.action == "ask":
        return _result("needs_approval", "Python 执行需要用户单次批准。", **common)
    if deps.python_sandbox is None:
        return _result("failed", "Python 沙箱未配置，已拒绝回退到主进程执行。", **common)

    try:
        finished = execute_persisted_request(deps, request)
        if finished.status != "succeeded":
            return _result("failed", "Python 沙箱执行失败。", **common,
                           stdout=finished.stdout, stderr=finished.stderr)
        relative = [f"exports/{item['name']}" for item in finished.artifacts]
        return _result("ok", "run_python 在隔离沙箱中执行成功。", artifacts=relative,
                       **common, stdout=finished.stdout)
    except Exception as exc:
        current = repository.required(request.request_id)
        if current.status == "running":
            repository.finish(request.request_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        return _result("failed", f"Python 安全执行失败: {type(exc).__name__}: {exc}", **common)


def _result(status: str, summary: str, artifacts: list[str] | None = None, **data) -> ToolResult:
    result: ToolResult = {"status": status, "summary": summary, "artifacts": artifacts or []}  # type: ignore[typeddict-item]
    if data:
        result["data"] = data
    return result
