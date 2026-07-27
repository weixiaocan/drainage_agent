from __future__ import annotations

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
    """Adapt Agent tool arguments to the shared analysis-run interface."""
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
