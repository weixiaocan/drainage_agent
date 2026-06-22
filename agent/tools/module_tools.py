from __future__ import annotations

import pickle
import time
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
from analysis.filtering import FilterConfig, run_data_filter
from analysis.patterns import analyze_patterns
from analysis.rainfall import analyze_rainfall
from analysis.rdii import analyze_rdii
from analysis.reporting import build_report
from analysis.risk import assess_risk
from analysis.schema import from_display_columns, to_display_columns
from analysis.stats import check_data


SHEET_TABLE_TYPES = {
    "数据体检": "data_check",
    "数据收集率统计": "data_check",
    "日降雨量统计": "rainfall_daily",
    "降雨概况": "rainfall_daily",
    "场次降雨统计": "rainfall_events",
    "降雨场次分析": "rainfall_events",
    "排污规律分析": "patterns",
    "旱天分析": "dry_stats",
    "旱天风险": "dry_risk",
    "雨天溢流风险": "rainy_risk",
    "雨天事件统计": "event_response",
    "RDII总量统计": "rdii",
}


def _rel(deps: AgentDeps, path: Path) -> str:
    try:
        return path.resolve().relative_to(deps.paths.root).as_posix()
    except ValueError:
        return str(path)


def _artifacts(deps: AgentDeps) -> list[str]:
    if not deps.paths.outputs.exists():
        return []
    return [_rel(deps, p) for p in deps.paths.outputs.rglob("*") if p.is_file()]


class ToolLLMClient:
    def __init__(self, deps: AgentDeps):
        from openai import OpenAI

        kwargs: dict[str, Any] = {"api_key": deps.settings.api_key}
        if deps.settings.base_url:
            kwargs["base_url"] = deps.settings.base_url
        self._client = OpenAI(**kwargs)
        self._model = deps.settings.model
        self._prompt_dirs = [deps.paths.root / "agent" / "prompts", deps.paths.root / "prompts"]

    def load_prompt(self, name: str) -> str:
        for prompt_dir in self._prompt_dirs:
            path = prompt_dir / f"{name}.txt"
            if path.exists():
                return path.read_text(encoding="utf-8")
        raise FileNotFoundError(name)

    def chat_json(self, prompt: str, temperature: float = 0.1) -> str:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                return response.choices[0].message.content or "{}"
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        raise last_exc or RuntimeError("LLM JSON call failed")


def _build_tool_llm_client(deps: AgentDeps) -> ToolLLMClient | None:
    if not deps.settings.api_key:
        return None
    return ToolLLMClient(deps)


