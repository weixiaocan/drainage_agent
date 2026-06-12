from __future__ import annotations

import pickle
import traceback
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from agent.deps import AgentDeps
from agent.tools.manifest import load_manifest, record_result
from agent.types import ToolResult, error, needs_input, ok
from analysis import io
from analysis.dry_curves import build_dry_curves, dry_statistics
from analysis.event_response import analyze_event_response
from analysis.patterns import analyze_patterns
from analysis.rainfall import analyze_rainfall
from analysis.rdii import analyze_rdii
from analysis.reporting import build_report
from analysis.risk import assess_risk
from analysis.stats import check_data, query_stats


def _rel(deps: AgentDeps, path: Path) -> str:
    try:
        return path.resolve().relative_to(deps.paths.root).as_posix()
    except ValueError:
        return str(path)


def _artifacts(deps: AgentDeps) -> list[str]:
    if not deps.paths.outputs.exists():
        return []
    return [_rel(deps, p) for p in deps.paths.outputs.rglob("*") if p.is_file()]


def _write_sheet(path: Path, sheet_name: str, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if path.exists() else "w"
    if mode == "a":
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    else:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def _run(
    deps: AgentDeps,
    tool_name: str,
    fn: Callable[[], tuple[str, dict[str, Any]]],
    params: dict[str, Any] | None = None,
) -> ToolResult:
    try:
        deps.paths.outputs.mkdir(parents=True, exist_ok=True)
        summary, data = fn()
        artifacts = _artifacts(deps)
        record_result(deps, tool_name, artifacts, params=params)
        return ok(summary, artifacts=artifacts, **data)
    except Exception as exc:
        deps.logger.exception("%s failed", tool_name)
        return error(f"{tool_name} 执行失败: {exc}", traceback=traceback.format_exc(limit=8))


def _manifest_stale(deps: AgentDeps, tool_name: str) -> bool:
    manifest = load_manifest(deps)
    item = manifest.get("results", {}).get(tool_name)
    if not item:
        return True
    from agent.tools.manifest import data_fingerprint

    return item.get("data_fingerprint") != data_fingerprint(deps)["digest"]


def _intermediate_dir(deps: AgentDeps) -> Path:
    path = deps.paths.outputs / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _curves_path(deps: AgentDeps) -> Path:
    return _intermediate_dir(deps) / "dry_curves.pkl"


def _save_curves(deps: AgentDeps, curves: dict[str, pd.DataFrame]) -> None:
    with _curves_path(deps).open("wb") as fh:
        pickle.dump(curves, fh)


def _load_curves(deps: AgentDeps) -> dict[str, pd.DataFrame]:
    path = _curves_path(deps)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return pickle.load(fh)


def _load_event_table(deps: AgentDeps) -> pd.DataFrame:
    if not deps.paths.combined_xlsx.exists():
        return pd.DataFrame()
    try:
        return pd.read_excel(deps.paths.combined_xlsx, sheet_name="场次降雨统计")
    except Exception:
        return pd.DataFrame()


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
    if events.empty:
        rain = io.load_rain(root=deps.paths.root)
        events = analyze_rainfall(rain)["events"]
        if not events.empty:
            _write_sheet(deps.paths.combined_xlsx, "场次降雨统计", events)
    return needs_input(
        "event_ids",
        "请从 options 中选择降雨场次编号；也可以回复“只出旱天报告”。",
        summary="需要先选择降雨场次编号，才能分析雨天响应、RDII 或雨天风险。",
        options=_event_options(events),
    )


def check_data_impl(deps: AgentDeps, points: list[str] | None = None) -> ToolResult:
    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, clean=False, root=deps.paths.root)
        stats_df = check_data(flow)
        _write_sheet(deps.paths.combined_xlsx, "数据体检", stats_df)
        avg = float(stats_df["collection_rate"].mean()) if not stats_df.empty else 0.0
        summary = f"数据体检完成：处理 {len(stats_df)} 个点位，平均收集率 {avg:.1%}。"
        return summary, {"table": stats_df.to_dict(orient="records")}

    return _run(deps, "check_data", work, params={"points": points or []})


