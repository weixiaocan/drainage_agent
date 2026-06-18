from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RDIIConfig:
    rain_effect_delay_hours: float = 12.0


def _event_ids(events: pd.DataFrame, selected: list[int] | None) -> list[int]:
    ids = sorted(int(value) for value in events["event_id"].dropna().unique())
    if selected:
        wanted = set(int(value) for value in selected)
        ids = [value for value in ids if value in wanted]
    return ids


def _dry_flow_segment(curve: pd.DataFrame, start: pd.Timestamp, length: int) -> np.ndarray:
    if curve.empty or length <= 0:
        return np.array([], dtype=float)
    ordered = curve.sort_values("minute_of_day")
    dry_flow = pd.to_numeric(ordered["flow_lps"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if len(dry_flow) == 0:
        return np.array([], dtype=float)
    start_minute = int(start.hour * 60 + start.minute)
    repeats = int(math.ceil((start_minute + length) / len(dry_flow))) + 1
    tiled = np.tile(dry_flow, repeats)
    return tiled[start_minute : start_minute + length]


def analyze_rdii(
    flow: pd.DataFrame,
    dry_curves: dict[str, pd.DataFrame],
    events: pd.DataFrame,
    event_ids: list[int] | None = None,
    config: RDIIConfig | None = None,
) -> dict[str, Any]:
    cfg = config or RDIIConfig()
    if flow.empty or not dry_curves or events.empty:
        return {"rdii_total": pd.DataFrame(), "rdii_curve_data": {}}

    rows_by_point: dict[str, dict[str, object]] = {
        point_id: {"point_id": point_id} for point_id in sorted(dry_curves.keys())
    }
    curve_data: dict[int, dict[str, pd.DataFrame]] = {}

    for event_id in _event_ids(events, event_ids):
        event_row = events[events["event_id"] == event_id].iloc[0]
        start = pd.to_datetime(event_row["start_time"], errors="coerce")
        end = pd.to_datetime(event_row["end_time"], errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        end = end + pd.Timedelta(hours=cfg.rain_effect_delay_hours)
        event_flow = flow[(flow["timestamp"] >= start) & (flow["timestamp"] <= end)].sort_values("timestamp")
        if event_flow.empty:
            continue
        event_curves: dict[str, pd.DataFrame] = {}

        for point_id in sorted(dry_curves.keys()):
            point_flow = event_flow[event_flow["point_id"].astype(str) == str(point_id)]
            if point_flow.empty:
                rows_by_point[point_id][f"场次{event_id}"] = np.nan
                continue
            rain_flow = pd.to_numeric(point_flow["flow_lps"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
            dry_segment = _dry_flow_segment(dry_curves[point_id], pd.Timestamp(start), len(rain_flow))
            if len(dry_segment) < len(rain_flow):
                rows_by_point[point_id][f"场次{event_id}"] = np.nan
                continue
            rdii = rain_flow - dry_segment[: len(rain_flow)]
            rdii_total_m3 = float(rdii[rdii > 0].sum() * 60 / 1000)
            rows_by_point[point_id][f"场次{event_id}"] = round(rdii_total_m3, 2)
            event_curves[point_id] = pd.DataFrame(
                {
                    "timestamp": point_flow["timestamp"].to_numpy(),
                    "rain_flow_lps": rain_flow,
                    "dry_flow_lps": dry_segment[: len(rain_flow)],
                    "rdii_lps": rdii,
                }
            ).set_index("timestamp")
        curve_data[event_id] = event_curves

    return {"rdii_total": pd.DataFrame(rows_by_point.values()), "rdii_curve_data": curve_data}
