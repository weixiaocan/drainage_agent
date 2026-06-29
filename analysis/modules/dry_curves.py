from __future__ import annotations

import pandas as pd


def build_dry_curves(flow: pd.DataFrame, smooth_window_minutes: int = 20) -> dict[str, pd.DataFrame]:
    curves: dict[str, pd.DataFrame] = {}
    if flow.empty:
        return curves
    df = flow.copy()
    df["date"] = df["timestamp"].dt.date
    df["minute_of_day"] = df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute
    for point_id, point_df in df.groupby("point_id", sort=True):
        day_frames = []
        for _, day_df in point_df.groupby("date", sort=True):
            minute_values = (
                day_df.groupby("minute_of_day")[["flow_lps", "level_m", "velocity_mps"]]
                .mean()
                .reindex(range(1440))
            )
            if minute_values.isna().any().any():
                minute_values = minute_values.interpolate(method="linear", limit_direction="both")
            if len(minute_values) != 1440 or minute_values.isna().any().any():
                continue
            day_frames.append(minute_values)
        if not day_frames:
            continue
        curve = sum(day_frames) / len(day_frames)
        if smooth_window_minutes > 1:
            curve = curve.rolling(smooth_window_minutes, min_periods=1, center=True).mean()
        curve = curve.reset_index()
        curve = curve.rename(columns={"index": "minute_of_day"})
        curves[str(point_id)] = curve
    return curves


def _site_lookup(sites: pd.DataFrame | None) -> dict[str, dict[str, float]]:
    if sites is None or sites.empty:
        return {}
    point_col = next((col for col in sites.columns if "点位" in str(col) or "监测点位" in str(col)), None)
    diameter_col = next((col for col in sites.columns if "管径" in str(col)), None)
    depth_col = next((col for col in sites.columns if "井深" in str(col)), None)
    if point_col is None:
        return {}
    result: dict[str, dict[str, float]] = {}
    for _, row in sites.iterrows():
        point_id = str(row.get(point_col, "")).strip()
        if not point_id:
            continue
        result[point_id] = {
            "diameter": float(row.get(diameter_col, 0.0)) if diameter_col and pd.notna(row.get(diameter_col)) else 0.0,
            "depth": float(row.get(depth_col, 0.0)) if depth_col and pd.notna(row.get(depth_col)) else 0.0,
        }
    return result


def dry_statistics(flow: pd.DataFrame, sites: pd.DataFrame | None = None) -> pd.DataFrame:
    if flow.empty:
        return pd.DataFrame()
    df = flow.copy()
    df["date"] = df["timestamp"].dt.date
    rows = []
    site_info = _site_lookup(sites)
    for point_id, point_df in df.groupby("point_id", sort=True):
        daily = (
            point_df.groupby("date")
            .agg(
                daily_flow_lps=("flow_lps", "mean"),
                daily_level_m=("level_m", "mean"),
                daily_max_level_m=("level_m", "max"),
                daily_velocity_mps=("velocity_mps", "mean"),
            )
            .dropna(subset=["daily_flow_lps"])
        )
        if daily.empty:
            continue
        info = site_info.get(str(point_id), {"diameter": 0.0, "depth": 0.0})
        diameter = info["diameter"]
        depth = info["depth"]
        max_level = float(point_df["level_m"].max())
        rows.append(
            {
                "point_id": point_id,
                "daily_flow_m3d": round(float(daily["daily_flow_lps"].mean() * 86.4), 2),
                "max_flow_lps": round(float(daily["daily_flow_lps"].max()), 2),
                "min_flow_lps": round(float(daily["daily_flow_lps"].min()), 2),
                "max_level_m": round(max_level, 2),
                "max_fullness": round(max_level / diameter * 1000, 2) if diameter > 0 else 0,
                "overflow_risk": round(max_level / depth, 2) if depth > 0 else 0,
                "avg_velocity_mps": round(float(daily["daily_velocity_mps"].mean()), 6),
                "avg_level_m": round(float(point_df["level_m"].mean()), 2),
            }
        )
    return pd.DataFrame(rows)
