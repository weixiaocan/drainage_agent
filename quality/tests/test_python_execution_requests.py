from datetime import datetime, timedelta, timezone

import pytest

from agent.python_execution_requests import InvalidExecutionTransition, PythonExecutionRequestRepository


def binding(code: str = "print(1)") -> dict[str, str]:
    return {"project_id": "p", "batch_id": "b", "session_id": "s",
            "code_sha256": PythonExecutionRequestRepository.hash_code(code)}


def create(repository, decision="ask", **kwargs):
    return repository.create(project_id="p", batch_id="b", session_id="s", run_id="r",
                             purpose="统计", code="print(1)", policy_decision=decision, **kwargs)


def test_policy_decision_selects_initial_state(tmp_path) -> None:
    repository = PythonExecutionRequestRepository(tmp_path / "db.sqlite3")
    assert [create(repository, item).status for item in ("allow", "ask", "deny")] == [
        "approved_automatically", "awaiting_approval", "denied"]


def test_approval_is_bound_and_cannot_expand_capabilities(tmp_path) -> None:
    repository = PythonExecutionRequestRepository(tmp_path / "db.sqlite3")
    request = create(repository, requested_capabilities=["overwrite:result.csv"])
    with pytest.raises(InvalidExecutionTransition):
        repository.approve(request.request_id, **{**binding(), "session_id": "other"},
                           approved_capabilities=[])
    with pytest.raises(ValueError):
        repository.approve(request.request_id, **binding(), approved_capabilities=["network"])
    assert repository.approve(request.request_id, **binding(),
                              approved_capabilities=["overwrite:result.csv"]).status == "approved"


def test_approval_is_single_use(tmp_path) -> None:
    repository = PythonExecutionRequestRepository(tmp_path / "db.sqlite3")
    request = create(repository)
    repository.approve(request.request_id, **binding(), approved_capabilities=[])
    assert repository.start(request.request_id, **binding(), input_snapshot_id="snap",
                            sandbox_image_digest="sha256:image").status == "running"
    with pytest.raises(InvalidExecutionTransition):
        repository.start(request.request_id, **binding(), input_snapshot_id="snap2",
                         sandbox_image_digest="sha256:image")
    assert repository.finish(request.request_id, status="succeeded", exit_code=0).status == "succeeded"


def test_expired_rejected_and_denied_requests_cannot_start(tmp_path) -> None:
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    repository = PythonExecutionRequestRepository(tmp_path / "db.sqlite3", clock=lambda: now)
    expired = create(repository, approval_ttl=timedelta(seconds=-1))
    with pytest.raises(InvalidExecutionTransition, match="expired"):
        repository.approve(expired.request_id, **binding(), approved_capabilities=[])
    assert repository.required(expired.request_id).status == "expired"
    rejected = create(repository)
    repository.reject(rejected.request_id)
    denied = create(repository, "deny")
    for request in (rejected, denied):
        with pytest.raises(InvalidExecutionTransition):
            repository.start(request.request_id, **binding(), input_snapshot_id="snap",
                             sandbox_image_digest="sha256:image")


def test_code_change_invalidates_approval(tmp_path) -> None:
    repository = PythonExecutionRequestRepository(tmp_path / "db.sqlite3")
    request = create(repository)
    with pytest.raises(InvalidExecutionTransition):
        repository.approve(request.request_id, **binding("print(2)"), approved_capabilities=[])
