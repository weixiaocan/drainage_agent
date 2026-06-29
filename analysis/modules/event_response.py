from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EventStatsConfig:
    rain_effect_delay_hours: float = 12.0


def _event_ids(events: pd.DataFrame, selected: list[int] | None) -> list[int]:
    ids = sorted(int(value) for value in events["event_id"].dropna().unique())
    if selected:
        wanted = set(int(value) for value in selected)
        ids = [value for value in ids if value in wanted]
    return ids


def analyze_event_response(
    flow: pd.DataFrame,
    events: pd.DataFrame,
    event_ids: list[int] | None,
    config: EventStatsConfig | None = None,
) -> pd.DataFrame:
    cfg = config or EventStatsConfig()
    if flow.empty or events.empty:
        return pd.DataFrame()

    selected_ids = []
    for event_id in _event_ids(events, event_ids):
        event = events[events["event_id"] == event_id].iloc[0]
        start = pd.to_datetime(event["start_time"], errors="coerce")
        end = pd.to_datetime(event["end_time"], errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        end = end + pd.Timedelta(hours=cfg.rain_effect_delay_hours)
        if flow[(flow["timestamp"] >= start) & (flow["timestamp"] <= end)].empty:
            continue
        selected_ids.append(event_id)

    if not selected_ids:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for point_id, point_df in flow.groupby("point_id", sort=True):
        row: dict[str, object] = {"point_id": point_id}
        for event_id in selected_ids:
            event = events[events["event_id"] == event_id].iloc[0]
            start = pd.to_datetime(event["start_time"], errors="coerce")
            end = pd.to_datetime(event["end_time"], errors="coerce")
            if pd.isna(start) or pd.isna(end):
                row[f"场次{event_id}_最大液位(m)"] = np.nan
                row[f"场次{event_id}_平均流量(m³/d)"] = np.nan
                row[f"场次{event_id}_峰值流量(L/s)"] = np.nan
                continue
            end = end + pd.Timedelta(hours=cfg.rain_effect_delay_hours)
            event_df = point_df[(point_df["timestamp"] >= start) & (point_df["timestamp"] <= end)]
            if event_df.empty:
                row[f"场次{event_id}_最大液位(m)"] = np.nan
                row[f"场次{event_id}_平均流量(m³/d)"] = np.nan
                row[f"场次{event_id}_峰值流量(L/s)"] = np.nan
                continue
            row[f"场次{event_id}_最大液位(m)"] = round(float(event_df["level_m"].max()), 2)
            row[f"场次{event_id}_平均流量(m³/d)"] = round(float(event_df["flow_lps"].mean() * 86.4), 2)
            row[f"场次{event_id}_峰值流量(L/s)"] = round(float(event_df["flow_lps"].max()), 2)
        rows.append(row)
    return pd.DataFrame(rows)
