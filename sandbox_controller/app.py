from __future__ import annotations

import hmac
import os
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sandbox_controller.controller import DockerCliRuntime, SandboxController


class JobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


def create_controller_app(controller: SandboxController, token: str) -> FastAPI:
    if len(token) < 32:
        raise ValueError("controller token must contain at least 32 characters")
    app = FastAPI(title="Sandbox Controller", docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.post("/v1/jobs/submit", dependencies=[Depends(authenticate)])
    def submit(command: JobCommand) -> dict[str, object]:
        return asdict(controller.submit(command.job_id))

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def status(job_id: str) -> dict[str, object]:
        try:
            return asdict(controller.status(job_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid job identifier") from exc

    @app.post("/v1/jobs/cancel", dependencies=[Depends(authenticate)])
    def cancel(command: JobCommand) -> dict[str, object]:
        return asdict(controller.cancel(command.job_id))

    return app


def build_app_from_env() -> FastAPI:
    token = os.environ.get("SANDBOX_CONTROLLER_TOKEN", "")
    jobs_root = Path(os.environ.get("SANDBOX_JOBS_ROOT", "/var/lib/sandbox-jobs"))
    state_file = Path(os.environ.get("SANDBOX_STATE_FILE", "/var/lib/sandbox-controller/jobs.json"))
    image = os.environ.get("SANDBOX_IMAGE", "")
    jobs_volume = os.environ.get("SANDBOX_JOBS_VOLUME", "sandbox-jobs")
    controller = SandboxController(jobs_root, state_file, DockerCliRuntime(image, jobs_volume))
    return create_controller_app(controller, token)