def _write_sheet(path: Path, sheet_name: str, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    display_df = to_display_columns(df, SHEET_TABLE_TYPES.get(sheet_name, ""))
    mode = "a" if path.exists() else "w"
    if mode == "a":
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            display_df.to_excel(writer, sheet_name=sheet_name, index=False)
    else:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            display_df.to_excel(writer, sheet_name=sheet_name, index=False)


def _remove_sheet(path: Path, sheet_name: str) -> None:
    if not path.exists():
        return
    from openpyxl import load_workbook

    workbook = load_workbook(path)
    if sheet_name not in workbook.sheetnames:
        return
    if len(workbook.sheetnames) <= 1:
        path.unlink()
        return
    workbook.remove(workbook[sheet_name])
    workbook.save(path)


def _add_rainfall_excel_charts(path: Path, daily: pd.DataFrame) -> None:
    if daily.empty or not path.exists():
        return
    from openpyxl import load_workbook
    from openpyxl.chart import BarChart, PieChart, Reference
    from openpyxl.chart.label import DataLabelList

    sheet_name = "降雨概况"
    workbook = load_workbook(path)
    if sheet_name not in workbook.sheetnames:
        return
    sheet = workbook[sheet_name]

    daily_rows = len(daily)
    if daily_rows == 0:
        workbook.save(path)
        return

    pie_col = 5
    rainy_days = int(pd.to_numeric(daily["rain_mm"], errors="coerce").fillna(0).gt(0).sum())
    non_rainy_days = int(daily_rows - rainy_days)
    sheet.cell(row=1, column=pie_col, value="类型")
    sheet.cell(row=1, column=pie_col + 1, value="天数")
    sheet.cell(row=2, column=pie_col, value="降雨日")
    sheet.cell(row=2, column=pie_col + 1, value=rainy_days)
    sheet.cell(row=3, column=pie_col, value="非降雨日")
    sheet.cell(row=3, column=pie_col + 1, value=non_rainy_days)

    bar_chart = BarChart()
    bar_chart.type = "col"
    bar_chart.title = "日降雨量时间序列"
    bar_chart.y_axis.title = "降雨量(mm)"
    bar_chart.x_axis.title = "日期"
    bar_chart.width = 20
    bar_chart.height = 10
    bar_chart.y_axis.majorGridlines = None
    bar_chart.x_axis.majorGridlines = None
    data_ref = Reference(sheet, min_col=2, min_row=1, max_row=daily_rows + 1)
    cats_ref = Reference(sheet, min_col=1, min_row=2, max_row=daily_rows + 1)
    bar_chart.add_data(data_ref, titles_from_data=True)
    bar_chart.set_categories(cats_ref)
    sheet.add_chart(bar_chart, "A8")

    pie_chart = PieChart()
    pie_chart.width = 10
    pie_chart.height = 10
    pie_chart.legend = None
    pie_data = Reference(sheet, min_col=pie_col + 1, min_row=1, max_row=3)
    pie_cats = Reference(sheet, min_col=pie_col, min_row=2, max_row=3)
    pie_chart.add_data(pie_data, titles_from_data=True)
    pie_chart.set_categories(pie_cats)
    pie_chart.dataLabels = DataLabelList()
    pie_chart.dataLabels.showPercent = True
    pie_chart.dataLabels.showVal = True
    pie_chart.dataLabels.showCatName = True
    sheet.add_chart(pie_chart, "A28")

    workbook.save(path)


def _save_rainfall_png_charts(daily: pd.DataFrame, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "daily_bar": output_dir / "日降雨量时间序列图.png",
        "rainy_ratio": output_dir / "降雨日占比饼图.png",
    }
    if daily.empty:
        return {key: str(value) for key, value in paths.items()}
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return {key: str(value) for key, value in paths.items()}

    plot_df = daily.copy()
    plot_df["date"] = pd.to_datetime(plot_df["date"], errors="coerce")
    plot_df["rain_mm"] = pd.to_numeric(plot_df["rain_mm"], errors="coerce").fillna(0)
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(9.2, 4.8), dpi=180)
    labels = [d.strftime("%Y-%m-%d") if not pd.isna(d) else "" for d in plot_df["date"]]
    ax.bar(range(len(plot_df)), plot_df["rain_mm"], color="#5B9BD5", edgecolor="#2F5597", linewidth=0.6)
    ax.set_xticks(range(len(plot_df)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7.5)
    ax.set_ylabel("降雨量(mm)")
    ax.set_xlabel("日期")
    ax.set_title("日降雨量时间序列")
    ax.grid(False)
    fig.tight_layout()
    fig.savefig(paths["daily_bar"], bbox_inches="tight")
    plt.close(fig)

    rainy_days = int(plot_df["rain_mm"].gt(0).sum())
    non_rainy_days = int(len(plot_df) - rainy_days)
    total_days = max(1, rainy_days + non_rainy_days)
    labels = ["降雨日", "非降雨日"]

    def autopct(pct: float) -> str:
        count = int(round(pct * total_days / 100.0))
        label = labels.pop(0)
        return f"{label}\n{count}天\n{pct:.0f}%"

    fig, ax = plt.subplots(figsize=(4.8, 4.8), dpi=180)
    ax.pie(
        [rainy_days, non_rainy_days],
        labels=["", ""],
        autopct=autopct,
        pctdistance=0.58,
        startangle=90,
        colors=["#5B9BD5", "#ED7D31"],
        wedgeprops={"edgecolor": "white", "linewidth": 1.0},
        textprops={"fontsize": 10, "color": "black", "ha": "center"},
    )
    ax.axis("equal")
    fig.tight_layout()
    fig.savefig(paths["rainy_ratio"], bbox_inches="tight")
    plt.close(fig)
    return {key: str(value) for key, value in paths.items()}


def _save_rdii_curve_pngs(
    rdii_curve_data: dict[int, dict[str, pd.DataFrame]],
    rain: pd.DataFrame,
    events: pd.DataFrame,
    output_dir: Path,
    delay_hours: float = 12.0,
    selected_events: list[int] | None = None,
) -> dict[int, dict[str, str]]:
    saved: dict[int, dict[str, str]] = {}
    if not rdii_curve_data or rain.empty or events.empty:
        return saved
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib as mpl
        from matplotlib import gridspec
        import matplotlib.pyplot as plt
    except Exception:
        return saved

    mpl.rcParams["font.sans-serif"] = ["SimHei"]
    mpl.rcParams["font.serif"] = ["SimHei"]
    mpl.rcParams["axes.unicode_minus"] = False

    rain_df = rain.copy()
    rain_df["timestamp"] = pd.to_datetime(rain_df["timestamp"], errors="coerce")
    rain_df = rain_df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    if "rain_mm" not in rain_df.columns:
        return saved

    event_ids = sorted(int(event_id) for event_id in rdii_curve_data.keys())
    if selected_events:
        wanted = {int(event_id) for event_id in selected_events}
        event_ids = [event_id for event_id in event_ids if event_id in wanted]

    for event_id in event_ids:
        event_rows = events[events["event_id"].astype(int) == int(event_id)]
        if event_rows.empty:
            continue
        event = event_rows.iloc[0]
        start = pd.to_datetime(event["start_time"], errors="coerce")
        end = pd.to_datetime(event["end_time"], errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        plot_end = end + pd.Timedelta(hours=delay_hours)
        event_rain = rain_df.loc[start:plot_end, "rain_mm"].copy()

        event_saved: dict[str, str] = {}
        for point_id, rdii_df in rdii_curve_data.get(event_id, {}).items():
            data_to_plot = rdii_df.copy()
            if data_to_plot.empty:
                continue
            data_to_plot.index = pd.to_datetime(data_to_plot.index, errors="coerce")
            data_to_plot = data_to_plot[~data_to_plot.index.isna()].sort_index()
            rename_map = {
                "rain_flow_lps": "雨天流量",
                "dry_flow_lps": "旱天流量",
                "rdii_lps": "RDII",
            }
            data_to_plot = data_to_plot.rename(columns=rename_map)
            keep_cols = [col for col in ("雨天流量", "旱天流量", "RDII") if col in data_to_plot.columns]
            if not keep_cols:
                continue

            event_dir = output_dir / "rdii_curve" / f"event{event_id}_{start.month}_{start.day}"
            event_dir.mkdir(parents=True, exist_ok=True)
            fig = plt.figure(figsize=(10, 5))
            grid = gridspec.GridSpec(2, 1, height_ratios=[1, 3])
            ax_flow = plt.subplot(grid[1])
            ax_rain = plt.subplot(grid[0])
            ax_rain.get_xaxis().set_visible(False)
            fig.subplots_adjust(hspace=0)

            data_to_plot[keep_cols].plot(ax=ax_flow, legend=True)
            ax_flow.set_xlabel('时间', fontsize='large')
            ax_flow.set_ylabel('流量/(L/s)', fontsize='large')

            rain_to_plot = _regularize_rain_series_for_plot(event_rain)
            if len(rain_to_plot) > 500:
                rain_to_plot = rain_to_plot.resample("10min").sum()
                rain_to_plot = _regularize_rain_series_for_plot(rain_to_plot)
            ax_rain.bar(range(len(rain_to_plot)), rain_to_plot.to_numpy(dtype=float), width=0.8)
            ax_rain.set_ylabel('降雨/mm', fontsize='large')
            if len(rain_to_plot) > 100:
                n_ticks = min(10, len(rain_to_plot))
                step = len(rain_to_plot) // n_ticks
                tick_positions = list(range(0, len(rain_to_plot), step))
                ax_rain.set_xticks(tick_positions)
                ax_rain.set_xticklabels(
                    [rain_to_plot.index[i].strftime('%m-%d %H:%M') for i in tick_positions],
                    rotation=45,
                    ha='right',
                )

            image_path = event_dir / f"{point_id}_event{event_id}.png"
            fig.savefig(image_path, dpi=300, bbox_inches="tight")
            plt.cla()
            plt.clf()
            plt.close(fig)
            event_saved[str(point_id)] = str(image_path)

        if event_saved:
            saved[event_id] = event_saved

    return saved


def _regularize_rain_series_for_plot(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    result = pd.to_numeric(series, errors="coerce").fillna(0.0).copy()
    index = pd.DatetimeIndex(pd.to_datetime(result.index, errors="coerce"))
    valid = ~index.isna()
    result = result[valid]
    index = pd.DatetimeIndex(index[valid])
    if result.empty:
        return result

    freq = index.freq or pd.infer_freq(index)
    if freq is None and len(index) > 1:
        deltas = index.to_series().diff().dropna()
        positive_deltas = deltas[deltas > pd.Timedelta(0)]
        if not positive_deltas.empty:
            freq = positive_deltas.min()
    if freq is not None:
        full_index = pd.date_range(index.min(), index.max(), freq=freq)
        result.index = index
        result = result.groupby(level=0).sum().reindex(full_index, fill_value=0.0)
        return result

    result.index = index
    return result


def _daily_curve_frame(dry_flow: pd.DataFrame, point_id: str, value_col: str) -> pd.DataFrame:
    if dry_flow.empty or value_col not in dry_flow.columns:
        return pd.DataFrame()

    point_flow = dry_flow[dry_flow["point_id"].astype(str) == str(point_id)].copy()
    if point_flow.empty:
        return pd.DataFrame()

    point_flow["timestamp"] = pd.to_datetime(point_flow["timestamp"], errors="coerce")
    point_flow = point_flow.dropna(subset=["timestamp"]).sort_values("timestamp")
    if point_flow.empty:
        return pd.DataFrame()

    day_index = pd.date_range("00:00:00", "23:59:00", freq="min")
    daily_df = pd.DataFrame(index=day_index)
    for date, day_data in point_flow.groupby(point_flow["timestamp"].dt.strftime("%Y-%m-%d"), sort=True):
        values = pd.to_numeric(day_data[value_col], errors="coerce").dropna().to_numpy()
        if len(values) == 0:
            continue
        padded = [0.0] * 1440
        limit = min(len(values), 1440)
        padded[:limit] = values[:limit]
        daily_df[str(date)] = padded
    return daily_df


def _plot_pipeline_pattern_curve(
    daily_df: pd.DataFrame,
    curve: pd.DataFrame,
    value_col: str,
    output_path: Path,
    daily_label: str,
    curve_label: str,
    y_label: str,
) -> bool:
    if daily_df.empty or daily_df.shape[1] == 0 or value_col not in curve.columns:
        return False

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    plot_curve = curve.copy()
    if "minute_of_day" in plot_curve.columns:
        plot_curve = plot_curve.sort_values("minute_of_day")
    curve_values = pd.to_numeric(plot_curve[value_col], errors="coerce").to_numpy()
    if len(curve_values) == 0:
        return False

    day_index = pd.date_range("00:00:00", "23:59:00", freq="min")
    padded_curve = [0.0] * 1440
    limit = min(len(curve_values), 1440)
    padded_curve[:limit] = curve_values[:limit]
    curve_series = pd.Series(padded_curve, index=day_index)

    fig = plt.figure(figsize=(10, 5), dpi=120)
    ax = fig.add_subplot(1, 1, 1)
    valid_days = list(daily_df.columns)
    for idx, day in enumerate(valid_days):
        label = daily_label if idx == len(valid_days) - 1 else ""
        daily_df[day].plot(ax=ax, color="#D3D3D3", label=label, legend=bool(label), alpha=0.5)
    curve_series.plot(ax=ax, color="#1E90FF", label=curve_label, legend=True)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(3, 24, 3)))
    ax.set_xlabel("时间")
    ax.set_ylabel(y_label)
    ax.grid(False)
    ax.legend(loc="upper right")
    plt.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return True


def _save_pattern_curve_pngs(curves: dict[str, pd.DataFrame], dry_flow: pd.DataFrame, output_dir: Path) -> dict[str, list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[str]] = {}
    if not curves:
        return saved
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return saved

    plt.rcParams["font.sans-serif"] = ["SimSun", "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    hour_ticks = list(range(0, 1441, 120))
    hour_labels = [f"{hour // 60:02d}:00" for hour in hour_ticks]

    for point_id, curve in curves.items():
        point_paths: list[str] = []
        plot_df = curve.copy()
        minutes = pd.to_numeric(plot_df["minute_of_day"], errors="coerce").fillna(0)

        if "flow_lps" in plot_df.columns:
            flow_path = output_dir / f"{point_id}_流量特征曲线.png"
            flow_daily = _daily_curve_frame(dry_flow, point_id, "flow_lps")
            if _plot_pipeline_pattern_curve(
                flow_daily,
                plot_df,
                "flow_lps",
                flow_path,
                "每日流量",
                "流量特征曲线_总体",
                "流量/(L/s)",
            ):
                point_paths.append(str(flow_path))

        if "level_m" in plot_df.columns:
            level_path = output_dir / f"{point_id}_液位特征曲线.png"
            level_daily = _daily_curve_frame(dry_flow, point_id, "level_m")
            if _plot_pipeline_pattern_curve(
                level_daily,
                plot_df,
                "level_m",
                level_path,
                "每日液位",
                "液位特征曲线",
                "液位/(m)",
            ):
                point_paths.append(str(level_path))

        saved[point_id] = point_paths
    return saved


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


def _rdii_curves_path(deps: AgentDeps) -> Path:
    return _intermediate_dir(deps) / "rdii_curves.pkl"


def _save_curves(deps: AgentDeps, curves: dict[str, pd.DataFrame]) -> None:
    with _curves_path(deps).open("wb") as fh:
        pickle.dump(curves, fh)


def _save_rdii_curves(deps: AgentDeps, curves: dict[int, dict[str, pd.DataFrame]]) -> None:
    with _rdii_curves_path(deps).open("wb") as fh:
        pickle.dump(curves, fh)


def _load_curves(deps: AgentDeps) -> dict[str, pd.DataFrame]:
    path = _curves_path(deps)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return pickle.load(fh)


def data_filter_impl(
    deps: AgentDeps,
    missing_rate_threshold: float = 0.1,
    expected_rows_per_day: int = 1440,
    rain_day_filter_threshold: float = 2.0,
    zero_like_threshold: float = 0.02,
    high_zero_ratio_threshold: float = 0.5,
    high_zero_ratio_normal_days_threshold: int = 5,
    zero_day_drop_min_nonzero_keep_days: int = 3,
    mean_lower_ratio: float = 0.5,
    mean_upper_ratio: float = 2.0,
    output_file: str | None = None,
) -> ToolResult:
    params = {
        "missing_rate_threshold": missing_rate_threshold,
        "expected_rows_per_day": expected_rows_per_day,
        "rain_day_filter_threshold": rain_day_filter_threshold,
        "zero_like_threshold": zero_like_threshold,
        "high_zero_ratio_threshold": high_zero_ratio_threshold,
        "high_zero_ratio_normal_days_threshold": high_zero_ratio_normal_days_threshold,
        "zero_day_drop_min_nonzero_keep_days": zero_day_drop_min_nonzero_keep_days,
        "mean_lower_ratio": mean_lower_ratio,
        "mean_upper_ratio": mean_upper_ratio,
        "output_file": output_file or "",
    }

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(root=deps.paths.root)
        rain = io.load_rain(root=deps.paths.root)
        out_path = Path(output_file) if output_file else deps.paths.filter_result
        if not out_path.is_absolute():
            out_path = deps.paths.root / out_path
        selected = run_data_filter(
            flow=flow,
            rain=rain,
            output_xlsx=out_path,
            config=FilterConfig(
                missing_rate_threshold=missing_rate_threshold,
                expected_rows_per_day=expected_rows_per_day,
                rain_day_filter_threshold=rain_day_filter_threshold,
                zero_like_threshold=zero_like_threshold,
                high_zero_ratio_threshold=high_zero_ratio_threshold,
                high_zero_ratio_normal_days_threshold=high_zero_ratio_normal_days_threshold,
                zero_day_drop_min_nonzero_keep_days=zero_day_drop_min_nonzero_keep_days,
                mean_lower_ratio=mean_lower_ratio,
                mean_upper_ratio=mean_upper_ratio,
            ),
        )
        point_count = len(selected)
        total_days = sum(len(days) for days in selected.values())
        summary = f"数据筛选完成：处理 {point_count} 个点位，筛出有效旱天 {total_days} 个点位日，输出 {_rel(deps, out_path)}。"
        return summary, {"selected": selected, "output_file": str(out_path)}

    return _run(deps, "data_filter", work, params=params)


def _load_event_table(deps: AgentDeps) -> pd.DataFrame:
    if not deps.paths.combined_xlsx.exists():
        return pd.DataFrame()
    for sheet_name in ("降雨场次分析", "场次降雨统计"):
        try:
            events = pd.read_excel(deps.paths.combined_xlsx, sheet_name=sheet_name)
            return from_display_columns(events, "rainfall_events")
        except Exception:
            continue
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
            _write_sheet(deps.paths.combined_xlsx, "降雨场次分析", events)
            _remove_sheet(deps.paths.combined_xlsx, "场次降雨统计")
    return needs_input(
        "event_ids",
        "请从 options 中选择降雨场次编号；也可以回复“只出旱天报告”。",
        summary="需要先选择降雨场次编号，才能分析雨天响应、RDII 或雨天风险。",
        options=_event_options(events),
    )


def _ensure_filter_result(deps: AgentDeps) -> Path:
    if deps.paths.filter_result.exists():
        return deps.paths.filter_result
    flow = io.load_flow(root=deps.paths.root)
    rain = io.load_rain(root=deps.paths.root)
    run_data_filter(flow=flow, rain=rain, output_xlsx=deps.paths.filter_result, config=FilterConfig())
    return deps.paths.filter_result


def _load_filtered_dry_flow(
    deps: AgentDeps,
    points: list[str] | None = None,
    time_range: list[str] | None = None,
) -> pd.DataFrame:
    filter_result = _ensure_filter_result(deps)
    return io.load_filtered_flow(points=points, time_range=time_range, root=deps.paths.root)


def check_data_impl(deps: AgentDeps, points: list[str] | None = None) -> ToolResult:
    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, root=deps.paths.root)
        stats_df = check_data(flow)
        _write_sheet(deps.paths.combined_xlsx, "数据收集率统计", stats_df)
        _remove_sheet(deps.paths.combined_xlsx, "数据体检")
        avg = float(stats_df["collection_rate"].mean()) if not stats_df.empty else 0.0
        summary = f"数据收集率统计完成：处理 {len(stats_df)} 个点位，平均收集率 {avg:.1%}。"
        return summary, {"table": stats_df.to_dict(orient="records")}

    return _run(deps, "check_data", work, params={"points": points or []})


def analyze_rainfall_impl(deps: AgentDeps, time_range: list[str] | None = None, output: str = "all", rainfall_gap_hours: int = 12) -> ToolResult:
    params = {"time_range": time_range or [], "output": output, "rainfall_gap_hours": rainfall_gap_hours}

    def work() -> tuple[str, dict[str, Any]]:
        rain = io.load_rain(time_range=time_range, root=deps.paths.root)
        result = analyze_rainfall(rain, gap_hours=rainfall_gap_hours)
        chart_paths: dict[str, str] = {}
        if output in {"all", "daily"}:
            _write_sheet(deps.paths.combined_xlsx, "降雨概况", result["daily"])
            _remove_sheet(deps.paths.combined_xlsx, "日降雨量统计")
            _add_rainfall_excel_charts(deps.paths.combined_xlsx, result["daily"])
            chart_paths = _save_rainfall_png_charts(result["daily"], deps.paths.outputs / "降雨分析图")
        if output in {"all", "events"}:
            _write_sheet(deps.paths.combined_xlsx, "降雨场次分析", result["events"])
            _remove_sheet(deps.paths.combined_xlsx, "场次降雨统计")
        rainy_days = int(result["daily"]["is_rainy"].sum()) if not result["daily"].empty else 0
        total = float(result["daily"]["rain_mm"].sum()) if not result["daily"].empty else 0.0
        summary = f"降雨分析完成：雨日 {rainy_days} 天，总雨量 {total:.1f} mm，场次 {len(result['events'])} 场。"
        data = {key: df.to_dict(orient="records") for key, df in result.items()}
        data["chart_paths"] = chart_paths
        return summary, data

    return _run(deps, "analyze_rainfall", work, params=params)


def _dry_inputs(deps: AgentDeps) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    dry_flow = _load_filtered_dry_flow(deps)
    stats_df = dry_statistics(dry_flow, io.load_sites(root=deps.paths.root))
    curves = build_dry_curves(dry_flow)
    _save_curves(deps, curves)
    _write_sheet(deps.paths.combined_xlsx, "旱天分析", stats_df)
    return dry_flow, stats_df, curves


def analyze_patterns_impl(deps: AgentDeps, points: list[str] | None = None, output: str = "all") -> ToolResult:
    params = {"points": points or [], "output": output}

    def work() -> tuple[str, dict[str, Any]]:
        dry_flow = _load_filtered_dry_flow(deps, points=points)
        llm_client = _build_tool_llm_client(deps)
        result = analyze_patterns(dry_flow, llm_client=llm_client)
        patterns = result["patterns"]
        curves = result["curves"]
        _save_curves(deps, curves)
        curve_images = _save_pattern_curve_pngs(curves, dry_flow, deps.paths.outputs / "特征曲线图")
        _write_sheet(deps.paths.combined_xlsx, "排污规律分析", patterns)
        llm_note = "描述由 LLM JSON 生成并经规则后处理" if llm_client is not None else "未配置 LLM，描述使用规则兜底生成"
        summary = (
            f"排污规律分析完成：分析 {len(patterns)} 个点位，生成 {len(curves)} 条旱天曲线。"
            f"{llm_note}。基于筛选结果 {_rel(deps, deps.paths.filter_result)}。"
        )
        return summary, {"table": patterns.to_dict(orient="records"), "curve_images": curve_images}

    return _run(deps, "analyze_patterns", work, params=params)


def analyze_event_response_impl(deps: AgentDeps, event_ids: list[int] | None = None, points: list[str] | None = None) -> ToolResult:
    precheck = _require_event_ids(deps, event_ids)
    if precheck:
        return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    params = {"event_ids": event_ids, "points": points or []}

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, root=deps.paths.root)
        events = _load_event_table(deps)
        response = analyze_event_response(flow, events, event_ids or [])
        _write_sheet(deps.paths.combined_xlsx, "雨天事件统计", response)
        if response.empty:
            deps.session.unavailable_event_ids = sorted(
                set(deps.session.unavailable_event_ids).union(event_ids or [])
            )
            selected = points or ["全部点位"]
            summary = (
                f"雨天事件统计无可用数据：场次 {event_ids} 与点位 {selected} 的监测数据无时间重叠，"
                "无法计算事件响应指标。"
            )
            return summary, {"table": [], "no_data": True, "event_ids": event_ids, "points": points or []}
        summary = f"雨天事件统计完成：场次 {event_ids}，输出 {len(response)} 个点位统计。"
        return summary, {"table": response.to_dict(orient="records"), "no_data": False}

    return _run(deps, "analyze_event_response", work, params=params)


