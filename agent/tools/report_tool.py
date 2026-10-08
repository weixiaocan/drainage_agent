"""Report tool: resolve section dependencies and assemble the DOCX draft."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

from agent.deps import AgentDeps
from agent.types import ToolResult, error, needs_input, ok
from analysis import io
from analysis.reporting import build_report
from agent.tools.analysis_tools import (
    _coverage_guard_result,
    _event_data_coverage,
    _event_options,
    _resolve_implicit_flow_year,
    _source_event_ids,
    _window_bounds,
    analyze_event_response_impl,
    analyze_patterns_impl,
    analyze_rainfall_impl,
    analyze_rdii_impl,
    assess_risk_impl,
    check_data_impl,
)
from agent.tools.tool_support import (
    _cached_report_frame,
    _cached_report_value,
    _normalize_point_scope,
    _range_result_prefix,
    _rel,
    _report_combined_workbook_path,
    _run,
    _safe_filename_part,
    _time_result_prefix,
    _write_report_combined_workbook,
)


DEFAULT_REPORT_SECTIONS = ["监测概况", "降雨分析", "旱天排污规律统计分析", "污水系统运行风险分析"]
REPORT_MONITORING_SECTIONS = {"监测概况", "数据概况", "概述与数据质量", "数据体检", "数据质量"}
REPORT_RAINFALL_SECTIONS = {"降雨分析", "降雨统计", "雨天事件统计", "事件响应", "RDII"}
REPORT_PATTERN_SECTIONS = {
    "旱天排污规律统计分析",
    "旱天排污规律",
    "旱天排污规律分析",
    "点位特征对比分析",
    "排污规律",
    "排污规律分析",
    "旱天分析",
}
REPORT_FULL_RISK_SECTIONS = {"污水系统运行风险分析", "污水系统运行风险", "运行风险分析", "风险评估"}
REPORT_DRY_RISK_SECTIONS = {"旱天风险", "旱天运行风险评估", "结论与建议"}
REPORT_RAINY_RISK_SECTIONS = {"雨天风险", "雨天溢流风险", "溢流风险"}
REPORT_RISK_SECTIONS = REPORT_FULL_RISK_SECTIONS | REPORT_DRY_RISK_SECTIONS | REPORT_RAINY_RISK_SECTIONS


def _section_requested(sections: list[str], aliases: set[str]) -> bool:
    return any(
        section == alias or section.startswith(f"{alias}（") or section.startswith(f"{alias}(")
        for section in sections
        for alias in aliases
    )


def _is_dry_only_report_sections(sections: list[str]) -> bool:
    wants_any_dry = _section_requested(sections, REPORT_PATTERN_SECTIONS | REPORT_DRY_RISK_SECTIONS)
    wants_rain = _section_requested(sections, REPORT_RAINFALL_SECTIONS | REPORT_RAINY_RISK_SECTIONS)
    wants_full_risk = _section_requested(sections, REPORT_FULL_RISK_SECTIONS)
    return wants_any_dry and not wants_rain and not wants_full_risk


def _report_actual_time_range(
    deps: AgentDeps,
    points: list[str] | None,
    start: str | None,
    end: str | None,
) -> tuple[str | None, str | None]:
    try:
        flow = io.load_flow(points=points, root=deps.paths.root)
        start_ts, end_ts = _window_bounds(start, end)
    except Exception:
        return start, end
    if start_ts is not None:
        flow = flow[flow["timestamp"] >= start_ts]
    if end_ts is not None:
        flow = flow[flow["timestamp"] <= end_ts]
    if flow.empty:
        return start, end
    actual_start = flow["timestamp"].min()
    actual_end = flow["timestamp"].max()
    return actual_start.strftime("%Y-%m-%d"), actual_end.strftime("%Y-%m-%d")


def generate_report_impl(
    deps: AgentDeps,
    points: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    sections: list[str] | None = None,
    event_ids: list[int] | None = None,
) -> ToolResult:
    start, end = _resolve_implicit_flow_year(deps, start, end)
    sections = sections or list(DEFAULT_REPORT_SECTIONS)
    report_start, report_end = _report_actual_time_range(deps, points, start, end)
    if start is not None or end is not None:
        flow = io.load_flow(points=points, root=deps.paths.root)
        if not flow.empty:
            start_ts, end_ts = _window_bounds(start, end)
            if start_ts is not None:
                flow = flow[flow["timestamp"] >= start_ts]
            if end_ts is not None:
                flow = flow[flow["timestamp"] <= end_ts]
            if flow.empty:
                return error(
                    f"请求的时间范围 [{start or '不限'}, {end or '不限'}] 内无监测数据覆盖，"
                    "无法生成报告。请修改时间范围后重试。"
                )
    requested_event_ids = _source_event_ids(
        deps, list(event_ids or deps.session.selected_event_ids)
    )
    requests_rainy_analysis = _section_requested(
        sections, REPORT_RAINY_RISK_SECTIONS | {"事件响应", "响应", "RDII"}
    )
    if requests_rainy_analysis and requested_event_ids:
        _, _, covered, excluded = _event_data_coverage(
            deps, requested_event_ids, points
        )
        coverage_failure = _coverage_guard_result(
            deps, requested_event_ids, covered, excluded
        )
        if coverage_failure:
            return coverage_failure
    if (
        deps.report_templates is not None
        and deps.analysis_runner is not None
        and deps.current_project_id
        and deps.current_batch_id
    ):
        from analysis.runs import (
            AnalysisInputRequired,
            AnalysisPreconditionError,
            AnalysisRequest,
        )

        project_id = deps.current_project_id
        batch_id = deps.current_batch_id
        wants_rainfall = _section_requested(
            sections, REPORT_RAINFALL_SECTIONS
        )
        wants_patterns = _section_requested(
            sections, REPORT_PATTERN_SECTIONS
        )
        wants_full_risk = _section_requested(
            sections, REPORT_FULL_RISK_SECTIONS
        )
        wants_dry_risk = wants_full_risk or _section_requested(
            sections, REPORT_DRY_RISK_SECTIONS
        )
        wants_rainy_risk = wants_full_risk or _section_requested(
            sections, REPORT_RAINY_RISK_SECTIONS
        )
        selected_event_ids = list(
            event_ids or deps.session.selected_event_ids
        )
        common = {
            "points": points,
            "start": start,
            "end": end,
        }
        try:
            deps.analysis_runner.run(
                AnalysisRequest(
                    project_id,
                    batch_id,
                    "data_quality",
                    **common,
                )
            )
            if wants_patterns:
                deps.analysis_runner.run(
                    AnalysisRequest(
                        project_id,
                        batch_id,
                        "patterns",
                        **common,
                    )
                )
            rainfall_result = None
            if wants_rainfall or wants_rainy_risk:
                rainfall_result = deps.analysis_runner.run(
                    AnalysisRequest(
                        project_id,
                        batch_id,
                        "rainfall",
                        **common,
                    )
                )
            if wants_rainy_risk and not selected_event_ids:
                selected_event_ids = sorted({
                    int(row["event_id"])
                    for row in (
                        rainfall_result.data.get("events", [])
                        if rainfall_result is not None
                        else []
                    )
                    if row.get("event_id") is not None
                })
            if wants_rainy_risk and not selected_event_ids:
                return error(
                    "当前数据未识别到可用于雨天分析的降雨场次，无法生成包含雨天内容的报告。"
                )
            if wants_rainy_risk:
                for algorithm in ("event_response", "rdii"):
                    deps.analysis_runner.run(
                        AnalysisRequest(
                            project_id,
                            batch_id,
                            algorithm,
                            event_ids=selected_event_ids,
                            **common,
                        )
                    )
            if wants_dry_risk or wants_rainy_risk:
                scope = (
                    "all"
                    if wants_dry_risk and wants_rainy_risk
                    else "dry"
                    if wants_dry_risk
                    else "rainy"
                )
                deps.analysis_runner.run(
                    AnalysisRequest(
                        project_id,
                        batch_id,
                        "risk",
                        event_ids=(
                            selected_event_ids
                            if scope in {"all", "rainy"}
                            else None
                        ),
                        scope=scope,
                        **common,
                    )
                )
            draft = deps.report_templates.create_draft(
                project_id,
                batch_id,
                "builtin",
                points=points,
                start=start,
                end=end,
                sections=sections,
            )
        except AnalysisInputRequired as exc:
            return needs_input(exc.field, str(exc), summary=str(exc))
        except (AnalysisPreconditionError, ValueError, LookupError) as exc:
            return error(str(exc))
        return ok(
            f"报告初稿第 {draft.version} 版已生成，需由排水监测分析人员审核。",
            artifacts=[draft.docx, draft.workbook],
            report_id=draft.report_id,
            version=draft.version,
        )
    points = _normalize_point_scope(points, deps)
    selected_event_ids = list(event_ids or deps.session.selected_event_ids)
    unavailable = sorted(set(selected_event_ids).intersection(deps.session.unavailable_event_ids))
    if unavailable:
        return error(
            f"无法生成可靠报告：场次 {unavailable} 与监测数据无时间重叠，缺少事件响应、RDII 和雨天风险依据。"
        )

    wants_monitoring = _section_requested(sections, REPORT_MONITORING_SECTIONS)
    wants_rainfall = _section_requested(sections, REPORT_RAINFALL_SECTIONS)
    wants_patterns = _section_requested(sections, REPORT_PATTERN_SECTIONS)
    wants_full_risk = _section_requested(sections, REPORT_FULL_RISK_SECTIONS)
    wants_dry_risk = wants_full_risk or _section_requested(sections, REPORT_DRY_RISK_SECTIONS)
    wants_rainy_risk = wants_full_risk or _section_requested(sections, REPORT_RAINY_RISK_SECTIONS)
    wants_event_response = _section_requested(sections, {"事件响应", "雨天事件统计"})
    wants_rdii = _section_requested(sections, {"RDII"})
    wants_risk = wants_dry_risk or wants_rainy_risk
    dry_only_report = _is_dry_only_report_sections(sections)
    if dry_only_report:
        wants_monitoring = True
    if not any((wants_monitoring, wants_rainfall, wants_patterns, wants_risk)):
        return error(f"无法识别报告章节: {sections}")
    tables: dict[str, pd.DataFrame] = {}
    rainfall_chart_paths: dict[str, str] = {}
    pattern_chart_paths: dict[str, list[str]] = {}
    summaries: list[str] = []
    time_range = _resolved_report_time_range(deps, start, end) if start is not None or end is not None else None
    report_sections = list(sections)
    if dry_only_report and not _section_requested(report_sections, REPORT_MONITORING_SECTIONS):
        report_sections.insert(0, "监测概况")

    if wants_monitoring:
        cached = _cached_report_frame(deps, "data_collection", points, start, end)
        if cached is None:
            data_check = check_data_impl(deps, points=points, start=start, end=end)
            if data_check["status"] != "ok":
                return data_check
            cached = _result_frame(data_check, "table")
            summaries.append(data_check["summary"])
        tables["data_collection"] = cached

    rain: ToolResult | None = None
    if wants_rainfall or wants_rainy_risk:
        rain_start = time_range[0] if time_range else None
        rain_end = time_range[1] if time_range else None
        cached_daily = _cached_report_frame(deps, "rainfall_daily", start=rain_start, end=rain_end)
        cached_events = _cached_report_frame(deps, "rainfall_events", start=rain_start, end=rain_end)
        rainfall_chart_paths = _cached_report_value(
            deps, "rainfall_chart_paths", start=rain_start, end=rain_end
        ) or {}
        if cached_daily is None or cached_events is None:
            rain = analyze_rainfall_impl(deps, time_range=time_range)
            if rain["status"] != "ok":
                return rain
            cached_daily = _result_frame(rain, "daily")
            cached_events = _result_frame(rain, "events")
            rainfall_chart_paths = deepcopy(rain.get("data", {}).get("chart_paths", {}))
            summaries.append(rain["summary"])
        tables["rainfall_daily"] = cached_daily
        tables["rainfall_events"] = cached_events

    if wants_event_response or wants_rdii:
        if not selected_event_ids:
            return needs_input(
                "event_ids",
                "请选择事件响应和 RDII 分析使用的降雨场次编号。",
                summary="事件响应和 RDII 分析必须基于明确的降雨场次。",
                options=_event_options(tables.get("rainfall_events", pd.DataFrame())),
            )
        if wants_event_response:
            event_response = analyze_event_response_impl(
                deps,
                event_ids=selected_event_ids,
                points=points,
                export=False,
            )
            if event_response["status"] != "ok":
                return event_response
            tables["rainy_event_stats"] = _result_frame(event_response, "table")
            summaries.append(event_response["summary"])
        if wants_rdii:
            rdii = analyze_rdii_impl(
                deps,
                event_ids=selected_event_ids,
                points=points,
                export=False,
            )
            if rdii["status"] != "ok":
                return rdii
            tables["rdii_total"] = _result_frame(rdii, "table")
            summaries.append(rdii["summary"])

    if wants_patterns:
        cached = _cached_report_frame(deps, "pattern_analysis", points, start, end)
        pattern_chart_paths = _cached_report_value(
            deps, "pattern_chart_paths", points, start, end
        ) or {}
        if cached is None or not pattern_chart_paths:
            patterns = analyze_patterns_impl(
                deps, points=points, start=start, end=end, report_charts=True
            )
            if patterns["status"] != "ok":
                return patterns
            cached = _result_frame(patterns, "table")
            pattern_chart_paths = deepcopy(patterns.get("data", {}).get("curve_images", {}))
            summaries.append(patterns["summary"])
        tables["pattern_analysis"] = cached

    if wants_risk:
        window_events = tables.get("rainfall_events", pd.DataFrame())
        event_id_column = "source_event_id" if time_range and "source_event_id" in window_events.columns else "event_id"
        available_ids = (
            set(pd.to_numeric(window_events.get(event_id_column), errors="coerce").dropna().astype(int).tolist())
            if not window_events.empty and event_id_column in window_events.columns
            else set()
        )
        if wants_rainy_risk and not selected_event_ids:
            return needs_input(
                "event_ids",
                "请选择报告雨天风险所使用的降雨场次编号。",
                summary="默认全套报告包含雨天风险，需要先选择降雨场次。",
                options=_event_options(window_events),
            )
        risk_event_ids = list(selected_event_ids)
        public_event_ids = list(selected_event_ids)
        source_to_local: dict[int, int] = {}
        if time_range and "source_event_id" in window_events.columns:
            local_to_source = {
                int(local): int(source)
                for local, source in zip(window_events["event_id"], window_events["source_event_id"])
            }
            source_to_local = {source: local for local, source in local_to_source.items()}
            selected_set = set(selected_event_ids)
            if selected_set and not selected_set.issubset(available_ids) and selected_set.issubset(local_to_source):
                risk_event_ids = [local_to_source[event_id] for event_id in selected_event_ids]
            public_event_ids = [source_to_local.get(event_id, event_id) for event_id in risk_event_ids]
        outside = sorted(set(risk_event_ids) - available_ids)
        if wants_rainy_risk and time_range and outside:
            return error(f"降雨场次 {outside} 不在报告时间窗 [{start or '不限'}, {end or '不限'}] 内。")
        dry_analysis = _cached_report_frame(deps, "dry_analysis", points, start, end) if wants_dry_risk else pd.DataFrame()
        dry_risk = _cached_report_frame(deps, "dry_risk", points, start, end) if wants_dry_risk else pd.DataFrame()
        rainy_risk = (
            _cached_report_frame(deps, "rainy_overflow_risk", points, start, end, risk_event_ids)
            if wants_rainy_risk
            else pd.DataFrame()
        )
        missing_dry = wants_dry_risk and (dry_analysis is None or dry_risk is None)
        missing_rainy = wants_rainy_risk and rainy_risk is None
        if missing_dry or missing_rainy:
            scope = "all" if missing_dry and missing_rainy else "dry" if missing_dry else "rainy"
            risk = assess_risk_impl(
                deps,
                scope=scope,
                event_ids=risk_event_ids if scope in {"all", "rainy"} else None,
                points=points,
                start=start,
                end=end,
            )
            if risk["status"] != "ok":
                return risk
            if missing_dry:
                dry_analysis = _result_frame(risk, "dry_analysis")
                dry_risk = _result_frame(risk, "dry_risk")
            if missing_rainy:
                rainy_risk = _result_frame(risk, "rainy_risk")
            summaries.append(risk["summary"])
        if wants_dry_risk:
            tables["dry_analysis"] = dry_analysis if dry_analysis is not None else pd.DataFrame()
            tables["dry_risk"] = dry_risk if dry_risk is not None else pd.DataFrame()
        if wants_rainy_risk:
            public_rainy_risk = rainy_risk.copy() if rainy_risk is not None else pd.DataFrame()
            if source_to_local and "event_id" in public_rainy_risk.columns:
                public_rainy_risk["event_id"] = public_rainy_risk["event_id"].map(
                    lambda value: source_to_local.get(int(value), int(value)) if pd.notna(value) else value
                )
            tables["rainy_overflow_risk"] = public_rainy_risk
        if wants_rainy_risk and tables["rainy_overflow_risk"].empty:
            return error("雨天风险计算结果为空，拒绝生成带空雨天风险章节的报告。")

    params = {
        "points": points or [],
        "start": start,
        "end": end,
        "sections": report_sections,
        "event_ids": public_event_ids if wants_risk else selected_event_ids,
    }

    def work() -> tuple[str, dict[str, Any]]:
        output_file = deps.paths.outputs / _report_filename(points, deps, start, end)
        combined_file = _report_combined_workbook_path(output_file)
        result = build_report(
            output_file,
            "排水监测数据分析报告",
            summaries,
            template_file=deps.paths.report_template_file,
            analysis_tables=tables,
            site_info_file=deps.paths.site_info_file,
            outputs_dir=deps.paths.outputs,
            sections=report_sections,
            has_rainfall_data=not tables.get("rainfall_daily", pd.DataFrame()).empty,
            point_ids=points,
            start=report_start,
            end=report_end,
            rainfall_chart_paths=rainfall_chart_paths,
            pattern_chart_paths=pattern_chart_paths,
            artifact_scope=_range_result_prefix(points, deps, start, end),
        )
        combined_sheets = _write_report_combined_workbook(deps, tables, combined_file)
        result["report_combined_sheets"] = combined_sheets
        if combined_sheets:
            result["result_destinations"] = [
                {
                    "kind": "combined_xlsx",
                    "path": _rel(deps, combined_file),
                    "sheet": None,
                }
            ]
        summary = (
            f"报告生成完成：{_rel(deps, output_file)}，范围点位 {points or ['全网']}，"
            f"时间窗 [{start or '全时段'}, {end or '全时段'}]。"
        )
        if report_start or report_end:
            summary += f" 报告正文按实际有效数据范围 [{report_start or '不限'}, {report_end or '不限'}] 填充。"
        if wants_rainy_risk:
            summary += f" 窗口内降雨场次编号 {public_event_ids}。"
        return summary, result

    return _run(deps, "generate_report", work, params=params, use_cache=False)


def _result_frame(result: ToolResult, key: str) -> pd.DataFrame:
    value = result.get("data", {}).get(key, [])
    return pd.DataFrame(value)


def _resolved_report_time_range(deps: AgentDeps, start: str | None, end: str | None) -> list[str]:
    rain = io.load_rain(root=deps.paths.root)
    resolved_start = start or rain["timestamp"].min().strftime("%Y-%m-%d %H:%M:%S")
    resolved_end = end or rain["timestamp"].max().strftime("%Y-%m-%d %H:%M:%S")
    return [resolved_start, resolved_end]


def _report_filename(
    points: list[str] | None,
    deps: AgentDeps,
    start: str | None,
    end: str | None,
) -> str:
    points = _normalize_point_scope(points, deps)
    if points is None:
        point_part = "全网"
    elif len(points) == 1:
        point_part = _safe_filename_part(points[0])[:24]
    else:
        first = _safe_filename_part(sorted(points, key=str)[0])[:12]
        point_part = f"{len(points)}点_{first}等"
    filename = f"{point_part}_{_time_result_prefix(start, end)}_分析报告.docx"
    if len(filename) > 80:
        filename = f"{point_part[:20]}_{_time_result_prefix(start, end)[:36]}_分析报告.docx"
    return filename
