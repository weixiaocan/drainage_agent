from __future__ import annotations

import pandas as pd

from .event_response import analyze_event_response


def analyze_rdii(flow: pd.DataFrame, dry_flow: pd.DataFrame, events: pd.DataFrame, event_ids: list[int]) -> pd.DataFrame:
    response = analyze_event_response(flow, events, event_ids)
    if response.empty:
        return pd.DataFrame()
    baseline = dry_flow.groupby("point_id")["flow_lps"].mean().rename("baseline_flow_lps") if not dry_flow.empty else pd.Series(dtype="float64")
    result = response.merge(baseline, on="point_id", how="left")
    result["baseline_flow_lps"] = result["baseline_flow_lps"].fillna(0.0)
    result["rdii_peak_lps"] = (result["peak_flow_lps"] - result["baseline_flow_lps"]).clip(lower=0.0)
    result["rdii_avg_lps"] = (result["avg_flow_lps"] - result["baseline_flow_lps"]).clip(lower=0.0)
    return result[
        [
            "event_id",
            "point_id",
            "baseline_flow_lps",
            "avg_flow_lps",
            "peak_flow_lps",
            "rdii_avg_lps",
            "rdii_peak_lps",
        ]
    ]

