from pathlib import Path

import pytest

from agent.python_sandbox import SandboxLimits
from sandbox_controller.controller import DockerCliRuntime, SandboxController


class FakeRuntime:
    def __init__(self) -> None:
        self.submissions = []
        self.cancelled = []
        self.removed = []
        self.log_output = ("stdout", "stderr")
        self.containers = []
        self.runtime_status = ("running", 0)

    def submit(self, **kwargs) -> None:
        self.submissions.append(kwargs)

    def inspect(self, container_name):
        return self.runtime_status

    def cancel(self, container_name):
        self.cancelled.append(container_name)

    def remove(self, container_name):
        self.removed.append(container_name)

    def logs(self, container_name):
        return self.log_output

    def managed_containers(self):
        return self.containers


def controller(tmp_path):
    job = tmp_path / "jobs" / "job-1"
    (job / "code").mkdir(parents=True)
    (job / "input").mkdir()
    (job / "output").mkdir()
    (job / "code" / "main.py").write_text("print(1)", encoding="utf-8")
    runtime = FakeRuntime()
    return SandboxController(tmp_path / "jobs", tmp_path / "state" / "jobs.json", runtime), runtime


def test_submit_is_idempotent_and_uses_resolved_fixed_job_root(tmp_path) -> None:
    service, runtime = controller(tmp_path)
    first = service.submit("job-1", SandboxLimits(memory_megabytes=256))
    second = service.submit("job-1")
    assert first == second
    assert len(runtime.submissions) == 1
    assert runtime.submissions[0]["job_root"] == (tmp_path / "jobs" / "job-1").resolve()
    assert runtime.submissions[0]["limits"].memory_megabytes == 256


@pytest.mark.parametrize("job_id", ["../escape", "a/b", "C:\\host", "", "作业"])
def test_controller_rejects_path_like_job_ids(tmp_path, job_id) -> None:
    service, _ = controller(tmp_path)
    with pytest.raises(ValueError, match="opaque"):
        service.submit(job_id)


def test_status_and_cancel_are_persisted(tmp_path) -> None:
    service, runtime = controller(tmp_path)
    service.submit("job-1")
    assert service.status("job-1").status == "running"
    assert service.cancel("job-1").status == "cancelled"
    assert runtime.cancelled == ["drainage-python-job-1"]
    assert runtime.removed == ["drainage-python-job-1"]
    restored = SandboxController(service.jobs_root, service.state_file, runtime)
    assert restored.status("job-1").status == "cancelled"


def test_completed_runtime_status_is_recorded(tmp_path) -> None:
    service, runtime = controller(tmp_path)
    service.submit("job-1")
    runtime.runtime_status = ("exited", 7)
    result = service.status("job-1")
    assert result.status == "failed"
    assert result.exit_code == 7
    assert result.stdout == "stdout"
    assert result.stderr == "stderr"
    assert runtime.removed == ["drainage-python-job-1"]


def test_restart_recovery_removes_only_managed_containers_and_fails_running_jobs(tmp_path) -> None:
    service, runtime = controller(tmp_path)
    service.submit("job-1")
    runtime.containers = ["drainage-python-job-1", "unrelated-database"]
    removed = service.recover_orphans()
    assert removed == ["drainage-python-job-1"]
    assert "unrelated-database" not in runtime.removed
    recovered = service.status("job-1")
    assert recovered.status == "failed"
    assert "restarted" in recovered.error


def test_docker_runtime_requires_digest_pinned_fixed_image() -> None:
    with pytest.raises(ValueError, match="digest"):
        DockerCliRuntime("drainage-python-sandbox:latest")
    assert DockerCliRuntime("drainage-python-sandbox@sha256:abc").image.endswith("abc")


def test_docker_command_has_mandatory_security_controls(monkeypatch, tmp_path) -> None:
    commands = []
    monkeypatch.setattr(DockerCliRuntime, "_run", staticmethod(lambda command: commands.append(command)))
    job_root = tmp_path / "job-1"
    DockerCliRuntime("drainage-python-sandbox@sha256:abc").submit(
        container_name="drainage-python-job-1", job_root=job_root,
        limits=SandboxLimits(),
    )
    command = commands[0]
    joined = " ".join(command)
    for required in ("--network none", "--read-only", "--cap-drop ALL",
                     "no-new-privileges:true", "--user 10001:10001", "--pids-limit",
                     "--memory-swap 512m"):
        assert required in joined
    assert "--privileged" not in command
    assert "/var/run/docker.sock" not in joined
    assert "type=bind" not in joined
    assert "type=volume" in joined
    assert "volume-subpath=job-1/code" in joined
