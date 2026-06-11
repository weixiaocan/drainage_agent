from __future__ import annotations

import logging
import pickle
import traceback
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from agent.deps import AgentDeps
from agent.tools.manifest import load_manifest, record_result
from agent.types import ToolResult, blocked, error, ok
from pipeline.core.config import Config
from pipeline.modules.data_filter.runner import run as data_filter_run
from pipeline.modules.data_stats.runner import run as data_stats_run
from pipeline.modules.dry_analysis.runner import run as dry_analysis_run
from pipeline.modules.event_stats.runner import run as event_stats_run
from pipeline.modules.pattern_analysis.runner import run as pattern_analysis_run
from pipeline.modules.rainfall_analysis.runner import run as rainfall_analysis_run
from pipeline.modules.rdii_analysis.runner import run as rdii_analysis_run
from pipeline.modules.report_assembler.runner import run as report_assembler_run
from pipeline.modules.risk_analysis.runner import run as risk_analysis_run


def _rel(deps: AgentDeps, path: Path) -> str:
    try:
        return path.resolve().relative_to(deps.paths.root).as_posix()
    except ValueError:
        return str(path)


def _logger(deps: AgentDeps, name: str) -> logging.Logger:
    return deps.logger.getChild(name)


def _build_config(
    deps: AgentDeps,
    *,
    event_ids: list[int] | None = None,
    missing_rate_threshold: float = 0.1,
    smooth_window_minutes: int = 20,
    rainfall_gap_hours: int = 12,
) -> Config:
    cfg = Config.for_testing(
        project_root=deps.paths.root,
        output_dir=deps.paths.outputs,
        flow_data_dir=deps.paths.flow_dir,
        rainfall_data_path=deps.paths.rainfall_file,
        site_info_path=deps.paths.site_info_file,
        report_template_path=deps.paths.report_template_file,
        missing_rate_threshold=missing_rate_threshold,
        smooth_window_minutes=smooth_window_minutes,
        llm_enabled=True,
        llm_api_key=deps.settings.api_key or "",
        llm_base_url=deps.settings.base_url or "https://api.deepseek.com",
        llm_model=deps.settings.model,
    )
    cfg._yaml.setdefault("output", {})
    cfg._yaml["output"].update(
        {
            "combined_results_file": "综合分析结果.xlsx",
            "filter_result_file": "筛选结果.xlsx",
            "report_file": "分析报告.docx",
            "charts_dirname": "charts",
        }
    )
    cfg._yaml.setdefault("analysis", {})["smooth_window"] = smooth_window_minutes
    cfg._baseinfo.update(
        {
            "smooth_window_minutes": smooth_window_minutes,
            "rainfall_gap_hours": rainfall_gap_hours,
            "rainfall_delay_hours": 48,
            "selected_rainfall_events": event_ids or deps.session.selected_event_ids,
        }
    )
    return cfg


