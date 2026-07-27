from __future__ import annotations

from analysis.jobs import BackgroundJobService
from analysis.runs import AnalysisRequest, AnalysisRunner
from agent.types import ToolResult, ok


def run_data_quality_analysis(
    runner: AnalysisRunner,
    *,
    project_id: str,
    batch_id: str,
    points: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    force_rerun: bool = False,
) -> ToolResult:
    """Adapt Agent tool arguments to the shared synchronous runner."""
    result = runner.run(
        AnalysisRequest(
            project_id=project_id,
            batch_id=batch_id,
            algorithm="data_quality",
            points=points,
            start=start,
            end=end,
            force_rerun=force_rerun,
        )
    )
    action = "复用已有" if result.reused else "生成"
    return ok(
        f"数据质量检查完成：{action}第 {result.version} 版结果。",
        artifacts=result.artifacts,
        run_id=result.run_id,
        version=result.version,
        reused=result.reused,
        identity=result.identity,
        table=result.data["table"],
    )


def submit_data_quality_analysis(
    jobs: BackgroundJobService,
    *,
    project_id: str,
    batch_id: str,
    points: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    force_rerun: bool = False,
) -> ToolResult:
    """Submit data quality work through the shared background job service."""
    job = jobs.submit(
        AnalysisRequest(
            project_id=project_id,
            batch_id=batch_id,
            algorithm="data_quality",
            points=points,
            start=start,
            end=end,
            force_rerun=force_rerun,
        )
    )
    return ok(
        "数据质量检查已进入后台队列。",
        job_id=job.job_id,
        job_status=job.status,
        project_id=job.project_id,
        batch_id=job.batch_id,
    )
