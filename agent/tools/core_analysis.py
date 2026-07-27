from __future__ import annotations

from analysis.jobs import BackgroundJobService
from analysis.runs import (
    AnalysisInputRequired,
    AnalysisRequest,
    AnalysisRunner,
)
from agent.types import ToolResult, needs_input, ok


def submit_core_analysis(
    jobs: BackgroundJobService,
    runner: AnalysisRunner,
    request: AnalysisRequest,
) -> ToolResult:
    """Adapt Agent analysis intent to the shared local job service."""
    try:
        runner.validate(request)
    except AnalysisInputRequired as exc:
        return needs_input(
            exc.field,
            str(exc),
            summary=str(exc),
        )
    job = jobs.submit(request)
    return ok(
        f"{request.algorithm} 已进入后台分析队列。",
        job_id=job.job_id,
        job_status=job.status,
        project_id=job.project_id,
        batch_id=job.batch_id,
        algorithm=request.algorithm,
    )
