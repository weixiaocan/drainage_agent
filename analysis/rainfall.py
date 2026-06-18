from __future__ import annotations

import pandas as pd


def classify_rain(total_mm: float) -> str:
    if total_mm >= 50:
        return "暴雨"
    if total_mm >= 25:
        return "大雨"
    if total_mm >= 10:
        return "中雨"
    if total_mm > 0:
        return "小雨"
    return "无雨"


def daily_rainfall(rain: pd.DataFrame) -> pd.DataFrame:
    if rain.empty:
        return pd.DataFrame(columns=["date", "rain_mm", "is_rainy"])
    df = rain.copy()
    df["date"] = df["timestamp"].dt.date
    daily = df.groupby("date", as_index=False)["rain_mm"].sum()
    daily["is_rainy"] = daily["rain_mm"] > 0
    return daily


def rainfall_events(rain: pd.DataFrame, gap_hours: int = 12, min_rainfall: float = 1.0) -> pd.DataFrame:
    if rain.empty:
        return pd.DataFrame(
            columns=[
                "event_id",
                "start_time",
                "end_time",
                "total_rain_mm",
                "duration_h",
                "peak_intensity_mmh",
                "max_3h_rain_mm",
                "max_6h_rain_mm",
                "max_12h_rain_mm",
                "max_24h_rain_mm",
                "avg_intensity_mmh",
                "rain_level",
            ]
        )
    rain_sorted = rain.sort_values("timestamp").copy()
    rainy = rain_sorted[rain_sorted["rain_mm"] > 0].copy()
    if rainy.empty:
        return rainfall_events(pd.DataFrame(), gap_hours=gap_hours, min_rainfall=min_rainfall)
    groups: list[list[pd.Series]] = []
    current: list[pd.Series] = []
    last_ts = None
    for _, row in rainy.iterrows():
        if last_ts is None or (row["timestamp"] - last_ts).total_seconds() <= gap_hours * 3600:
            current.append(row)
        else:
            groups.append(current)
            current = [row]
        last_ts = row["timestamp"]
    if current:
        groups.append(current)
    rows = []
    event_id = 1
    for group in groups:
        rainy_event = pd.DataFrame(group)
        start = rainy_event["timestamp"].min()
        end = rainy_event["timestamp"].max()
        event = rain_sorted[(rain_sorted["timestamp"] >= start) & (rain_sorted["timestamp"] <= end)].copy()
        total = float(event["rain_mm"].sum())
        if total < min_rainfall:
            continue
        duration_h = max((end - start).total_seconds() / 3600, 1.0)
        rows.append(
            {
                "event_id": event_id,
                "start_time": start.strftime("%Y-%m-%d %H:%M"),
                "end_time": end.strftime("%Y-%m-%d %H:%M"),
                "total_rain_mm": round(total, 2),
                "duration_h": round(duration_h, 2),
                "peak_intensity_mmh": round(float(event["rain_mm"].max()), 2),
                "max_3h_rain_mm": round(float(event["rain_mm"].rolling(3).sum().max()), 2),
                "max_6h_rain_mm": round(float(event["rain_mm"].rolling(6).sum().max()), 2),
                "max_12h_rain_mm": round(float(event["rain_mm"].rolling(12).sum().max()), 2),
                "max_24h_rain_mm": round(float(event["rain_mm"].rolling(24).sum().max()), 2),
                "avg_intensity_mmh": round(total / duration_h, 2),
                "rain_level": classify_rain(total),
            }
        )
        event_id += 1
    return pd.DataFrame(rows)


def analyze_rainfall(rain: pd.DataFrame, gap_hours: int = 12) -> dict[str, pd.DataFrame]:
    return {
        "daily": daily_rainfall(rain),
        "events": rainfall_events(rain, gap_hours=gap_hours),
    }
