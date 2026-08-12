from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Protocol

from agent.python_sandbox import PythonSandbox, SandboxArtifact, SandboxRequest, SandboxResult


class SandboxControllerClient(Protocol):
    def submit(self, job_id: str) -> dict[str, object]: ...
    def status(self, job_id: str) -> dict[str, object]: ...
    def cancel(self, job_id: str) -> dict[str, object]: ...


class DockerPythonSandbox(PythonSandbox):
    """Controller-backed adapter with no Docker or host-path authority."""

    def __init__(self, controller: SandboxControllerClient, *, image_digest: str,
                 poll_interval_seconds: float = 0.2,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        if not image_digest.startswith("sha256:"):
            raise ValueError("sandbox image digest must be pinned")
        if poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        self.controller = controller
        self.image_digest = image_digest
        self.poll_interval_seconds = poll_interval_seconds
        self.clock = clock
        self.sleeper = sleeper

    def execute(self, request: SandboxRequest) -> SandboxResult:
        started = self.clock()
        self.controller.submit(request.job_id)
        deadline = started + request.limits.timeout_seconds
        while True:
            state = self.controller.status(request.job_id)
            status = str(state.get("status", "unknown"))
            if status in {"succeeded", "failed", "cancelled"}:
                return self._result(request, state, started)
            if status not in {"submitted", "running"}:
                return SandboxResult(status="system_error", duration_ms=self._elapsed(started),
                                     error=f"unexpected controller status: {status}")
            if self.clock() >= deadline:
                self.controller.cancel(request.job_id)
                return SandboxResult(status="timed_out", duration_ms=self._elapsed(started),
                                     error="sandbox execution deadline exceeded")
            self.sleeper(self.poll_interval_seconds)

    def _result(self, request: SandboxRequest, state: dict[str, object], started: float) -> SandboxResult:
        status = str(state.get("status"))
        artifacts = self._artifacts(state.get("artifacts", []))
        mapped = "succeeded" if status == "succeeded" else "failed"
        if status == "cancelled":
            mapped = "failed"
        return SandboxResult(
            status=mapped,  # type: ignore[arg-type]
            exit_code=_optional_int(state.get("exit_code")),
            stdout=str(state.get("stdout") or "")[-8000:],
            stderr=str(state.get("stderr") or "")[-8000:],
            duration_ms=self._elapsed(started),
            artifacts=artifacts,
            error=str(state["error"]) if state.get("error") else None,
        )

    @staticmethod
    def _artifacts(value: object) -> tuple[SandboxArtifact, ...]:
        if not isinstance(value, list):
            return ()
        result = []
        for item in value:
            if not isinstance(item, dict):
                continue
            path = item.get("relative_path")
            size = item.get("size_bytes")
            if isinstance(path, str) and isinstance(size, int) and size >= 0:
                result.append(SandboxArtifact(path, size))
        return tuple(result)

    def _elapsed(self, started: float) -> int:
        return max(0, int((self.clock() - started) * 1000))


class FileControllerClient:
    """Narrow local transport for separately mounted controller inbox/outbox files."""

    def __init__(self, exchange_root: Path) -> None:
        self.exchange_root = exchange_root.resolve()

    def submit(self, job_id: str) -> dict[str, object]:
        return self._request("submit", job_id)

    def status(self, job_id: str) -> dict[str, object]:
        return self._request("status", job_id)

    def cancel(self, job_id: str) -> dict[str, object]:
        return self._request("cancel", job_id)

    def _request(self, operation: str, job_id: str) -> dict[str, object]:
        # Transport implementation is intentionally fail-closed until the
        # controller service owns the exchange and authentication protocol.
        raise RuntimeError(f"controller transport unavailable for {operation}:{job_id}")


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
