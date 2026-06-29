from __future__ import annotations

import re

import pandas as pd


def check_data(flow: pd.DataFrame) -> pd.DataFrame:
    if flow.empty:
        return pd.DataFrame(columns=["point_id", "record_count", "monitoring_days", "theoretical_count", "collection_rate"])
    rows = []
    for point_id, point_df in flow.groupby("point_id", sort=False):
        start = point_df["timestamp"].min()
        end = point_df["timestamp"].max()
        days = max((end.normalize() - start.normalize()).days + 1, 1)
        expected = days * 1440
        rows.append(
            {
                "point_id": point_id,
                "record_count": len(point_df),
                "monitoring_days": days,
                "theoretical_count": expected,
                "collection_rate": min(len(point_df) / expected, 1.0) if expected else 0.0,
            }
        )
    result = pd.DataFrame(rows)
    result["_sort_key"] = result["point_id"].astype(str).map(_point_sort_key)
    return result.sort_values("_sort_key").drop(columns="_sort_key").reset_index(drop=True)


def _point_sort_key(point_id: str) -> tuple[str, int, str]:
    match = re.match(r"^([A-Za-z]+)(\d+)$", str(point_id).strip())
    if match:
        return match.group(1), int(match.group(2)), str(point_id)
    return str(point_id), -1, str(point_id)