def analyze_rdii_impl(deps: AgentDeps, event_ids: list[int] | None = None, points: list[str] | None = None, output: str = "all") -> ToolResult:
    precheck = _require_event_ids(deps, event_ids)
    if precheck:
        return precheck
    event_ids = event_ids or deps.session.selected_event_ids
    params = {"event_ids": event_ids, "points": points or [], "output": output}

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(points=points, root=deps.paths.root)
        dry_flow = _load_filtered_dry_flow(deps, points=points)
        events = _load_event_table(deps)
        dry_curves = build_dry_curves(dry_flow)
        _save_curves(deps, dry_curves)
        result = analyze_rdii(flow, dry_curves, events, event_ids or [])
        table = result["rdii_total"]
        _save_rdii_curves(deps, result["rdii_curve_data"])
        if table.empty:
            deps.session.unavailable_event_ids = sorted(
                set(deps.session.unavailable_event_ids).union(event_ids or [])
            )
            summary = (
                f"RDII 分析无可用数据：场次 {event_ids} 与点位 {points or ['全部点位']} 的监测数据"
                "无时间重叠，无法计算 RDII。"
            )
            _write_sheet(deps.paths.combined_xlsx, "RDII总量统计", table)
            return summary, {"table": [], "chart_paths": {}, "no_data": True, "event_ids": event_ids}
        rain = io.load_rain(root=deps.paths.root)
        chart_paths = _save_rdii_curve_pngs(
            result["rdii_curve_data"],
            rain,
            events,
            deps.paths.outputs,
            selected_events=event_ids,
        )
        _write_sheet(deps.paths.combined_xlsx, "RDII总量统计", table)
        chart_count = sum(len(point_paths) for point_paths in chart_paths.values())
        summary = f"RDII 分析完成：场次 {event_ids}，输出 {len(table)} 行统计，生成 {chart_count} 张 RDII 曲线图。"
        return summary, {"table": table.to_dict(orient="records"), "chart_paths": chart_paths, "no_data": False}

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
        sites = io.load_sites(root=deps.paths.root)
        flow = pd.DataFrame()
        events = pd.DataFrame()
        event_table = pd.DataFrame()
        if scope in {"rainy", "all"} and event_ids:
            flow = io.load_flow(root=deps.paths.root)
            events = _load_event_table(deps)
            event_table = analyze_event_response(flow, _load_event_table(deps), event_ids)
        result = assess_risk(
            dry_stats,
            event_table,
            scope=scope,
            sites=sites,
            flow=flow,
            events=events,
            event_ids=event_ids,
        )
        if not result["dry_risk"].empty:
            _write_sheet(deps.paths.combined_xlsx, "旱天风险", result["dry_risk"])
        if not result["rainy_risk"].empty:
            _write_sheet(deps.paths.combined_xlsx, "雨天溢流风险", result["rainy_risk"])
        summary = f"风险评估完成：旱天风险 {len(result['dry_risk'])} 行，雨天风险 {len(result['rainy_risk'])} 行。"
        return summary, {key: df.to_dict(orient="records") for key, df in result.items()}

    return _run(deps, "assess_risk", work, params=params)


