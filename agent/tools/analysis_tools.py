"""Analysis tools: data check, rainfall, event response, RDII, dry patterns and risk."""

from __future__ import annotations

import calendar
import re
from pathlib import Path
from typing import Any

import pandas as pd

from agent.deps import AgentDeps
from analysis.exports import save_rainfall_png_charts, save_rdii_curve_pngs
from agent.types import FilterConfirmationRequired, ToolResult, error, needs_input
from analysis import io
from analysis.modules.dry_curves import build_dry_curves, dry_statistics
from analysis.modules.event_response import analyze_event_response
from analysis.modules.patterns import analyze_patterns
from analysis.modules.rainfall import analyze_rainfall
from analysis.modules.rdii import analyze_rdii
from analysis.modules.risk import assess_risk
from analysis.modules.stats import check_data
from agent.tools.filter_tool import _confirmed_filter_result_path, data_filter_impl
from agent.tools.tool_support import (
    _add_rainfall_excel_charts,
    _analysis_assets_dir,
    _build_tool_llm_client,
    _range_result_prefix,
    _rel,
    _remove_sheet,
    _route_table_result,
    _run,
    _save_curves,
    _save_partial_pattern_curve_png,
    _save_partial_rdii_curve_png,
    _save_pattern_curve_pngs,
    _save_rdii_curves,
    is_full_network,
)


def _load_event_table(deps: AgentDeps) -> pd.DataFrame:
    rain = io.load_rain(root=deps.paths.root)
    return analyze_rainfall(rain)["events"]


def _event_options(events: pd.DataFrame) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for _, row in events.iterrows():
        options.append(
            {
                "event_id": int(row["event_id"]),
                "label": f"场次{int(row['event_id'])}: {row['start_time']} 至 {row['end_time']}，总雨量 {float(row['total_rain_mm']):.1f} mm",
            }
        )
    return options


def _require_event_ids(deps: AgentDeps, event_ids: list[int] | None) -> ToolResult | None:
    if event_ids:
        deps.session.selected_event_ids = event_ids
        return None
    if deps.session.selected_event_ids:
        return None
    events = _load_event_table(deps)
    return needs_input(
        "event_ids",
        "请从 options 中选择降雨场次编号；也可以回复“只出旱天报告”。",
        summary="需要先选择降雨场次编号，才能分析雨天响应、RDII 或雨天风险。",
        options=_event_options(events),
    )


def _event_data_coverage(
    deps: AgentDeps,
    event_ids: list[int],
    points: list[str] | None = None,
    delay_hours: float = 12.0,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[dict[str, str]]]:
    flow = io.load_flow(points=points, root=deps.paths.root)
    events = _load_event_table(deps)

    wanted = {int(event_id) for event_id in event_ids}
    selected_events = events[events["event_id"].astype(int).isin(wanted)].copy() if not events.empty else events
    requested_points = [str(point) for point in points] if points else sorted(flow["point_id"].astype(str).unique())
    covered: list[str] = []
    excluded: list[dict[str, str]] = []
    for point_id in requested_points:
        point_flow = flow[flow["point_id"].astype(str) == point_id]
        has_coverage = False
        for _, event in selected_events.iterrows():
            start = pd.to_datetime(event.get("start_time"), errors="coerce")
            end = pd.to_datetime(event.get("end_time"), errors="coerce")
            if pd.isna(start) or pd.isna(end):
                continue
            end = end + pd.Timedelta(hours=delay_hours)
            if not point_flow[(point_flow["timestamp"] >= start) & (point_flow["timestamp"] <= end)].empty:
                has_coverage = True
                break
        if has_coverage:
            covered.append(point_id)
        else:
            excluded.append({"point_id": point_id, "reason": "该时段/该点位无数据，无法分析"})

    covered_flow = flow[flow["point_id"].astype(str).isin(covered)].copy()
    return covered_flow, events, covered, excluded


def _coverage_guard_result(
    deps: AgentDeps,
    event_ids: list[int],
    covered: list[str],
    excluded: list[dict[str, str]],
) -> ToolResult | None:
    if covered:
        return None
    deps.session.unavailable_event_ids = sorted(
        set(deps.session.unavailable_event_ids).union(event_ids)
    )
    point_labels = [item["point_id"] for item in excluded] or ["全部点位"]
    return needs_input(
        "data_coverage",
        "请选择覆盖该时段的点位或其他降雨事件。",
        summary=f"该时段/该点位无数据，无法分析：场次 {event_ids}，点位 {point_labels}。",
        options=excluded,
    )


