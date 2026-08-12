from agent.docker_python_sandbox import DockerPythonSandbox
from agent.python_sandbox import SandboxLimits, SandboxRequest


class Controller:
    def __init__(self, states):
        self.states = iter(states)
        self.submitted = []
        self.cancelled = []

    def submit(self, job_id):
        self.submitted.append(job_id)
        return {"status": "submitted"}

    def status(self, job_id):
        return next(self.states)

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return {"status": "cancelled"}


def request(timeout=60):
    return SandboxRequest("job-1", "print(1)", "snap", SandboxLimits(timeout_seconds=timeout))


def test_adapter_returns_sandbox_result_without_docker_authority() -> None:
    controller = Controller([
        {"status": "running"},
        {"status": "succeeded", "exit_code": 0, "stdout": "1\n",
         "artifacts": [{"relative_path": "out.csv", "size_bytes": 2}]},
    ])
    times = iter([0.0, 0.1, 0.2, 0.3])
    sandbox = DockerPythonSandbox(controller, image_digest="sha256:abc", clock=lambda: next(times),
                                  sleeper=lambda _: None)
    result = sandbox.execute(request())
    assert result.ok
    assert result.stdout == "1\n"
    assert result.artifacts[0].relative_path == "out.csv"
    assert controller.submitted == ["job-1"]


def test_adapter_cancels_after_deadline() -> None:
    controller = Controller([{"status": "running"}])
    times = iter([0.0, 2.0, 2.0])
    sandbox = DockerPythonSandbox(controller, image_digest="sha256:abc", clock=lambda: next(times),
                                  sleeper=lambda _: None)
    result = sandbox.execute(request(timeout=1))
    assert result.status == "timed_out"
    assert controller.cancelled == ["job-1"]


def test_adapter_fails_closed_on_unknown_controller_status() -> None:
    controller = Controller([{"status": "mystery"}])
    times = iter([0.0, 0.1])
    result = DockerPythonSandbox(controller, image_digest="sha256:abc", clock=lambda: next(times)).execute(request())
    assert result.status == "system_error"
    assert "unexpected" in result.error


def test_adapter_truncates_controller_output() -> None:
    controller = Controller([{"status": "failed", "exit_code": 1,
                              "stdout": "x" * 9000, "stderr": "y" * 9000}])
    times = iter([0.0, 0.1])
    result = DockerPythonSandbox(controller, image_digest="sha256:abc", clock=lambda: next(times)).execute(request())
    assert len(result.stdout) == len(result.stderr) == 8000


def test_adapter_requires_digest_pinned_image() -> None:
    try:
        DockerPythonSandbox(Controller([]), image_digest="latest")
    except ValueError as exc:
        assert "pinned" in str(exc)
    else:
        raise AssertionError("unpinned image accepted")
