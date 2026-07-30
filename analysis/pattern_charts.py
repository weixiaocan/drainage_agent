from __future__ import annotations

from pathlib import Path

import pandas as pd


def _daily_curve_frame(
    dry_flow: pd.DataFrame,
    point_id: str,
    value_col: str,
) -> pd.DataFrame:
    if dry_flow.empty or value_col not in dry_flow.columns:
        return pd.DataFrame()

    point_flow = dry_flow[
        dry_flow["point_id"].astype(str) == str(point_id)
    ].copy()
    if point_flow.empty:
        return pd.DataFrame()

    point_flow["timestamp"] = pd.to_datetime(
        point_flow["timestamp"], errors="coerce"
    )
    point_flow = point_flow.dropna(subset=["timestamp"]).sort_values("timestamp")
    if point_flow.empty:
        return pd.DataFrame()

    day_index = pd.date_range("00:00:00", "23:59:00", freq="min")
    daily_df = pd.DataFrame(index=day_index)
    grouped = point_flow.groupby(
        point_flow["timestamp"].dt.strftime("%Y-%m-%d"),
        sort=True,
    )
    for date, day_data in grouped:
        values = pd.to_numeric(
            day_data[value_col], errors="coerce"
        ).dropna().to_numpy()
        if len(values) == 0:
            continue
        padded = [0.0] * 1440
        limit = min(len(values), 1440)
        padded[:limit] = values[:limit]
        daily_df[str(date)] = padded
    return daily_df


def _plot_pattern_curve(
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
    curve_values = pd.to_numeric(
        plot_curve[value_col], errors="coerce"
    ).to_numpy()
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
        daily_df[day].plot(
            ax=ax,
            color="#D3D3D3",
            label=label,
            legend=bool(label),
            alpha=0.5,
        )
    curve_series.plot(
        ax=ax,
        color="#1E90FF",
        label=curve_label,
        legend=True,
    )

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


def save_pattern_curve_pngs(
    curves: dict[str, pd.DataFrame],
    dry_flow: pd.DataFrame,
    output_dir: Path,
    scope_prefix: str,
) -> dict[str, list[str]]:
    target_dir = Path(output_dir) / scope_prefix
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[str]] = {}
    if not curves:
        return saved
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return saved

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "SimSun",
        "Microsoft YaHei",
        "SimHei",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    for point_id, curve in curves.items():
        point_paths: list[str] = []
        plot_df = curve.copy()
        if "flow_lps" in plot_df.columns:
            flow_path = target_dir / f"{point_id}_流量特征曲线.png"
            if _plot_pattern_curve(
                _daily_curve_frame(dry_flow, point_id, "flow_lps"),
                plot_df,
                "flow_lps",
                flow_path,
                "每日流量",
                "流量特征曲线_总体",
                "流量/(L/s)",
            ):
                point_paths.append(str(flow_path))

        if "level_m" in plot_df.columns:
            level_path = target_dir / f"{point_id}_液位特征曲线.png"
            if _plot_pattern_curve(
                _daily_curve_frame(dry_flow, point_id, "level_m"),
                plot_df,
                "level_m",
                level_path,
                "每日液位",
                "液位特征曲线",
                "液位/(m)",
            ):
                point_paths.append(str(level_path))

        saved[str(point_id)] = point_paths
    return saved
