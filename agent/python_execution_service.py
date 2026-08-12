from __future__ import annotations

import shutil

from agent.deps import AgentDeps
from agent.python_artifacts import create_input_snapshot, validate_and_receive_artifacts
from agent.python_execution_requests import PythonExecutionRequest
from agent.python_sandbox import SandboxRequest
from agent.core.logging_utils import trace_event


def execute_persisted_request(deps: AgentDeps, request: PythonExecutionRequest) -> PythonExecutionRequest:
    repository = deps.python_execution_requests
    if repository is None or deps.python_sandbox is None or deps.sandbox_jobs_root is None:
        raise RuntimeError("Python sandbox execution services are not fully configured")
    if request.status not in {"approved", "approved_automatically"}:
        raise ValueError("Python execution request is not approved")
    if repository.hash_code(request.code) != request.code_sha256:
        raise ValueError("persisted Python code hash mismatch")
    if request.overwrite and "overwrite_outputs" not in request.approved_capabilities:
        raise ValueError("overwrite capability was not approved")
    snapshot = None
    try:
        trace_event(deps.trace, {
            "event": "python_execution_start", "run_id": request.run_id,
            "job_id": request.request_id, "code_sha256": request.code_sha256,
            "policy_decision": request.policy_decision,
            "capabilities": list(request.approved_capabilities),
        })
        snapshot = create_input_snapshot(
            deps.paths.root, deps.sandbox_jobs_root,
            project_id=request.project_id, batch_id=request.batch_id,
            resources=request.inputs, snapshot_id=request.request_id,
        )
        (snapshot.job_root / "code" / "main.py").write_text(
            "from prelude import *\n" + request.code, encoding="utf-8"
        )
        repository.start(
            request.request_id, project_id=request.project_id, batch_id=request.batch_id,
            session_id=request.session_id, code_sha256=request.code_sha256,
            input_snapshot_id=snapshot.snapshot_id,
            sandbox_image_digest=deps.python_sandbox.image_digest,
        )
        result = deps.python_sandbox.execute(
            SandboxRequest(request.request_id, request.code, snapshot.snapshot_id)
        )
        terminal = "succeeded" if result.ok else (
            "timed_out" if result.status == "timed_out" else "failed"
        )
        artifacts = ()
        if result.ok:
            artifacts = validate_and_receive_artifacts(
                snapshot.job_root / "output", deps.paths.outputs, overwrite=request.overwrite,
            )
        finished = repository.finish(
            request.request_id, status=terminal, stdout=result.stdout, stderr=result.stderr,
            exit_code=result.exit_code, error=result.error,
            artifacts=[item.__dict__ for item in artifacts],
        )
        trace_event(deps.trace, {
            "event": "python_execution_finish", "run_id": request.run_id,
            "job_id": request.request_id, "status": finished.status,
            "exit_code": finished.exit_code,
            "sandbox_image_digest": finished.sandbox_image_digest,
            "input_snapshot_id": finished.input_snapshot_id,
            "artifacts": list(finished.artifacts),
        })
        return finished
    except Exception as exc:
        current = repository.required(request.request_id)
        if current.status == "running":
            repository.finish(request.request_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if snapshot is not None:
            shutil.rmtree(snapshot.job_root, ignore_errors=True)
