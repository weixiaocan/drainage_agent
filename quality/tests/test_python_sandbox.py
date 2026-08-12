import pytest

from agent.python_sandbox import (
    FakePythonSandbox,
    PythonSandbox,
    SandboxArtifact,
    SandboxLimits,
    SandboxRequest,
    SandboxResult,
)


def request() -> SandboxRequest:
    return SandboxRequest(job_id="job-123", code="print(1)", input_snapshot_id="snapshot-1")


def test_fake_sandbox_satisfies_protocol_and_records_requests() -> None:
    sandbox: PythonSandbox = FakePythonSandbox(
        SandboxResult(
            status="succeeded",
            exit_code=0,
            stdout="1\n",
            duration_ms=12,
            artifacts=(SandboxArtifact("summary.csv", 42),),
        )
    )
    result = sandbox.execute(request())
    assert result.ok
    assert result.artifacts == (SandboxArtifact("summary.csv", 42),)
    assert isinstance(sandbox, FakePythonSandbox)
    assert sandbox.requests == [request()]


def test_fake_sandbox_responder_can_model_failure_without_executing_code() -> None:
    sandbox = FakePythonSandbox(
        responder=lambda item: SandboxResult(
            status="timed_out", duration_ms=item.limits.timeout_seconds * 1000,
            error="deadline exceeded",
        )
    )
    result = sandbox.execute(request())
    assert result.status == "timed_out"
    assert not result.ok
    assert result.exit_code is None


@pytest.mark.parametrize("field", [
    "timeout_seconds", "memory_megabytes", "cpu_count", "process_limit",
    "tmp_megabytes", "output_megabytes",
])
def test_sandbox_limits_must_be_positive(field) -> None:
    values = {
        "timeout_seconds": 60, "memory_megabytes": 512, "cpu_count": 1.0,
        "process_limit": 32, "tmp_megabytes": 64, "output_megabytes": 64,
    }
    values[field] = 0
    with pytest.raises(ValueError, match="positive"):
        SandboxLimits(**values)


@pytest.mark.parametrize("job_id", ["", "../escape", "job/path", "作业"])
def test_request_rejects_path_like_or_non_ascii_job_ids(job_id) -> None:
    with pytest.raises(ValueError, match="opaque ASCII"):
        SandboxRequest(job_id=job_id, code="print(1)", input_snapshot_id="snapshot")


def test_fake_sandbox_rejects_ambiguous_configuration() -> None:
    with pytest.raises(ValueError, match="either"):
        FakePythonSandbox(SandboxResult(status="succeeded"),
                          responder=lambda _: SandboxResult(status="failed"))