def _xlsx_sheets(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        with pd.ExcelFile(path) as xls:
            return list(xls.sheet_names)
    except Exception:
        return []


def _has_sheet(deps: AgentDeps, sheet: str) -> bool:
    return sheet in _xlsx_sheets(deps.paths.combined_xlsx)


def _has_sheet_prefix(deps: AgentDeps, prefix: str) -> bool:
    return any(name.startswith(prefix) for name in _xlsx_sheets(deps.paths.combined_xlsx))


DRY_CURVE_ARTIFACTS = {
    "dry_curve_data": "dry_curve_data.pkl",
    "dry_curve_data_workday": "dry_curve_data_workday.pkl",
    "dry_curve_data_weekend": "dry_curve_data_weekend.pkl",
    "day_num": "day_num.pkl",
}


def _intermediate_dir(deps: AgentDeps) -> Path:
    return deps.paths.outputs / "intermediate"


def _dry_curve_artifact_paths(deps: AgentDeps) -> dict[str, Path]:
    base = _intermediate_dir(deps)
    return {key: base / filename for key, filename in DRY_CURVE_ARTIFACTS.items()}


def _save_dry_curve_artifacts(deps: AgentDeps, result: dict[str, Any]) -> None:
    base = _intermediate_dir(deps)
    base.mkdir(parents=True, exist_ok=True)
    for key, path in _dry_curve_artifact_paths(deps).items():
        if key in result:
            with path.open("wb") as fh:
                pickle.dump(result[key], fh)


def _load_dry_curve_artifacts(deps: AgentDeps) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for key, path in _dry_curve_artifact_paths(deps).items():
        if path.exists():
            with path.open("rb") as fh:
                loaded[key] = pickle.load(fh)
    return loaded


def _require_dry_curve_artifacts(deps: AgentDeps) -> ToolResult | None:
    required = _dry_curve_artifact_paths(deps)
    missing = [path.name for key, path in required.items() if key == "dry_curve_data" and not path.exists()]
    if missing:
        return blocked("旱天特征曲线中间产物", "请先调用 run_dry_analysis")
    if _manifest_stale(deps, "run_dry_analysis"):
        return blocked("旱天特征曲线中间产物已过期", "数据已更新，请重新调用 run_dry_analysis")
    return None


def _manifest_stale(deps: AgentDeps, tool_name: str) -> bool:
    manifest = load_manifest(deps)
    item = manifest.get("results", {}).get(tool_name)
    if not item:
        return False
    from agent.tools.manifest import data_fingerprint

    return item.get("data_fingerprint") != data_fingerprint(deps)["digest"]


def _require_file(deps: AgentDeps, path: Path, missing: str, hint: str, tool_name: str | None = None) -> ToolResult | None:
    if not path.exists():
        return blocked(missing, hint)
    if tool_name and _manifest_stale(deps, tool_name):
        return blocked(f"{missing} 已过期", hint)
    return None


def _require_sheet(deps: AgentDeps, sheet: str, missing: str, hint: str, tool_name: str | None = None) -> ToolResult | None:
    if not _has_sheet(deps, sheet):
        return blocked(missing, hint)
    if tool_name and _manifest_stale(deps, tool_name):
        return blocked(f"{missing} 已过期", hint)
    return None


def _artifacts(deps: AgentDeps) -> list[str]:
    if not deps.paths.outputs.exists():
        return []
    return [_rel(deps, p) for p in deps.paths.outputs.rglob("*") if p.is_file()]


def _normalize_choice(value: str, aliases: dict[str, str], default: str) -> str:
    raw = (value or default).strip().lower()
    return aliases.get(raw, raw if raw in set(aliases.values()) else default)


def _run_tool(
    deps: AgentDeps,
    tool_name: str,
    runner: Callable[..., dict[str, Any]],
    cfg: Config,
    summary_fn: Callable[[dict[str, Any]], str],
    *,
    params: dict[str, Any] | None = None,
    runner_kwargs: dict[str, Any] | None = None,
    artifacts_fn: Callable[[AgentDeps, dict[str, Any]], list[str]] | None = None,
    postprocess_fn: Callable[[AgentDeps, dict[str, Any]], None] | None = None,
) -> ToolResult:
    deps.paths.outputs.mkdir(parents=True, exist_ok=True)
    try:
        result = runner(cfg, _logger(deps, tool_name), **(runner_kwargs or {}))
        if postprocess_fn:
            postprocess_fn(deps, result)
        artifacts = artifacts_fn(deps, result) if artifacts_fn else _artifacts(deps)
        record_result(deps, tool_name, artifacts, params=params)
        return ok(summary_fn(result), artifacts=artifacts, raw_keys=sorted(result.keys()))
    except Exception as exc:
        deps.logger.exception("%s failed", tool_name)
        return error(f"{tool_name} 执行失败: {exc}", traceback=traceback.format_exc(limit=8))


def _df_len(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _summarize_data_stats(result: dict[str, Any]) -> str:
    df = result.get("stats_df")
    if df is None or getattr(df, "empty", True):
        return "数据收集率统计完成，但未找到有效流量数据。"
    avg = float(df["数据收集率(%)"].mean()) if "数据收集率(%)" in df else 0.0
    low = df.sort_values("数据收集率(%)").head(3)["点位编号"].astype(str).tolist() if "数据收集率(%)" in df else []
    return f"数据收集率统计完成：处理 {len(df)} 个点位，平均收集率 {avg:.2%}，收集率最低点位: {', '.join(low)}。"


def run_data_stats_impl(deps: AgentDeps) -> ToolResult:
    cfg = _build_config(deps)
    return _run_tool(deps, "run_data_stats", data_stats_run, cfg, _summarize_data_stats)


def _summarize_data_filter(result: dict[str, Any]) -> str:
    selected = result.get("selected", {})
    total_days = sum(len(days) for days in selected.values())
    empty_points = [str(k) for k, days in selected.items() if not days]
    return f"数据筛选完成：处理 {len(selected)} 个点位，有效旱天总数 {total_days} 天，空结果点位 {len(empty_points)} 个。"


def run_data_filter_impl(deps: AgentDeps, missing_rate_threshold: float = 0.1) -> ToolResult:
    cfg = _build_config(deps, missing_rate_threshold=missing_rate_threshold)
    return _run_tool(
        deps,
        "run_data_filter",
        data_filter_run,
        cfg,
        _summarize_data_filter,
        params={"missing_rate_threshold": missing_rate_threshold},
    )


def _summarize_dry(result: dict[str, Any]) -> str:
    dry_curves = result.get("dry_curve_data", {})
    stats = result.get("statistics")
    day_num = result.get("day_num")
    day_summary = ""
    if day_num is not None and not getattr(day_num, "empty", True):
        day_summary = f"，天数统计 {len(day_num)} 行"
    return f"旱天分析完成：生成 {len(dry_curves)} 个点位特征曲线，统计表 { _df_len(stats) } 行{day_summary}。"


def run_dry_analysis_impl(deps: AgentDeps, smooth_window_minutes: int = 20) -> ToolResult:
    precheck = _require_file(deps, deps.paths.filter_result, "筛选结果.xlsx", "请先调用 run_data_filter", "run_data_filter")
    if precheck:
        return precheck
    cfg = _build_config(deps, smooth_window_minutes=smooth_window_minutes)
    return _run_tool(
        deps,
        "run_dry_analysis",
        dry_analysis_run,
        cfg,
        _summarize_dry,
        params={"smooth_window_minutes": smooth_window_minutes},
        postprocess_fn=_save_dry_curve_artifacts,
    )


RAINFALL_RANGE_ALIASES = {
    "all": "all",
    "全部": "all",
    "日": "daily",
    "降雨日": "daily",
    "daily": "daily",
    "day": "daily",
    "场次": "events",
    "事件": "events",
    "events": "events",
    "event": "events",
    "图表": "charts",
    "图": "charts",
    "charts": "charts",
    "chart": "charts",
}


def _summarize_rainfall(result: dict[str, Any], rainfall_range: str = "all") -> str:
    daily = result.get("daily_rain")
    event = result.get("event_rain")
    rainy_days = 0
    total_rain = 0.0
    if daily is not None and not getattr(daily, "empty", True):
        rain_col = next((c for c in daily.columns if "降雨量" in str(c)), None)
        if rain_col:
            rainy_days = int((daily[rain_col] > 0).sum())
            total_rain = float(daily[rain_col].sum())
    examples = []
    if event is not None and not getattr(event, "empty", True):
        for _, row in event.head(5).iterrows():
            examples.append(" / ".join(str(x) for x in row.iloc[:4].tolist()))
    if rainfall_range == "daily":
        return f"降雨日统计完成：雨日 {rainy_days} 天，总雨量 {total_rain:.1f} mm，日统计 { _df_len(daily) } 行。"
    if rainfall_range == "events":
        return f"场次降雨统计完成：有效场次 { _df_len(event) } 场。场次示例: {'; '.join(examples)}"
    if rainfall_range == "charts":
        chart_count = len(result.get("rainfall_chart_paths", {}) or {})
        return f"降雨图表生成完成：生成 {chart_count} 张图，日统计 { _df_len(daily) } 行，场次 { _df_len(event) } 场。"
    return f"降雨分析完成：雨日 {rainy_days} 天，总雨量 {total_rain:.1f} mm，有效场次 { _df_len(event) } 场。场次示例: {'; '.join(examples)}"


def _rainfall_artifacts(deps: AgentDeps, result: dict[str, Any], rainfall_range: str) -> list[str]:
    artifacts = _artifacts(deps)
    if rainfall_range == "charts":
        return [p for p in artifacts if "降雨分析图" in p or p.lower().endswith((".png", ".jpg", ".jpeg"))]
    return artifacts


def run_rainfall_analysis_impl(deps: AgentDeps, rainfall_gap_hours: int = 12, rainfall_range: str = "all") -> ToolResult:
    rainfall_range = _normalize_choice(rainfall_range, RAINFALL_RANGE_ALIASES, "all")
    if not deps.paths.rainfall_file.exists() or deps.paths.rainfall_file.stat().st_size == 0:
        return ok("未检测到有效降雨数据，雨天路径可跳过。")
    cfg = _build_config(deps, rainfall_gap_hours=rainfall_gap_hours)
    return _run_tool(
        deps,
        "run_rainfall_analysis",
        rainfall_analysis_run,
        cfg,
        lambda result: _summarize_rainfall(result, rainfall_range),
        params={"rainfall_gap_hours": rainfall_gap_hours, "rainfall_range": rainfall_range},
        artifacts_fn=lambda tool_deps, result: _rainfall_artifacts(tool_deps, result, rainfall_range),
    )


def _summarize_event_stats(result: dict[str, Any]) -> str:
    df = result.get("event_stats")
    return f"雨天事件统计完成：生成 { _df_len(df) } 行事件响应统计。"


def run_event_stats_impl(deps: AgentDeps, event_ids: list[int]) -> ToolResult:
    if not event_ids:
        return blocked("未选择降雨场次编号", "请先调用 run_rainfall_analysis 并让用户选择 event_ids")
    precheck = _require_sheet(deps, "场次降雨统计", "场次降雨统计", "请先调用 run_rainfall_analysis", "run_rainfall_analysis")
    if precheck:
        return precheck
    deps.session.selected_event_ids = event_ids
    cfg = _build_config(deps, event_ids=event_ids)
    return _run_tool(
        deps,
        "run_event_stats",
        event_stats_run,
        cfg,
        _summarize_event_stats,
        params={"event_ids": event_ids},
    )


def _summarize_pattern(result: dict[str, Any]) -> str:
    df = result.get("pattern_df")
    chart_count = result.get("chart_count", {})
    return f"排污规律分析完成：分析 { _df_len(df) } 个点位，生成流量图 {chart_count.get('flow_charts', 0)} 张、液位图 {chart_count.get('level_charts', 0)} 张。"


def run_pattern_analysis_impl(deps: AgentDeps) -> ToolResult:
    precheck = _require_dry_curve_artifacts(deps)
    if precheck:
        return precheck
    dry_artifacts = _load_dry_curve_artifacts(deps)
    cfg = _build_config(deps)
    return _run_tool(
        deps,
        "run_pattern_analysis",
        pattern_analysis_run,
        cfg,
        _summarize_pattern,
        runner_kwargs=dry_artifacts,
    )


def _summarize_rdii(result: dict[str, Any]) -> str:
    return (
        "RDII 分析完成："
        f"最大液位 { _df_len(result.get('max_level')) } 行，"
        f"平均流量 { _df_len(result.get('avg_flow')) } 行，"
        f"RDII 总量 { _df_len(result.get('rdii_total')) } 行。"
    )


def run_rdii_analysis_impl(deps: AgentDeps, event_ids: list[int]) -> ToolResult:
    if not event_ids:
        return blocked("未选择降雨场次编号", "请先调用 run_rainfall_analysis 并让用户选择 event_ids")
    curve_precheck = _require_dry_curve_artifacts(deps)
    if curve_precheck:
        return curve_precheck
    precheck = _require_sheet(deps, "场次降雨统计", "场次降雨统计", "请先调用 run_rainfall_analysis", "run_rainfall_analysis")
    if precheck:
        return precheck
    deps.session.selected_event_ids = event_ids
    dry_artifacts = _load_dry_curve_artifacts(deps)
    cfg = _build_config(deps, event_ids=event_ids)
    return _run_tool(
        deps,
        "run_rdii_analysis",
        rdii_analysis_run,
        cfg,
        _summarize_rdii,
        params={"event_ids": event_ids},
        runner_kwargs={"dry_curve_data": dry_artifacts.get("dry_curve_data")},
    )


RISK_SCOPE_ALIASES = {
    "all": "all",
    "全部": "all",
    "旱天": "dry",
    "旱天风险": "dry",
    "dry": "dry",
    "雨天": "rainy",
    "雨天风险": "rainy",
    "溢流": "rainy",
    "rainy": "rainy",
    "wet": "rainy",
}


def _summarize_risk(result: dict[str, Any], scope: str = "all") -> str:
    dry_len = _df_len(result.get("dry_risk"))
    rainy_len = _df_len(result.get("rainy_risk"))
    if scope == "dry":
        return f"旱天风险分析完成：旱天风险 {dry_len} 行。"
    if scope == "rainy":
        return f"雨天溢流风险分析完成：雨天溢流风险 {rainy_len} 行。"
    return f"风险分析完成：旱天风险 {dry_len} 行，雨天溢流风险 {rainy_len} 行。"


def run_risk_analysis_impl(deps: AgentDeps, event_ids: list[int] | None = None, scope: str = "all") -> ToolResult:
    scope = _normalize_choice(scope, RISK_SCOPE_ALIASES, "all")
    if scope in {"dry", "all"} and not _has_sheet(deps, "旱天分析"):
        return blocked("旱天分析结果", "请先调用 run_dry_analysis")
    if scope in {"rainy", "all"} and not _has_sheet(deps, "场次降雨统计"):
        return blocked("场次降雨统计", "请先调用 run_rainfall_analysis")
    has_rainfall = scope in {"rainy", "all"}
    if event_ids:
        deps.session.selected_event_ids = event_ids
    cfg = _build_config(deps, event_ids=event_ids)
    return _run_tool(
        deps,
        "run_risk_analysis",
        risk_analysis_run,
        cfg,
        lambda result: _summarize_risk(result, scope),
        params={"event_ids": event_ids or [], "has_rainfall_data": has_rainfall, "scope": scope},
        runner_kwargs={"has_rainfall_data": has_rainfall},
    )


def _summarize_report(result: dict[str, Any]) -> str:
    stats = result.get("stats", {})
    output_file = result.get("output_file")
    return (
        f"报告组装完成：输出 {output_file}，"
        f"填充表格 {stats.get('tables_filled', 0)} 个，插入图片 {stats.get('images_inserted', 0)} 张。"
    )


def run_report_assembler_impl(deps: AgentDeps) -> ToolResult:
    curve_precheck = _require_dry_curve_artifacts(deps)
    if curve_precheck:
        return curve_precheck
    for sheet, missing, hint in [
        ("旱天分析", "旱天分析结果", "请先调用 run_dry_analysis"),
        ("排污规律分析", "排污规律分析结果", "请先调用 run_pattern_analysis"),
        ("旱天风险", "风险分析结果", "请先调用 run_risk_analysis"),
    ]:
        precheck = _require_sheet(deps, sheet, missing, hint)
        if precheck:
            return precheck
    if not deps.paths.report_template_file.exists():
        return blocked("报告模板", f"请将 docx 模板放入 {deps.paths.templates}")
    dry_artifacts = _load_dry_curve_artifacts(deps)
    cfg = _build_config(deps)
    return _run_tool(
        deps,
        "run_report_assembler",
        report_assembler_run,
        cfg,
        _summarize_report,
        runner_kwargs={"dry_curve_data": dry_artifacts.get("dry_curve_data")},
    )