def query_stats_impl(
    deps: AgentDeps,
    points: list[str] | None = None,
    time_range: list[str] | None = None,
    dry_only: bool = True,
    metrics: list[str] | None = None,
    aggs: list[str] | None = None,
    clean: bool = True,
) -> ToolResult:
    params = {
        "points": points or [],
        "time_range": time_range or [],
        "dry_only": dry_only,
        "metrics": metrics or ["流量", "液位", "流速"],
        "aggs": aggs or ["均值", "最大", "最小"],
        "clean": clean,
    }

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, time_range=time_range, clean=clean, dry_only=dry_only, root=deps.paths.root)
        table = query_stats(flow, metrics=metrics, aggs=aggs)
        _write_sheet(deps.paths.combined_xlsx, "聚合统计", table)
        clean_summary = io.last_clean_report().summary()
        summary = f"聚合统计完成：输出 {len(table)} 个点位。{clean_summary}"
        return summary, {"table": table.to_dict(orient="records")}

    return _run(deps, "query_stats", work, params=params)


def analyze_rainfall_impl(deps: AgentDeps, time_range: list[str] | None = None, output: str = "all", rainfall_gap_hours: int = 12) -> ToolResult:
    params = {"time_range": time_range or [], "output": output, "rainfall_gap_hours": rainfall_gap_hours}

    def work() -> tuple[str, dict[str, Any]]:
        rain = io.load_rain(time_range=time_range, root=deps.paths.root)
        result = analyze_rainfall(rain, gap_hours=rainfall_gap_hours)
        if output in {"all", "daily"}:
            _write_sheet(deps.paths.combined_xlsx, "日降雨量统计", result["daily"])
        if output in {"all", "events"}:
            _write_sheet(deps.paths.combined_xlsx, "场次降雨统计", result["events"])
        rainy_days = int(result["daily"]["is_rainy"].sum()) if not result["daily"].empty else 0
        total = float(result["daily"]["rain_mm"].sum()) if not result["daily"].empty else 0.0
        summary = f"降雨分析完成：雨日 {rainy_days} 天，总雨量 {total:.1f} mm，场次 {len(result['events'])} 场。"
        return summary, {key: df.to_dict(orient="records") for key, df in result.items()}

    return _run(deps, "analyze_rainfall", work, params=params)


def _dry_inputs(deps: AgentDeps) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    dry_flow = io.load_flow(clean=True, dry_only=True, root=deps.paths.root)
    stats_df = dry_statistics(dry_flow)
    curves = build_dry_curves(dry_flow)
    _save_curves(deps, curves)
    _write_sheet(deps.paths.combined_xlsx, "旱天分析", stats_df)
    return dry_flow, stats_df, curves


def analyze_patterns_impl(deps: AgentDeps, points: list[str] | None = None, output: str = "all") -> ToolResult:
    params = {"points": points or [], "output": output}

    def work() -> tuple[str, dict[str, Any]]:
        dry_flow = io.load_flow(points=points, clean=True, dry_only=True, root=deps.paths.root)
        result = analyze_patterns(dry_flow)
        patterns = result["patterns"]
        curves = result["curves"]
        _save_curves(deps, curves)
        _write_sheet(deps.paths.combined_xlsx, "排污规律分析", patterns)
        clean_summary = io.last_clean_report().summary()
        summary = f"排污规律分析完成：分析 {len(patterns)} 个点位，生成 {len(curves)} 条旱天曲线。{clean_summary}"
        return summary, {"table": patterns.to_dict(orient="records")}

    return _run(deps, "analyze_patterns", work, params=params)


def analyze_event_response_impl(deps: AgentDeps, event_ids: list[int] | None = None, points: list[str] | None = None) -> ToolResult:
    precheck = _require_event_ids(deps, event_ids)
    if precheck:
        return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    params = {"event_ids": event_ids, "points": points or []}

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, clean=True, dry_only=False, root=deps.paths.root)
        events = _load_event_table(deps)
        response = analyze_event_response(flow, events, event_ids or [])
        _write_sheet(deps.paths.combined_xlsx, "雨天事件响应", response)
        summary = f"事件响应分析完成：场次 {event_ids}，输出 {len(response)} 行统计。{io.last_clean_report().summary()}"
        return summary, {"table": response.to_dict(orient="records")}

    return _run(deps, "analyze_event_response", work, params=params)