def _window_bounds(start: str | None, end: str | None) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else None
    if end_ts is not None and isinstance(end, str) and len(end.strip()) <= 10:
        end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise ValueError("start must be earlier than or equal to end")
    return start_ts, end_ts


def _window_data_coverage(
    deps: AgentDeps,
    points: list[str] | None,
    start: str | None,
    end: str | None,
) -> tuple[pd.DataFrame, list[str], list[dict[str, str]], dict[str, str | None]]:
    flow = _load_filtered_dry_flow(deps, points=points)
    start_ts, end_ts = _window_bounds(start, end)
    requested_points = [str(point) for point in points] if points else sorted(flow["point_id"].astype(str).unique())
    covered: list[str] = []
    excluded: list[dict[str, str]] = []
    frames: list[pd.DataFrame] = []
    for point_id in requested_points:
        point_flow = flow[flow["point_id"].astype(str) == point_id]
        if start_ts is not None:
            point_flow = point_flow[point_flow["timestamp"] >= start_ts]
        if end_ts is not None:
            point_flow = point_flow[point_flow["timestamp"] <= end_ts]
        if point_flow.empty:
            excluded.append({"point_id": point_id, "reason": "该时段/该点位无数据，无法分析"})
            continue
        covered.append(point_id)
        frames.append(point_flow)

    window_flow = pd.concat(frames, ignore_index=True) if frames else flow.iloc[0:0].copy()
    actual_start = window_flow["timestamp"].min() if not window_flow.empty else None
    actual_end = window_flow["timestamp"].max() if not window_flow.empty else None
    coverage = {
        "requested_start": str(start) if start is not None else None,
        "requested_end": str(end) if end is not None else None,
        "actual_start": actual_start.isoformat(sep=" ") if actual_start is not None else None,
        "actual_end": actual_end.isoformat(sep=" ") if actual_end is not None else None,
    }
    return window_flow.reset_index(drop=True), covered, excluded, coverage


def _window_coverage_guard_result(
    covered: list[str],
    excluded: list[dict[str, str]],
    start: str | None,
    end: str | None,
) -> ToolResult | None:
    if covered:
        return None
    point_labels = [item["point_id"] for item in excluded] or ["全部点位"]
    return needs_input(
        "data_coverage",
        "请选择有数据覆盖的时间窗或点位。",
        summary=f"时间窗 [{start or '不限'}, {end or '不限'}] 内点位 {point_labels} 无数据覆盖，无法分析。",
        options=excluded,
    )


def _window_coverage_note(coverage: dict[str, str | None], excluded: list[dict[str, str]]) -> str:
    note = f"；实际分析范围 [{coverage['actual_start']}, {coverage['actual_end']}]"
    if excluded:
        note += f"；剔除无覆盖点位 {[item['point_id'] for item in excluded]}"
    return note


def _ensure_filter_result(deps: AgentDeps) -> Path:
    confirmed = _confirmed_filter_result_path(deps)
    if confirmed is not None:
        return confirmed
    result = data_filter_impl(deps)
    if result.get("status") == "needs_confirmation":
        raise FilterConfirmationRequired(result, "data_filter", {})
    if result.get("status") != "ok":
        raise RuntimeError(result.get("summary") or "data_filter failed")
    confirmed = _confirmed_filter_result_path(deps)
    return confirmed or deps.paths.filter_result


def _load_filtered_dry_flow(
    deps: AgentDeps,
    points: list[str] | None = None,
    time_range: list[str] | None = None,
) -> pd.DataFrame:
    if (
        deps.filter_baselines is not None
        and deps.current_project_id is not None
        and deps.current_batch_id is not None
    ):
        flow = deps.filter_baselines.load_flow(
            deps.current_project_id, deps.current_batch_id
        )
        if points:
            wanted = {str(point) for point in points}
            flow = flow[flow["point_id"].astype(str).isin(wanted)]
        if time_range:
            start, end = map(pd.to_datetime, time_range)
            flow = flow[
                (flow["timestamp"] >= start) & (flow["timestamp"] <= end)
            ]
        return flow.reset_index(drop=True)
    filter_result = _ensure_filter_result(deps)
    return io.load_flow_by_filter_result(filter_result, points=points, time_range=time_range, root=deps.paths.root)


