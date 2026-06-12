from __future__ import annotations

import pandas as pd


def analyze_event_response(flow: pd.DataFrame, events: pd.DataFrame, event_ids: list[int]) -> pd.DataFrame:
    if flow.empty or events.empty or not event_ids:
        return pd.DataFrame()
    rows = []
    selected = events[events["event_id"].isin(event_ids)]
    for _, event in selected.iterrows():
        start = pd.to_datetime(event["start_time"])
        end = pd.to_datetime(event["end_time"])
        window = flow[(flow["timestamp"] >= start) & (flow["timestamp"] <= end)]
        for point_id, point_df in window.groupby("point_id", sort=True):
            peak_idx = point_df["flow_lps"].idxmax() if not point_df.empty else None
            peak_time = point_df.loc[peak_idx, "timestamp"] if peak_idx is not None else pd.NaT
            rows.append(
                {
                    "event_id": int(event["event_id"]),
                    "point_id": point_id,
                    "avg_flow_lps": float(point_df["flow_lps"].mean()),
                    "peak_flow_lps": float(point_df["flow_lps"].max()),
                    "avg_level_m": float(point_df["level_m"].mean()),
                    "peak_level_m": float(point_df["level_m"].max()),
                    "avg_velocity_mps": float(point_df["velocity_mps"].mean()),
                    "peak_time": peak_time,
                    "response_hours": (peak_time - start).total_seconds() / 3600 if pd.notna(peak_time) else None,
                }
            )
    return pd.DataFrame(rows)

