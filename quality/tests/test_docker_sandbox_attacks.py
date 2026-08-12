from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from agent.python_sandbox import SandboxLimits
from agent.docker_python_sandbox import DockerPythonSandbox
from agent.python_sandbox import SandboxRequest
from sandbox_controller.controller import DockerCliRuntime, SandboxController


pytestmark = pytest.mark.skipif(
    os.environ.get("DRAINAGE_RUN_DOCKER_ATTACK_TESTS") != "1",
    reason="set DRAINAGE_RUN_DOCKER_ATTACK_TESTS=1 to run real Docker attacks",
)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )


@pytest.fixture()
def real_controller(tmp_path: Path):
    image = os.environ.get("DRAINAGE_SANDBOX_TEST_IMAGE", "")
    if not image.startswith("drainage-python-sandbox@sha256:"):
        pytest.fail("DRAINAGE_SANDBOX_TEST_IMAGE must be pinned by digest")
    volume = f"drainage-sandbox-test-{uuid.uuid4().hex}"
    helper = f"drainage-sandbox-prep-{uuid.uuid4().hex}"
    _docker("volume", "create", volume)
    _docker(
        "run", "--detach", "--name", helper, "--user", "0:0",
        "--mount", f"type=volume,src={volume},dst=/jobs",
        "--entrypoint", "sleep", image, "300",
    )
    jobs_root = tmp_path / "jobs"
    controller = SandboxController(
        jobs_root, tmp_path / "controller-state.json", DockerCliRuntime(image, volume),
    )
    try:
        yield controller, helper, jobs_root
    finally:
        controller.recover_orphans()
        _docker("rm", "--force", helper, check=False)
        _docker("volume", "rm", "--force", volume, check=False)


def _execute(controller: SandboxController, helper: str, jobs_root: Path,
             code: str, *, limits: SandboxLimits | None = None):
    job_id = f"attack-{uuid.uuid4().hex}"
    local = jobs_root / job_id
    (local / "code").mkdir(parents=True)
    (local / "input").mkdir()
    (local / "output").mkdir()
    (local / "code" / "main.py").write_text(code, encoding="utf-8")
    (local / "input" / "sentinel.txt").write_text("immutable", encoding="utf-8")
    _docker("exec", helper, "mkdir", "-p", f"/jobs/{job_id}")
    _docker("cp", f"{local}{os.sep}.", f"{helper}:/jobs/{job_id}")
    _docker("exec", helper, "chown", "-R", "10001:10001", f"/jobs/{job_id}/output")
    controller.submit(job_id, limits or SandboxLimits(timeout_seconds=10, memory_megabytes=128,
                                                       process_limit=16, tmp_megabytes=16))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = controller.status(job_id)
        if result.status != "running":
            return result
        time.sleep(0.1)
    controller.cancel(job_id)
    pytest.fail("real sandbox did not finish before the test deadline")


class _DirectControllerClient:
    def __init__(self, controller: SandboxController) -> None:
        self.controller = controller

    def submit(self, job_id: str):
        return self.controller.submit(job_id).__dict__

    def status(self, job_id: str):
        return self.controller.status(job_id).__dict__

    def cancel(self, job_id: str):
        return self.controller.cancel(job_id).__dict__


@pytest.mark.parametrize(
    ("code", "marker"),
    [
        (
            "import socket\n"
            "try:\n socket.create_connection(('1.1.1.1', 53), timeout=1)\n"
            "except OSError:\n print('NETWORK_BLOCKED')\n"
            "else:\n raise RuntimeError('network unexpectedly available')\n",
            "NETWORK_BLOCKED",
        ),
        (
            "from pathlib import Path\n"
            "try:\n Path('/escape.txt').write_text('bad')\n"
            "except OSError:\n print('ROOTFS_BLOCKED')\n"
            "else:\n raise RuntimeError('root filesystem unexpectedly writable')\n",
            "ROOTFS_BLOCKED",
        ),
        (
            "from pathlib import Path\n"
            "try:\n Path('/job/input/sentinel.txt').write_text('bad')\n"
            "except OSError:\n print('INPUT_BLOCKED')\n"
            "else:\n raise RuntimeError('input unexpectedly writable')\n",
            "INPUT_BLOCKED",
        ),
        (
            "import os\n"
            "assert os.geteuid() == 10001\n"
            "status = open('/proc/self/status', encoding='utf-8').read()\n"
            "assert 'CapEff:\\t0000000000000000' in status\n"
            "assert not os.path.exists('/var/run/docker.sock')\n"
            "assert not os.path.exists('/app/.env')\n"
            "print('AUTHORITY_BLOCKED')\n",
            "AUTHORITY_BLOCKED",
        ),
    ],
)
def test_real_sandbox_blocks_escape_vectors(real_controller, code: str, marker: str) -> None:
    controller, helper, jobs_root = real_controller
    result = _execute(controller, helper, jobs_root, code)

    assert result.status == "succeeded", result.stderr
    assert marker in result.stdout