def generate_report_impl(deps: AgentDeps, sections: list[str] | None = None, event_ids: list[int] | None = None) -> ToolResult:
    sections = sections or ["监测概况", "降雨分析", "旱天排污规律统计分析", "污水系统运行风险分析"]
    selected_event_ids = event_ids or deps.session.selected_event_ids
    unavailable = sorted(set(selected_event_ids).intersection(deps.session.unavailable_event_ids))
    if unavailable:
        return error(
            f"无法生成可靠报告：场次 {unavailable} 与监测数据无时间重叠，缺少事件响应、RDII 和雨天风险依据。"
        )
    rainy_sections = {"降雨分析", "雨天事件统计", "事件响应", "RDII", "雨天风险", "雨天溢流风险"}
    if rainy_sections.intersection(sections):
        precheck = _require_event_ids(deps, event_ids)
        if precheck:
            return precheck
    params = {"sections": sections, "event_ids": selected_event_ids}

    def work() -> tuple[str, dict[str, Any]]:
        summaries: list[str] = []
        data_check = check_data_impl(deps)
        summaries.append(data_check["summary"])
        if any(section in sections for section in ("降雨分析", "雨天事件统计", "事件响应", "RDII", "雨天风险", "雨天溢流风险")):
            rain = analyze_rainfall_impl(deps)
            summaries.append(rain["summary"])
        if any(section in sections for section in ("旱天排污规律统计分析", "排污规律", "排污规律分析", "旱天分析")):
            patterns = analyze_patterns_impl(deps)
            summaries.append(patterns["summary"])
        if any(section in sections for section in ("污水系统运行风险分析", "运行风险分析", "风险评估", "旱天风险", "雨天风险", "溢流风险")):
            risk = assess_risk_impl(deps, scope="dry")
            summaries.append(risk["summary"])
        output_file = deps.paths.outputs / "分析报告.docx"
        result = build_report(
            output_file,
            "排水监测数据分析报告",
            summaries,
            template_file=deps.paths.report_template_file,
            combined_xlsx=deps.paths.combined_xlsx,
            site_info_file=deps.paths.site_info_file,
            outputs_dir=deps.paths.outputs,
            sections=sections,
        )
        summary = (
            f"报告生成完成：{_rel(deps, output_file)}，"
            f"模板匹配 {len(result['templated_sections'])} 个模块，"
            f"补充生成 {len(result['generated_sections'])} 个模块。"
        )
        return summary, result

    return _run(deps, "generate_report", work, params=params)
