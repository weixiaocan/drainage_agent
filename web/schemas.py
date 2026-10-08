"""Request/response models and serializers for the web API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, Field

from analysis.jobs import BackgroundJob
from web.projects import AnalysisBatch, Project


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    project_id: str | None = None
    batch_id: str | None = None
    debug: bool = False
    model_id: str | None = None
    attachment_paths: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    run_id: str
    reply: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    python_approval: dict[str, Any] | None = None


class CancelRequest(BaseModel):
    session_id: str


class PythonApprovalCommand(BaseModel):
    session_id: str
    code_sha256: str
    approved_capabilities: list[str] = Field(default_factory=list)


class ProjectCreateRequest(BaseModel):
    name: str


class AnalysisBatchCreateRequest(BaseModel):
    name: str


class ImportMappingRequest(BaseModel):
    mapping: dict[str, str]
    units: dict[str, str]


class BatchImportMappingItem(BaseModel):
    import_id: str
    mapping: dict[str, str]
    units: dict[str, str]


class BatchImportMappingRequest(BaseModel):
    imports: list[BatchImportMappingItem]


class ImportProfileCreateRequest(BaseModel):
    name: str
    source_identifier: str
    mapping: dict[str, str]
    source_units: dict[str, str]
    parsing_rules: dict[str, str]


class AnalysisRunRequest(BaseModel):
    points: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    event_ids: list[int] = Field(default_factory=list)
    scope: str = "all"
    force_rerun: bool = False


class ReportDraftRequest(BaseModel):
    template_id: str = "builtin"


class FilterRunRequest(BaseModel):
    missing_rate_threshold: float = 0.1
    expected_rows_per_day: int = 1440
    rain_day_filter_threshold: float = 2.0
    zero_like_threshold: float = 0.02
    high_zero_ratio_threshold: float = 0.5
    high_zero_ratio_normal_days_threshold: int = 5
    zero_day_drop_min_nonzero_keep_days: int = 3
    mean_lower_ratio: float = 0.5
    mean_upper_ratio: float = 2.0


class FilterConfirmationRequest(BaseModel):
    confirm: bool


def project_data(project: Project) -> dict[str, str]:
    return asdict(project)


def batch_data(batch: AnalysisBatch) -> dict[str, str]:
    return asdict(batch)

def job_data(job: BackgroundJob) -> dict[str, object]:
    data = asdict(job)
    data["result_url"] = (
        f"/api/projects/{job.project_id}/batches/{job.batch_id}"
        f"/analysis-results/{job.result_run_id}"
        if job.result_run_id
        else None
    )
    return data
