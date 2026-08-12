from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol


SandboxStatus = Literal[
    "succeeded",
    "failed",
    "timed_out",
    "resource_exhausted",
    "system_error",
]


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 60
    memory_megabytes: int = 512
    cpu_count: float = 1.0
    process_limit: int = 32
    tmp_megabytes: int = 64
    output_megabytes: int = 64

    def __post_init__(self) -> None:
        values = (
            self.timeout_seconds,
            self.memory_megabytes,
            self.cpu_count,
            self.process_limit,
            self.tmp_megabytes,
            self.output_megabytes,
        )
        if any(value <= 0 for value in values):
            raise ValueError("sandbox limits must be positive")


@dataclass(frozen=True)
class SandboxRequest:
    job_id: str
    code: str
    input_snapshot_id: str
    limits: SandboxLimits = field(default_factory=SandboxLimits)

    def __post_init__(self) -> None:
        if not self.job_id or not self.job_id.isascii() or not self.job_id.replace("-", "").isalnum():
            raise ValueError("job_id must be an opaque ASCII identifier")
        if not self.code.strip():
            raise ValueError("sandbox code must not be empty")
        if not self.input_snapshot_id:
            raise ValueError("input_snapshot_id must not be empty")


@dataclass(frozen=True)
class SandboxArtifact:
    relative_path: str
    size_bytes: int


@dataclass(frozen=True)
class SandboxResult:
    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    artifacts: tuple[SandboxArtifact, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class PythonSandbox(Protocol):
    image_digest: str

    def execute(self, request: SandboxRequest) -> SandboxResult:
        """Execute one request in an implementation-defined isolated sandbox."""
        ...


class FakePythonSandbox:
    """Deterministic test double. It never evaluates the supplied Python code."""

    image_digest = "fake-python-sandbox@sha256:test"

    def __init__(
        self,
        result: SandboxResult | None = None,
        *,
        responder: Callable[[SandboxRequest], SandboxResult] | None = None,
    ) -> None:
        if result is not None and responder is not None:
            raise ValueError("provide either result or responder")
        self._result = result or SandboxResult(status="succeeded", exit_code=0)
        self._responder = responder
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return self._responder(request) if self._responder else self._result