def analyze_rdii_impl(deps: AgentDeps, event_ids: list[int] | None = None, points: list[str] | None = None, output: str = "all") -> ToolResult:
    precheck = _require_event_ids(deps, event_ids)
    if precheck:
        return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    params = {"event_ids": event_ids, "points": points or [], "output": output}

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, clean=True, dry_only=False, root=deps.paths.root)
        dry_flow = io.load_flow(points=points, clean=True, dry_only=True, root=deps.paths.root)
        events = _load_event_table(deps)
        table = analyze_rdii(flow, dry_flow, events, event_ids or [])
        _write_sheet(deps.paths.combined_xlsx, "RDII统计", table)
        summary = f"RDII 分析完成：场次 {event_ids}，输出 {len(table)} 行统计。"
        return summary, {"table": table.to_dict(orient="records")}

    return _run(deps, "analyze_rdii", work, params=params)


def assess_risk_impl(deps: AgentDeps, scope: str = "all", event_ids: list[int] | None = None) -> ToolResult:
    scope = {"旱天": "dry", "雨天": "rainy", "全部": "all"}.get(scope, scope)
    if scope in {"rainy", "all"}:
        precheck = _require_event_ids(deps, event_ids)
        if precheck:
            return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    params = {"scope": scope, "event_ids": event_ids or []}

    def work() -> tuple[str, dict[str, Any]]:
        dry_flow, dry_stats, _ = _dry_inputs(deps)
        event_table = pd.DataFrame()
        if scope in {"rainy", "all"} and event_ids:
            flow = io.load_flow(clean=True, dry_only=False, root=deps.paths.root)
            event_table = analyze_event_response(flow, _load_event_table(deps), event_ids)
        result = assess_risk(dry_stats, event_table, scope=scope)
        if not result["dry_risk"].empty:
            _write_sheet(deps.paths.combined_xlsx, "旱天风险", result["dry_risk"])
        if not result["rainy_risk"].empty:
            _write_sheet(deps.paths.combined_xlsx, "雨天溢流风险", result["rainy_risk"])
        summary = f"风险评估完成：旱天风险 {len(result['dry_risk'])} 行，雨天风险 {len(result['rainy_risk'])} 行。"
        return summary, {key: df.to_dict(orient="records") for key, df in result.items()}

    return _run(deps, "assess_risk", work, params=params)


def generate_report_impl(deps: AgentDeps, sections: list[str] | None = None, event_ids: list[int] | None = None) -> ToolResult:
    sections = sections or ["数据体检", "聚合统计", "降雨分析", "排污规律", "风险评估"]
    rainy_sections = {"降雨分析", "事件响应", "RDII", "雨天风险"}
    if rainy_sections.intersection(sections):
        precheck = _require_event_ids(deps, event_ids)
        if precheck:
            return precheck
    params = {"sections": sections, "event_ids": event_ids or deps.session.selected_event_ids}

    def work() -> tuple[str, dict[str, Any]]:
        summaries: list[str] = []
        data_check = check_data_impl(deps)
        summaries.append(data_check["summary"])
        stats = query_stats_impl(deps)
        summaries.append(stats["summary"])
        if "降雨分析" in sections:
            rain = analyze_rainfall_impl(deps)
            summaries.append(rain["summary"])
        if "排污规律" in sections:
            patterns = analyze_patterns_impl(deps)
            summaries.append(patterns["summary"])
        if "风险评估" in sections:
            risk = assess_risk_impl(deps, scope="dry")
            summaries.append(risk["summary"])
        output_file = deps.paths.outputs / "分析报告.docx"
        result = build_report(output_file, "排水监测数据分析报告", summaries)
        summary = f"报告生成完成：{_rel(deps, output_file)}，写入 {result['stats']['paragraphs']} 段摘要。"
        return summary, result

    return _run(deps, "generate_report", work, params=params)
