from __future__ import annotations

import pandas as pd


def build_dry_curves(flow: pd.DataFrame, smooth_window_minutes: int = 20) -> dict[str, pd.DataFrame]:
    curves: dict[str, pd.DataFrame] = {}
    if flow.empty:
        return curves
    df = flow.copy()
    df["minute_of_day"] = df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute
    for point_id, point_df in df.groupby("point_id", sort=True):
        curve = point_df.groupby("minute_of_day")[["flow_lps", "level_m", "velocity_mps"]].mean()
        if smooth_window_minutes > 1:
            curve = curve.rolling(smooth_window_minutes, min_periods=1, center=True).mean()
        curve = curve.reset_index()
        curves[str(point_id)] = curve
    return curves


def dry_statistics(flow: pd.DataFrame) -> pd.DataFrame:
    if flow.empty:
        return pd.DataFrame()
    rows = []
    for point_id, point_df in flow.groupby("point_id", sort=True):
        rows.append(
            {
                "point_id": point_id,
                "daily_flow_m3d": float(point_df["flow_lps"].mean() * 86.4),
                "max_flow_lps": float(point_df["flow_lps"].max()),
                "min_flow_lps": float(point_df["flow_lps"].min()),
                "avg_level_m": float(point_df["level_m"].mean()),
                "max_level_m": float(point_df["level_m"].max()),
                "avg_velocity_mps": float(point_df["velocity_mps"].mean()),
            }
        )
    return pd.DataFrame(rows)

