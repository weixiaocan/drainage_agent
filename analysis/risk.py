from __future__ import annotations

import pandas as pd


def _level(value: float, warn: float, high: float) -> str:
    if value >= high:
        return "高"
    if value >= warn:
        return "中"
    return "低"


def assess_risk(dry_stats: pd.DataFrame, event_response: pd.DataFrame | None = None, scope: str = "all") -> dict[str, pd.DataFrame]:
    dry = pd.DataFrame()
    rainy = pd.DataFrame()
    if scope in {"all", "dry"} and not dry_stats.empty:
        dry = dry_stats.copy()
        dry["silting_risk"] = dry["avg_velocity_mps"].apply(lambda v: "高" if v < 0.3 else ("中" if v < 0.6 else "低"))
        dry["overflow_risk"] = dry["max_level_m"].apply(lambda v: _level(float(v), 1.5, 2.5))
        dry["running_risk"] = dry.apply(
            lambda row: "高" if "高" in {row["silting_risk"], row["overflow_risk"]} else ("中" if "中" in {row["silting_risk"], row["overflow_risk"]} else "低"),
            axis=1,
        )
    if scope in {"all", "rainy"} and event_response is not None and not event_response.empty:
        rainy = event_response.copy()
        rainy["overflow_risk"] = rainy["peak_level_m"].apply(lambda v: _level(float(v), 1.8, 2.8))
    return {"dry_risk": dry, "rainy_risk": rainy}

