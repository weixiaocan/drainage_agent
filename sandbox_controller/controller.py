from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
from io import BytesIO
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from agent.python_sandbox import SandboxLimits


JobStatus = Literal["submitted", "running", "succeeded", "failed", "cancelled", "unknown"]
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
MAX_OUTPUT_ARCHIVE_MEMBERS = 100
MAX_OUTPUT_ARCHIVE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ControllerJob:
    job_id: str
    status: JobStatus
    container_name: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[dict[str, object], ...] = ()
    error: str | None = None


class ContainerRuntime(Protocol):
    def submit(self, *, container_name: str, job_root: Path, limits: SandboxLimits) -> None: ...
    def inspect(self, container_name: str) -> tuple[str, int | None]: ...
    def cancel(self, container_name: str) -> None: ...
    def remove(self, container_name: str) -> None: ...
    def logs(self, container_name: str) -> tuple[str, str]: ...
    def collect_output(self, container_name: str, output_root: Path) -> None: ...
    def managed_containers(self) -> list[str]: ...


class SandboxController:
    def __init__(self, jobs_root: Path, state_file: Path, runtime: ContainerRuntime) -> None:
        self.jobs_root = jobs_root.resolve()
        self.state_file = state_file.resolve()
        self.runtime = runtime
        self._lock = Lock()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def submit(self, job_id: str, limits: SandboxLimits = SandboxLimits()) -> ControllerJob:
        job_root = self._job_root(job_id)
        self._validate_job(job_root)
        with self._lock:
            jobs = self._load()
            existing = jobs.get(job_id)
            if existing:
                return self._decode_job(existing)
            job = ControllerJob(job_id, "submitted", f"drainage-python-{job_id}")
            jobs[job_id] = asdict(job)
            self._save(jobs)
        try:
            self.runtime.submit(container_name=job.container_name, job_root=job_root, limits=limits)
        except Exception:
            self._record(ControllerJob(job_id, "failed", job.container_name))
            raise
        running = ControllerJob(job_id, "running", job.container_name)
        self._record(running)
        return running

    def status(self, job_id: str) -> ControllerJob:
        self._job_root(job_id)
        record = self._load().get(job_id)
        if not record:
            return ControllerJob(job_id, "unknown", f"drainage-python-{job_id}")
        job = self._decode_job(record)
        if job.status != "running":
            return job
        runtime_status, exit_code = self.runtime.inspect(job.container_name)
        mapped: JobStatus = {"running": "running", "exited": "succeeded" if exit_code == 0 else "failed"}.get(
            runtime_status, "failed")  # type: ignore[assignment]
        current = ControllerJob(job_id, mapped, job.container_name, exit_code)
        if current.status != "running":
            stdout, stderr = self.runtime.logs(job.container_name)
            try:
                self.runtime.collect_output(job.container_name, self._job_root(job_id) / "output")
                artifacts = self._candidate_artifacts(self._job_root(job_id))
                error = None
            except Exception as exc:
                mapped = "failed"
                artifacts = ()
                error = f"sandbox output collection failed: {type(exc).__name__}"
            current = ControllerJob(job_id, mapped, job.container_name, exit_code,
                                    stdout[-8000:], stderr[-8000:], artifacts, error)
        self._record(current)
        if current.status != "running":
            self.runtime.remove(job.container_name)
        return current

    def cancel(self, job_id: str) -> ControllerJob:
        job = self.status(job_id)
        if job.status == "running":
            self.runtime.cancel(job.container_name)
            self.runtime.remove(job.container_name)
            job = ControllerJob(job_id, "cancelled", job.container_name)
            self._record(job)
        return job

    def recover_orphans(self) -> list[str]:
        """Remove only controller-owned containers left across a restart."""
        removed = []
        jobs = self._load()
        known = {str(item.get("container_name")) for item in jobs.values()}
        for name in self.runtime.managed_containers():
            if not name.startswith("drainage-python-"):
                continue
            self.runtime.remove(name)
            removed.append(name)
        for job_id, item in jobs.items():
            if item.get("status") in {"submitted", "running"} and item.get("container_name") in known:
                item.update({"status": "failed", "error": "controller restarted during execution"})
                jobs[job_id] = item
        self._save(jobs)
        return removed

    def _job_root(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError("job_id must be an opaque identifier")
        root = (self.jobs_root / job_id).resolve()
        if not root.is_relative_to(self.jobs_root):
            raise ValueError("job path escapes jobs root")
        return root

    @staticmethod
    def _validate_job(root: Path) -> None:
        required = (root / "code" / "main.py", root / "input", root / "output")
        if not required[0].is_file() or not required[1].is_dir() or not required[2].is_dir():
            raise ValueError("job directory does not satisfy controller contract")
        if any(path.is_symlink() for path in required):
            raise ValueError("job contract paths cannot be links")

    @staticmethod
    def _candidate_artifacts(root: Path) -> tuple[dict[str, object], ...]:
        output = root / "output"
        if not output.is_dir():
            return ()
        result = []
        for path in sorted(output.iterdir())[:100]:
            if path.is_file() and not path.is_symlink():
                result.append({"relative_path": path.name, "size_bytes": path.stat().st_size})
        return tuple(result)

    def _record(self, job: ControllerJob) -> None:
        with self._lock:
            jobs = self._load()
            jobs[job.job_id] = asdict(job)
            self._save(jobs)

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.state_file.is_file():
            return {}
        decoded = json.loads(self.state_file.read_text(encoding="utf-8"))
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _decode_job(value: dict[str, object]) -> ControllerJob:
        decoded = dict(value)
        decoded["artifacts"] = tuple(decoded.get("artifacts") or ())
        return ControllerJob(**decoded)  # type: ignore[arg-type]

    def _save(self, jobs: dict[str, dict[str, object]]) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(jobs, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_file)


class DockerCliRuntime:
    """Docker access belongs only in the separately deployed controller process."""

    def __init__(self, image: str, jobs_volume: str = "sandbox-jobs") -> None:
        if not image.startswith("drainage-python-sandbox@sha256:"):
            raise ValueError("sandbox image must be pinned by digest")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", jobs_volume):
            raise ValueError("sandbox jobs volume name is invalid")
        self.image = image
        self.jobs_volume = jobs_volume

    def submit(self, *, container_name: str, job_root: Path, limits: SandboxLimits) -> None:
        job_id = job_root.name
        command = [
            "docker", "run", "--detach", "--name", container_name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--user", "10001:10001",
            "--cpus", str(limits.cpu_count), "--memory", f"{limits.memory_megabytes}m",
            "--memory-swap", f"{limits.memory_megabytes}m",
            "--pids-limit", str(limits.process_limit),
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={limits.tmp_megabytes}m",
            "--tmpfs", (f"/job/output:rw,noexec,nosuid,size={limits.output_megabytes}m,"
                        "uid=10001,gid=10001,mode=0770"),
            "--mount", f"type=volume,src={self.jobs_volume},dst=/job/code,volume-subpath={job_id}/code,readonly",
            "--mount", f"type=volume,src={self.jobs_volume},dst=/job/input,volume-subpath={job_id}/input,readonly",
            self.image,
        ]
        self._run(command)

    def inspect(self, container_name: str) -> tuple[str, int | None]:
        completed = self._run(["docker", "inspect", "--format", "{{.State.Status}}|{{.State.ExitCode}}",
                               container_name])
        status, exit_code = completed.stdout.strip().split("|", 1)
        if status == "running":
            marker = self._run(
                ["docker", "exec", container_name, "cat", "/tmp/sandbox-complete"], check=False,
            )
            if marker.returncode == 0 and marker.stdout.strip().lstrip("-").isdigit():
                return "exited", int(marker.stdout.strip())
        return status, int(exit_code)

    def cancel(self, container_name: str) -> None:
        self._run(["docker", "kill", container_name])

    def remove(self, container_name: str) -> None:
        self._run(["docker", "rm", "--force", container_name])

    def logs(self, container_name: str) -> tuple[str, str]:
        completed = self._run(["docker", "logs", container_name])
        return completed.stdout, completed.stderr

    def collect_output(self, container_name: str, output_root: Path) -> None:
        output_root.mkdir(parents=True, exist_ok=True)
        if any(output_root.iterdir()):
            raise RuntimeError("sandbox output destination is not empty")
        archive = self._run_bytes([
            "docker", "exec", container_name, "tar", "-C", "/job/output", "-cf", "-", ".",
        ])
        root = output_root.resolve()
        member_count = 0
        total_bytes = 0
        with tarfile.open(fileobj=BytesIO(archive), mode="r|") as bundle:
            for member in bundle:
                member_count += 1
                if member_count > MAX_OUTPUT_ARCHIVE_MEMBERS:
                    raise RuntimeError("sandbox output archive contains too many members")
                if member.size < 0:
                    raise RuntimeError("sandbox output archive has an invalid member size")
                total_bytes += member.size
                if total_bytes > MAX_OUTPUT_ARCHIVE_BYTES:
                    raise RuntimeError("sandbox output archive exceeds the size limit")
                relative = Path(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("sandbox output archive escapes destination")
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise RuntimeError("sandbox output archive escapes destination")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise RuntimeError("sandbox output archive contains a special file")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("sandbox output archive member is unreadable")
                with target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)

    def managed_containers(self) -> list[str]:
        completed = self._run([
            "docker", "ps", "--all", "--filter", "name=^drainage-python-",
            "--format", "{{.Names}}",
        ])
        return [line for line in completed.stdout.splitlines() if line.startswith("drainage-python-")]

    @staticmethod
    def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, check=check, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15)

    @staticmethod
    def _run_bytes(command: list[str]) -> bytes:
        return subprocess.run(command, check=True, capture_output=True, timeout=15).stdout