def test_real_sandbox_enforces_memory_limit(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    result = _execute(
        controller, helper, jobs_root,
        "payload = bytearray(512 * 1024 * 1024)\nprint(len(payload))\n",
        limits=SandboxLimits(timeout_seconds=10, memory_megabytes=64, process_limit=8,
                             tmp_megabytes=16),
    )

    assert result.status == "failed"
    assert result.exit_code != 0


def test_real_sandbox_enforces_process_limit(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    result = _execute(
        controller, helper, jobs_root,
        "from pathlib import Path\n"
        "limit = Path('/sys/fs/cgroup/pids.max').read_text().strip()\n"
        "assert limit == '8', limit\n"
        "print('PIDS_LIMITED')\n",
        limits=SandboxLimits(timeout_seconds=10, memory_megabytes=64, process_limit=8,
                             tmp_megabytes=16),
    )

    assert result.status == "succeeded", result.stderr
    assert "PIDS_LIMITED" in result.stdout


def test_real_sandbox_enforces_tmpfs_limit(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    result = _execute(
        controller, helper, jobs_root,
        "import os\n"
        "stats = os.statvfs('/tmp')\n"
        "capacity = stats.f_frsize * stats.f_blocks\n"
        "assert capacity <= 17 * 1024 * 1024, capacity\n"
        "print('TMP_LIMITED')\n",
        limits=SandboxLimits(timeout_seconds=10, memory_megabytes=64, process_limit=8,
                             tmp_megabytes=16),
    )

    assert result.status == "succeeded", result.stderr
    assert "TMP_LIMITED" in result.stdout


def test_real_sandbox_enforces_output_limit_and_collects_artifacts(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    success = _execute(
        controller, helper, jobs_root,
        "from pathlib import Path\n"
        "Path('/job/output/result.json').write_text('{\"ok\": true}')\n",
        limits=SandboxLimits(timeout_seconds=10, memory_megabytes=64, process_limit=8,
                             tmp_megabytes=16, output_megabytes=8),
    )

    assert success.status == "succeeded", success.stderr
    assert success.artifacts == ({"relative_path": "result.json", "size_bytes": 12},)

    exhausted = _execute(
        controller, helper, jobs_root,
        "with open('/job/output/fill.bin', 'wb') as target:\n"
        " target.write(b'x' * (16 * 1024 * 1024))\n",
        limits=SandboxLimits(timeout_seconds=10, memory_megabytes=64, process_limit=8,
                             tmp_megabytes=16, output_megabytes=8),
    )

    assert exhausted.status == "failed"
    assert exhausted.exit_code != 0


@pytest.mark.parametrize(
    "code",
    [
        (
            "from pathlib import Path\n"
            "for index in range(101):\n"
            " Path('/job/output', f'{index}.json').write_text('{}')\n"
        ),
        (
            "import os\n"
            "os.symlink('/etc/passwd', '/job/output/escape-link')\n"
        ),
    ],
)
def test_real_sandbox_rejects_unsafe_output_collections(real_controller, code: str) -> None:
    controller, helper, jobs_root = real_controller
    result = _execute(controller, helper, jobs_root, code)

    assert result.status == "failed"
    assert result.artifacts == ()
    assert "output collection failed" in (result.error or "")


def test_real_sandbox_times_out_and_removes_infinite_loop(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    job_id = f"attack-{uuid.uuid4().hex}"
    local = jobs_root / job_id
    (local / "code").mkdir(parents=True)
    (local / "input").mkdir()
    (local / "output").mkdir()
    code = "while True:\n pass\n"
    (local / "code" / "main.py").write_text(code, encoding="utf-8")
    _docker("exec", helper, "mkdir", "-p", f"/jobs/{job_id}")
    _docker("cp", f"{local}{os.sep}.", f"{helper}:/jobs/{job_id}")
    sandbox = DockerPythonSandbox(
        _DirectControllerClient(controller), image_digest="sha256:test", poll_interval_seconds=0.05,
    )

    result = sandbox.execute(SandboxRequest(
        job_id, code, "snapshot", SandboxLimits(timeout_seconds=1, memory_megabytes=64,
                                                 process_limit=8, tmp_megabytes=16),
    ))

    assert result.status == "timed_out"
    names = _docker("ps", "--all", "--format", "{{.Names}}").stdout.splitlines()
    assert f"drainage-python-{job_id}" not in names


def test_real_controller_restart_removes_orphan(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    job_id = f"attack-{uuid.uuid4().hex}"
    local = jobs_root / job_id
    (local / "code").mkdir(parents=True)
    (local / "input").mkdir()
    (local / "output").mkdir()
    (local / "code" / "main.py").write_text("while True:\n pass\n", encoding="utf-8")
    _docker("exec", helper, "mkdir", "-p", f"/jobs/{job_id}")
    _docker("cp", f"{local}{os.sep}.", f"{helper}:/jobs/{job_id}")
    controller.submit(job_id, SandboxLimits(timeout_seconds=10, memory_megabytes=64,
                                            process_limit=8, tmp_megabytes=16))
    restarted = SandboxController(controller.jobs_root, controller.state_file, controller.runtime)

    removed = restarted.recover_orphans()

    assert f"drainage-python-{job_id}" in removed
    assert restarted.status(job_id).status == "failed"


def test_real_sandbox_cleanup_leaves_no_job_container(real_controller) -> None:
    controller, helper, jobs_root = real_controller
    result = _execute(controller, helper, jobs_root, "print('DONE')\n")

    assert result.status == "succeeded"
    names = _docker("ps", "--all", "--format", "{{.Names}}").stdout.splitlines()
    assert result.container_name not in names