def check_data_impl(
    deps: AgentDeps,
    points: list[str] | None = None,
    export: bool = False,
    start: str | None = None,
    end: str | None = None,
    force_rerun: bool = False,
) -> ToolResult:
    start, end = _resolve_implicit_flow_year(deps, start, end)
    windowed = start is not None or end is not None

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, root=deps.paths.root)
        coverage = None
        if windowed:
            start_ts, end_ts = _window_bounds(start, end)
            if start_ts is not None:
                flow = flow[flow["timestamp"] >= start_ts]
            if end_ts is not None:
                flow = flow[flow["timestamp"] <= end_ts]
            coverage = {
                "requested_start": start,
                "requested_end": end,
                "actual_start": flow["timestamp"].min().isoformat(sep=" ") if not flow.empty else None,
                "actual_end": flow["timestamp"].max().isoformat(sep=" ") if not flow.empty else None,
            }
        stats_df = check_data(flow)
        if windowed and not stats_df.empty:
            effective_start = start_ts or flow["timestamp"].min()
            effective_end = end_ts or flow["timestamp"].max()
            expected = max(int((effective_end - effective_start) / pd.Timedelta(minutes=1)) + 1, 1)
            stats_df["monitoring_days"] = max(int((expected + 1439) // 1440), 1)
            stats_df["theoretical_count"] = expected
            stats_df["collection_rate"] = (stats_df["record_count"] / expected).clip(upper=1.0)
        destination = _route_table_result(
            deps, stats_df, "数据收集率统计", points, export, start=start, end=end
        )
        if destination["kind"] == "combined_xlsx":
            _remove_sheet(deps.paths.combined_xlsx, "数据体检")
        avg = float(stats_df["collection_rate"].mean()) if not stats_df.empty else 0.0
        summary = f"数据收集率统计完成：处理 {len(stats_df)} 个点位，平均收集率 {avg:.1%}。"
        data = {"table": stats_df.to_dict(orient="records"), "result_destinations": [destination]}
        if coverage is not None:
            data["window_coverage"] = coverage
        return summary, data

    return _run(
        deps,
        "check_data",
        work,
        params={"points": points or [], "export": export, "start": start, "end": end},
    )


def _rainfall_window_bounds(time_range: list[str]) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_text, end_text = time_range
    start = pd.to_datetime(start_text)
    end = pd.to_datetime(end_text)
    if isinstance(end_text, str) and len(end_text.strip()) <= 10:
        end = end + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return start, end


def _filter_rainfall_result_to_window(
    rain: pd.DataFrame,
    result: dict[str, pd.DataFrame],
    time_range: list[str],
) -> dict[str, pd.DataFrame]:
    start, end = _rainfall_window_bounds(time_range)
    window_rain = rain[(rain["timestamp"] >= start) & (rain["timestamp"] <= end)].copy()
    window_daily = analyze_rainfall(window_rain)["daily"]

    events = result["events"].copy()
    if not events.empty:
        event_starts = pd.to_datetime(events["start_time"], errors="coerce")
        event_ends = pd.to_datetime(events["end_time"], errors="coerce")
        events = events[(event_ends >= start) & (event_starts <= end)].copy()
    return {"daily": window_daily, "events": events.reset_index(drop=True)}


def _resolve_implicit_rainfall_year(
    deps: AgentDeps,
    rain: pd.DataFrame,
    time_range: list[str] | None,
) -> list[str] | None:
    """Use the dataset year when the user's date omitted a calendar year."""
    if not time_range:
        return time_range
    return _resolve_implicit_dataset_year(deps, rain["timestamp"], time_range)


def _resolve_implicit_dataset_year(
    deps: AgentDeps,
    timestamps: pd.Series,
    values: list[str],
) -> list[str]:
    """Map model-supplied placeholder years to the unique source-data year."""
    if re.search(r"\b(?:19|20)\d{2}\b", deps.session.current_user_prompt or ""):
        return values
    parsed_timestamps = pd.to_datetime(timestamps, errors="coerce").dropna()
    years = parsed_timestamps.dt.year.unique()
    if len(years) != 1:
        return values
    dataset_year = int(years[0])
    resolved: list[str] = []
    for value in values:
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            return values
        if timestamp.year != dataset_year:
            last_day = calendar.monthrange(dataset_year, timestamp.month)[1]
            timestamp = timestamp.replace(
                year=dataset_year,
                day=min(timestamp.day, last_day),
            )
        resolved.append(
            value if timestamp.year == dataset_year and str(value).startswith(str(dataset_year)) else str(timestamp)
        )
    return resolved


def _resolve_implicit_flow_year(
    deps: AgentDeps,
    start: str | None,
    end: str | None,
) -> tuple[str | None, str | None]:
    values = [value for value in (start, end) if value is not None]
    if not values:
        return start, end
    flow = io.load_flow(root=deps.paths.root)
    resolved = iter(_resolve_implicit_dataset_year(deps, flow["timestamp"], values))
    return (
        next(resolved) if start is not None else None,
        next(resolved) if end is not None else None,
    )


def _monitoring_covered_rainfall_events(
    deps: AgentDeps, events: pd.DataFrame, delay_hours: float = 12.0
) -> tuple[list[int], int | None]:
    flow = io.load_flow(root=deps.paths.root)
    covered_rows: list[pd.Series] = []
    for _, event in events.iterrows():
        start = pd.to_datetime(event.get("start_time"), errors="coerce")
        end = pd.to_datetime(event.get("end_time"), errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        window = flow[
            (flow["timestamp"] >= start)
            & (flow["timestamp"] <= end + pd.Timedelta(hours=delay_hours))
        ]
        if not window.empty:
            covered_rows.append(event)
    covered_ids = [int(row["event_id"]) for row in covered_rows]
    if not covered_rows:
        return covered_ids, None
    largest = max(
        covered_rows,
        key=lambda row: float(row.get("total_rain_mm") or 0.0),
    )
    return covered_ids, int(largest["event_id"])


def analyze_rainfall_impl(
    deps: AgentDeps,
    time_range: list[str] | None = None,
    output: str = "all",
    rainfall_gap_hours: int = 12,
    export: bool = False,
) -> ToolResult:
    rain = io.load_rain(root=deps.paths.root)
    resolved_time_range = _resolve_implicit_rainfall_year(deps, rain, time_range)
    params = {
        "time_range": resolved_time_range or [],
        "output": output,
        "rainfall_gap_hours": rainfall_gap_hours,
        "export": export,
    }

    def work() -> tuple[str, dict[str, Any]]:
        result = analyze_rainfall(rain, gap_hours=rainfall_gap_hours)
        if resolved_time_range:
            result = _filter_rainfall_result_to_window(rain, result, resolved_time_range)
        range_start = resolved_time_range[0] if resolved_time_range else None
        range_end = resolved_time_range[1] if resolved_time_range else None
        chart_paths: dict[str, str] = {}
        destinations: list[dict[str, Any]] = []
        if output in {"all", "daily"}:
            destination = _route_table_result(
                deps,
                result["daily"],
                "降雨概况",
                None,
                export,
                start=range_start,
                end=range_end,
            )
            destinations.append(destination)
            if destination["kind"] == "combined_xlsx":
                _remove_sheet(deps.paths.combined_xlsx, "日降雨量统计")
                _add_rainfall_excel_charts(deps.paths.combined_xlsx, result["daily"])
            chart_paths = save_rainfall_png_charts(
                result["daily"],
                _analysis_assets_dir(deps) / "降雨分析图",
                _range_result_prefix(None, deps, range_start, range_end),
            )
        if output in {"all", "events"}:
            destination = _route_table_result(
                deps,
                result["events"],
                "降雨场次分析",
                None,
                export,
                start=range_start,
                end=range_end,
            )
            destinations.append(destination)
            if destination["kind"] == "combined_xlsx":
                _remove_sheet(deps.paths.combined_xlsx, "场次降雨统计")
        rainy_days = int(result["daily"]["is_rainy"].sum()) if not result["daily"].empty else 0
        total = float(result["daily"]["rain_mm"].sum()) if not result["daily"].empty else 0.0
        summary = f"降雨分析完成：雨日 {rainy_days} 天，总雨量 {total:.1f} mm，场次 {len(result['events'])} 场。"
        covered_event_ids, largest_covered_event_id = _monitoring_covered_rainfall_events(
            deps, result["events"]
        )
        if covered_event_ids:
            summary += (
                f" 与流量监测共同覆盖的场次为 {covered_event_ids}，"
                f"其中总雨量最大的是场次 {largest_covered_event_id}。"
            )
        else:
            summary += " 当前没有与流量监测时间重叠的降雨场次。"
        data = {key: df.to_dict(orient="records") for key, df in result.items()}
        data["has_rainfall_coverage"] = bool(rainy_days or not result["events"].empty)
        data["resolved_time_range"] = resolved_time_range or []
        data["monitoring_covered_event_ids"] = covered_event_ids
        data["largest_monitoring_covered_event_id"] = largest_covered_event_id
        data["chart_paths"] = chart_paths
        data["result_destinations"] = destinations
        return summary, data

    return _run(deps, "analyze_rainfall", work, params=params)


def _dry_inputs(deps: AgentDeps, points: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    dry_flow = _load_filtered_dry_flow(deps, points=points)
    stats_df = dry_statistics(dry_flow, io.load_sites(root=deps.paths.root))
    curves = build_dry_curves(dry_flow)
    _save_curves(deps, curves)
    return dry_flow, stats_df, curves


def analyze_patterns_impl(
    deps: AgentDeps,
    points: list[str] | None = None,
    output: str = "all",
    export: bool = False,
    start: str | None = None,
    end: str | None = None,
    report_charts: bool = False,
) -> ToolResult:
    start, end = _resolve_implicit_flow_year(deps, start, end)
    params = {
        "points": points or [],
        "start": start,
        "end": end,
        "output": output,
        "export": export,
        "report_charts": report_charts,
    }
    windowed = start is not None or end is not None
    coverage: dict[str, str | None] | None = None
    covered: list[str] = []
    excluded: list[dict[str, str]] = []
    window_flow = pd.DataFrame()
    if windowed:
        try:
            window_flow, covered, excluded, coverage = _window_data_coverage(deps, points, start, end)
        except ValueError as exc:
            return error(str(exc))
        coverage_failure = _window_coverage_guard_result(covered, excluded, start, end)
        if coverage_failure:
            return coverage_failure

    def work() -> tuple[str, dict[str, Any]]:
        dry_flow = window_flow if windowed else _load_filtered_dry_flow(deps, points=points)
        llm_client = _build_tool_llm_client(deps)
        result = analyze_patterns(dry_flow, llm_client=llm_client)
        patterns = result["patterns"]
        curves = result["curves"]
        _save_curves(deps, curves)
        scope_prefix = _range_result_prefix(points, deps, start, end)
        if is_full_network(points, deps) or report_charts:
            curve_images = _save_pattern_curve_pngs(
                curves,
                dry_flow,
                _analysis_assets_dir(deps) / "特征曲线图",
                scope_prefix,
            )
        elif export:
            curve_images = _save_partial_pattern_curve_png(
                curves, dry_flow, points, _analysis_assets_dir(deps), scope_prefix
            )
        else:
            curve_images = {}
        destination = _route_table_result(
            deps, patterns, "排污规律分析", points, export, start=start, end=end
        )
        llm_note = "描述由 LLM JSON 生成并经规则后处理" if llm_client is not None else "未配置 LLM，描述使用规则兜底生成"
        summary = (
            f"排污规律分析完成：分析 {len(patterns)} 个点位，生成 {len(curves)} 条旱天曲线。"
            f"{llm_note}。基于筛选结果 {_rel(deps, deps.paths.filter_result)}。"
        )
        if coverage is not None:
            summary += _window_coverage_note(coverage, excluded)
        data = {
            "table": patterns.to_dict(orient="records"),
            "curve_images": curve_images,
            "result_destinations": [destination],
        }
        if windowed:
            data["window_coverage"] = coverage
            data["covered_points"] = covered
            data["excluded_points"] = excluded
        return summary, data

    return _run(deps, "analyze_patterns", work, params=params)


def analyze_event_response_impl(
    deps: AgentDeps,
    event_ids: list[int] | None = None,
    points: list[str] | None = None,
    export: bool = False,
) -> ToolResult:
    precheck = _require_event_ids(deps, event_ids)
    if precheck:
        return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    requested_event_ids = [int(event_id) for event_id in event_ids or []]
    params = {"event_ids": requested_event_ids, "points": points or [], "export": export}

    flow, events, covered, excluded = _event_data_coverage(deps, requested_event_ids, points)
    coverage_failure = _coverage_guard_result(deps, requested_event_ids, covered, excluded)
    if coverage_failure:
        return coverage_failure

    def work() -> tuple[str, dict[str, Any]]:
        response = analyze_event_response(flow, events, requested_event_ids)
        public_response = response
        destination = _route_table_result(deps, public_response, "雨天事件统计", points, export)
        if response.empty:
            deps.session.unavailable_event_ids = sorted(
                set(deps.session.unavailable_event_ids).union(event_ids or [])
            )
            selected = points or ["全部点位"]
            summary = (
                f"雨天事件统计无可用数据：场次 {event_ids} 与点位 {selected} 的监测数据无时间重叠，"
                "无法计算事件响应指标。"
            )
            return summary, {
                "table": [],
                "no_data": True,
                "event_ids": event_ids,
                "points": points or [],
                "result_destinations": [destination],
            }
        excluded_note = f"；剔除无覆盖点位 {[item['point_id'] for item in excluded]}" if excluded else ""
        summary = f"雨天事件统计完成：场次 {event_ids}，输出 {len(response)} 个点位统计{excluded_note}。"
        return summary, {
            "table": public_response.to_dict(orient="records"),
            "no_data": False,
            "covered_points": covered,
            "excluded_points": excluded,
            "result_destinations": [destination],
        }

    return _run(deps, "analyze_event_response", work, params=params)


def analyze_rdii_impl(
    deps: AgentDeps,
    event_ids: list[int] | None = None,
    points: list[str] | None = None,
    output: str = "all",
    export: bool = False,
) -> ToolResult:
    precheck = _require_event_ids(deps, event_ids)
    if precheck:
        return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    requested_event_ids = [int(event_id) for event_id in event_ids or []]
    params = {"event_ids": requested_event_ids, "points": points or [], "output": output, "export": export}

    flow, events, covered, excluded = _event_data_coverage(deps, requested_event_ids, points)
    coverage_failure = _coverage_guard_result(deps, requested_event_ids, covered, excluded)
    if coverage_failure:
        return coverage_failure

    def work() -> tuple[str, dict[str, Any]]:
        dry_flow = _load_filtered_dry_flow(deps, points=covered)
        dry_curves = build_dry_curves(dry_flow)
        _save_curves(deps, dry_curves)
        result = analyze_rdii(flow, dry_curves, events, requested_event_ids)
        table = result["rdii_total"]
        public_table = table
        _save_rdii_curves(deps, result["rdii_curve_data"])
        if table.empty:
            deps.session.unavailable_event_ids = sorted(
                set(deps.session.unavailable_event_ids).union(event_ids or [])
            )
            summary = (
                f"RDII 分析无可用数据：场次 {event_ids} 与点位 {points or ['全部点位']} 的监测数据"
                "无时间重叠，无法计算 RDII。"
            )
            summary += " RDII 总量单位 m³，RDII 曲线单位 L/s。"
            destination = _route_table_result(deps, public_table, "RDII总量统计", points, export)
            return summary, {
                "table": [],
                "chart_paths": {},
                "no_data": True,
                "event_ids": event_ids,
                "units": {"rdii_total": "m³", "rdii_curve": "L/s"},
                "result_destinations": [destination],
            }
        rain = io.load_rain(root=deps.paths.root)
        if is_full_network(points, deps):
            chart_paths = save_rdii_curve_pngs(
                result["rdii_curve_data"],
                rain,
                events,
                _analysis_assets_dir(deps),
                selected_events=requested_event_ids,
            )
        elif export:
            chart_paths = _save_partial_rdii_curve_png(
                result["rdii_curve_data"], points, _analysis_assets_dir(deps), requested_event_ids
            )
        else:
            chart_paths = {}
        destination = _route_table_result(deps, public_table, "RDII总量统计", points, export)
        chart_count = sum(len(point_paths) for point_paths in chart_paths.values())
        excluded_note = f"；剔除无覆盖点位 {[item['point_id'] for item in excluded]}" if excluded else ""
        summary = f"RDII 分析完成：场次 {event_ids}，输出 {len(table)} 行统计，生成 {chart_count} 张 RDII 曲线图{excluded_note}。"
        summary += " RDII 总量单位 m³，RDII 曲线单位 L/s。"
        return summary, {
            "table": public_table.to_dict(orient="records"),
            "chart_paths": chart_paths,
            "no_data": False,
            "units": {"rdii_total": "m³", "rdii_curve": "L/s"},
            "covered_points": covered,
            "excluded_points": excluded,
            "result_destinations": [destination],
        }

    return _run(deps, "analyze_rdii", work, params=params)


def assess_risk_impl(
    deps: AgentDeps,
    scope: str = "all",
    event_ids: list[int] | None = None,
    points: list[str] | None = None,
    export: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> ToolResult:
    start, end = _resolve_implicit_flow_year(deps, start, end)
    scope = {"旱天": "dry", "雨天": "rainy", "全部": "all"}.get(scope, scope)
    if scope in {"rainy", "all"}:
        precheck = _require_event_ids(deps, event_ids)
        if precheck:
            return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    requested_event_ids = [int(event_id) for event_id in event_ids or []]
    params = {
        "scope": scope,
        "event_ids": requested_event_ids,
        "points": points or [],
        "start": start,
        "end": end,
        "export": export,
    }

    windowed = scope in {"dry", "all"} and (start is not None or end is not None)
    dry_window_flow = pd.DataFrame()
    dry_covered: list[str] = []
    dry_excluded: list[dict[str, str]] = []
    window_coverage: dict[str, str | None] | None = None
    if windowed:
        try:
            dry_window_flow, dry_covered, dry_excluded, window_coverage = _window_data_coverage(
                deps, points, start, end
            )
        except ValueError as exc:
            return error(str(exc))
        coverage_failure = _window_coverage_guard_result(dry_covered, dry_excluded, start, end)
        if coverage_failure:
            return coverage_failure

    flow = pd.DataFrame()
    events = pd.DataFrame()
    covered: list[str] = []
    excluded: list[dict[str, str]] = []
    if scope in {"rainy", "all"} and requested_event_ids:
        flow, events, covered, excluded = _event_data_coverage(deps, requested_event_ids, points)
        coverage_failure = _coverage_guard_result(deps, requested_event_ids, covered, excluded)
        if coverage_failure:
            return coverage_failure

    def work() -> tuple[str, dict[str, Any]]:
        if windowed:
            dry_flow = dry_window_flow
            dry_stats = dry_statistics(dry_flow, io.load_sites(root=deps.paths.root))
        else:
            dry_flow, dry_stats, _ = _dry_inputs(deps, points=points)
        sites = io.load_sites(root=deps.paths.root)
        event_table = pd.DataFrame()
        if scope in {"rainy", "all"} and requested_event_ids:
            event_table = analyze_event_response(flow, events, requested_event_ids)
        result = assess_risk(
            dry_stats,
            event_table,
            scope=scope,
            sites=sites,
            flow=flow,
            events=events,
            event_ids=requested_event_ids,
        )
        destinations = [
            _route_table_result(
                deps, dry_stats, "旱天分析", points, export, start=start, end=end
            )
        ]
        if not result["dry_risk"].empty:
            destinations.append(
                _route_table_result(
                    deps, result["dry_risk"], "旱天风险", points, export, start=start, end=end
                )
            )
        if not result["rainy_risk"].empty:
            destinations.append(
                _route_table_result(
                    deps,
                    result["rainy_risk"],
                    "雨天溢流风险",
                    points,
                    export,
                    start=start,
                    end=end,
                )
            )
        excluded_note = f"；雨天分析剔除无覆盖点位 {[item['point_id'] for item in excluded]}" if excluded else ""
        summary = f"风险评估完成：旱天风险 {len(result['dry_risk'])} 行，雨天风险 {len(result['rainy_risk'])} 行{excluded_note}。"
        if window_coverage is not None:
            summary += _window_coverage_note(window_coverage, dry_excluded)
        data = {key: df.to_dict(orient="records") for key, df in result.items()}
        data["dry_analysis"] = dry_stats.to_dict(orient="records")
        data["covered_points"] = covered
        data["excluded_points"] = excluded
        if windowed:
            data["window_coverage"] = window_coverage
            data["window_covered_points"] = dry_covered
            data["window_excluded_points"] = dry_excluded
        data["result_destinations"] = destinations
        return summary, data

    return _run(deps, "assess_risk", work, params=params)
