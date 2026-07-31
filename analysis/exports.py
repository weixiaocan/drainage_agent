"""确定性结果导出：把分析运行结果渲染成用户可下载的 CSV/PNG 文件。

只接受 ``exports=("table_csv", "charts_png")`` 中显式声明的导出类型，
产物写入对应运行的 ``results/{algorithm}/{run_id}/`` 目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ALGORITHM_TABLE_LABELS = {
    "data_quality": "数据质量结果",
    "patterns": "排污规律结果",
    "rainfall": "降雨分析结果",
    "event_response": "降雨响应结果",
    "rdii": "RDII分析结果",
    "risk": "风险分析结果",
}

TABLE_KEY_LABELS = {
    "daily": "日降雨统计",
    "events": "降雨场次",
    "dry_analysis": "旱天统计",
    "dry_risk": "旱天风险",
    "rainy_risk": "雨天风险",
}


def render_exports(
    batch_root: Path,
    algorithm: str,
    run_id: str,
    exports: Iterable[str],
    context: dict[str, Any],
) -> list[str]:
    out_dir = batch_root / "results" / algorithm / run_id
    scope_prefix = str(context.get("scope_prefix") or "全网_全时段")
    written: list[Path] = []
    requested = set(exports)
    if "table_csv" in requested:
        for key, frame in (context.get("tables") or {}).items():
            if frame is None or frame.empty:
                continue
            written.append(
                _write_table_csv(out_dir, algorithm, key, frame)
            )
    if "charts_png" in requested:
        written.extend(_render_charts(algorithm, out_dir, scope_prefix, context))
    return [
        path.relative_to(batch_root).as_posix()
        for path in written
        if path.is_file()
    ]


def _write_table_csv(
    out_dir: Path,
    algorithm: str,
    key: str,
    frame: pd.DataFrame,
) -> Path:
    label = ALGORITHM_TABLE_LABELS.get(algorithm, algorithm)
    suffix = "" if key == "table" else f"_{TABLE_KEY_LABELS.get(key, key)}"
    path = out_dir / f"{label}{suffix}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _render_charts(
    algorithm: str,
    out_dir: Path,
    scope_prefix: str,
    context: dict[str, Any],
) -> list[Path]:
    if algorithm == "patterns":
        from analysis.pattern_charts import save_pattern_curve_pngs

        saved = save_pattern_curve_pngs(
            context.get("curves") or {},
            context.get("dry_flow") if context.get("dry_flow") is not None else pd.DataFrame(),
            out_dir / "特征曲线图",
            scope_prefix,
        )
        return [Path(p) for paths in saved.values() for p in paths]
    if algorithm == "rainfall":
        daily = context.get("daily")
        if daily is None:
            return []
        saved = save_rainfall_png_charts(daily, out_dir / "降雨分析图", scope_prefix)
        return [Path(p) for p in saved.values()]
    if algorithm == "rdii":
        saved = save_rdii_curve_pngs(
            context.get("rdii_curve_data") or {},
            context.get("rain") if context.get("rain") is not None else pd.DataFrame(),
            context.get("events") if context.get("events") is not None else pd.DataFrame(),
            out_dir,
            selected_events=context.get("event_ids"),
        )
        return [Path(p) for paths in saved.values() for p in paths.values()]
    return []


def save_rainfall_png_charts(
    daily: pd.DataFrame,
    output_dir: Path,
    scope_prefix: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "daily_bar": output_dir / f"{scope_prefix}_日降雨量时间序列图.png",
        "rainy_ratio": output_dir / f"{scope_prefix}_降雨日占比饼图.png",
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
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "SimHei", "Microsoft YaHei", "DejaVu Sans"]
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


def save_rdii_curve_pngs(
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

    mpl.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "SimHei"]
    mpl.rcParams["font.serif"] = ["Noto Serif CJK SC", "SimHei"]
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
