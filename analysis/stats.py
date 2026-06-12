from __future__ import annotations

import pandas as pd


METRIC_COLUMNS = {
    "流量": "flow_lps",
    "flow": "flow_lps",
    "flow_lps": "flow_lps",
    "液位": "level_m",
    "level": "level_m",
    "level_m": "level_m",
    "流速": "velocity_mps",
    "velocity": "velocity_mps",
    "velocity_mps": "velocity_mps",
}

AGG_FUNCS = {
    "均值": "mean",
    "平均": "mean",
    "mean": "mean",
    "最大": "max",
    "max": "max",
    "最小": "min",
    "min": "min",
    "总和": "sum",
    "sum": "sum",
    "计数": "count",
    "count": "count",
}


def query_stats(flow: pd.DataFrame, metrics: list[str] | None = None, aggs: list[str] | None = None) -> pd.DataFrame:
    if flow.empty:
        return pd.DataFrame()
    metric_cols = [METRIC_COLUMNS.get(m, m) for m in (metrics or ["流量", "液位", "流速"])]
    metric_cols = [c for c in metric_cols if c in flow.columns]
    agg_funcs = [AGG_FUNCS.get(a, a) for a in (aggs or ["均值", "最大", "最小"])]
    grouped = flow.groupby("point_id")[metric_cols].agg(agg_funcs)
    grouped.columns = [f"{metric}_{agg}" for metric, agg in grouped.columns]
    return grouped.reset_index()


def check_data(flow: pd.DataFrame) -> pd.DataFrame:
    if flow.empty:
        return pd.DataFrame(columns=["point_id", "record_count", "monitoring_days", "missing_cells", "collection_rate"])
    rows = []
    for point_id, point_df in flow.groupby("point_id", sort=True):
        start = point_df["timestamp"].min()
        end = point_df["timestamp"].max()
        days = max((end.normalize() - start.normalize()).days + 1, 1)
        expected = days * 1440
        rows.append(
            {
                "point_id": point_id,
                "record_count": len(point_df),
                "monitoring_days": days,
                "start_time": start,
                "end_time": end,
                "missing_cells": int(point_df[["flow_lps", "level_m", "velocity_mps"]].isna().sum().sum()),
                "collection_rate": min(len(point_df) / expected, 1.0) if expected else 0.0,
            }
        )
    return pd.DataFrame(rows)

