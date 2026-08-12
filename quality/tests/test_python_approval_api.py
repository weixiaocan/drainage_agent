from fastapi.testclient import TestClient

from agent.python_execution_requests import PythonExecutionRequestRepository
from agent.python_sandbox import FakePythonSandbox, SandboxResult
from web.app import create_app


def make_client(tmp_path):
    app = create_app(root=tmp_path)
    return app, TestClient(app)


def pending(app):
    repository: PythonExecutionRequestRepository = app.state.deps.python_execution_requests
    return repository.create(
        project_id="p", batch_id="b", session_id="s", run_id="r",
        purpose="覆盖统计", code="print(1)", policy_decision="ask",
        policy_reasons=["overwrite_requires_approval"],
        requested_capabilities=["overwrite_outputs"], affected_paths=["out.csv"],
        inputs=[], outputs=[], overwrite=True,
    )


def command(request, **overrides):
    value = {"session_id": "s", "code_sha256": request.code_sha256,
             "approved_capabilities": ["overwrite_outputs"]}
    value.update(overrides)
    return value


def test_approval_api_returns_full_review_data_and_approves_once(tmp_path) -> None:
    app, client = make_client(tmp_path)
    app.state.deps.python_sandbox = FakePythonSandbox(
        SandboxResult(status="succeeded", exit_code=0)
    )
    request = pending(app)
    path = f"/api/projects/p/batches/b/python-executions/{request.request_id}"
    review = client.get(path)
    assert review.status_code == 200
    assert review.json()["code"] == "print(1)"
    assert review.json()["network"] == "none"
    approved = client.post(path + "/approve", json=command(request))
    assert approved.status_code == 200
    assert approved.json()["status"] == "succeeded"
    assert len(app.state.deps.python_sandbox.requests) == 1
    assert client.post(path + "/approve", json=command(request)).status_code == 409


def test_approval_cannot_cross_scope_or_change_hash_or_capabilities(tmp_path) -> None:
    app, client = make_client(tmp_path)
    app.state.deps.python_sandbox = FakePythonSandbox()
    request = pending(app)
    base = f"/api/projects/p/batches/b/python-executions/{request.request_id}"
    assert client.get(base.replace("/b/", "/other/")).status_code == 404
    assert client.post(base + "/approve", json=command(request, code_sha256="changed")).status_code == 409
    assert client.post(base + "/approve", json=command(request,
        approved_capabilities=["network"])).status_code == 409


def test_rejection_is_bound_and_terminal(tmp_path) -> None:
    app, client = make_client(tmp_path)
    app.state.deps.python_sandbox = FakePythonSandbox()
    request = pending(app)
    base = f"/api/projects/p/batches/b/python-executions/{request.request_id}"
    assert client.post(base + "/reject", json=command(request, session_id="other")).status_code == 409
    rejected = client.post(base + "/reject", json=command(request))
    assert rejected.json()["status"] == "rejected"
    assert client.post(base + "/approve", json=command(request)).status_code == 409


def test_unconfigured_sandbox_does_not_consume_approval(tmp_path) -> None:
    app, client = make_client(tmp_path)
    request = pending(app)
    base = f"/api/projects/p/batches/b/python-executions/{request.request_id}"
    response = client.post(base + "/approve", json=command(request))
    assert response.status_code == 503
    assert app.state.deps.python_execution_requests.required(request.request_id).status == "awaiting_approval"
