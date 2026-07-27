from __future__ import annotations

from dataclasses import asdict

from agent.types import ToolResult, needs_confirmation, ok
from analysis.baselines import FilterBaselineService, FilterRequest


def run_filter_analysis(
    service: FilterBaselineService,
    *,
    project_id: str,
    batch_id: str,
    **parameters: object,
) -> ToolResult:
    """Adapt Agent filter arguments to the shared baseline service."""
    result = service.run_filter(
        FilterRequest(
            project_id=project_id,
            batch_id=batch_id,
            **parameters,
        )
    )
    return needs_confirmation(
        "filter_result",
        "请下载检查筛选结果；如有修改请重新上传，然后明确确认。",
        "自动筛选已完成，等待排水监测分析人员确认。",
        artifacts=[result.artifact],
        filter_id=result.filter_id,
        version=result.version,
        identity=result.identity,
        **result.summary,
    )


def confirm_filter_baseline(
    service: FilterBaselineService,
    *,
    project_id: str,
    batch_id: str,
    filter_id: str,
    confirmed: bool,
) -> ToolResult:
    """Perform an explicit, target-bound filter confirmation."""
    if not confirmed:
        raise ValueError("必须明确确认筛选结果才能建立分析基线")
    baseline = service.confirm(project_id, batch_id, filter_id)
    return ok(
        f"筛选结果已确认为第 {baseline.version} 版分析基线。",
        artifacts=[baseline.artifact],
        **asdict(baseline),
    )
