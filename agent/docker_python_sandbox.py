from __future__ import annotations

import time
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
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


class HttpControllerClient:
    """Authenticated narrow client; sends only an operation and opaque job ID."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 5.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("controller URL must be a plain internal HTTP origin")
        if parsed.hostname not in {"localhost", "127.0.0.1", "sandbox-controller"}:
            raise ValueError("controller URL host is not an approved internal host")
        if len(token) < 32:
            raise ValueError("controller token must contain at least 32 characters")
        if timeout_seconds <= 0:
            raise ValueError("controller timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def submit(self, job_id: str) -> dict[str, object]:
        return self._request("submit", job_id)

    def status(self, job_id: str) -> dict[str, object]:
        return self._request("status", job_id)

    def cancel(self, job_id: str) -> dict[str, object]:
        return self._request("cancel", job_id)

    def _request(self, operation: str, job_id: str) -> dict[str, object]:
        method = "GET" if operation == "status" else "POST"
        body = None if method == "GET" else json.dumps({"job_id": job_id}).encode("utf-8")
        suffix = f"/v1/jobs/{job_id}" if operation == "status" else f"/v1/jobs/{operation}"
        request = Request(
            self.base_url + suffix,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"sandbox controller request failed: {operation}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("sandbox controller returned an invalid response")
        return decoded


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
