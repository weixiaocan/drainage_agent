from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class CleanReport:
    total_days: int = 0
    kept_days: int = 0
    removed_days: int = 0
    removed_by_reason: dict[str, int] = field(default_factory=dict)
    dry_only: bool = False

    @property
    def removed_ratio(self) -> float:
        return self.removed_days / self.total_days if self.total_days else 0.0

    def summary(self) -> str:
        if self.total_days == 0:
            return "未发现可清洗的数据日。"
        reasons = "，".join(f"{k}{v}天" for k, v in self.removed_by_reason.items()) or "无"
        return (
            f"清洗覆盖 {self.total_days} 个点位日，保留 {self.kept_days} 个，"
            f"剔除 {self.removed_days} 个（{self.removed_ratio:.1%}），主要原因：{reasons}。"
        )


def daily_rain(rain: pd.DataFrame) -> pd.Series:
    if rain.empty:
        return pd.Series(dtype="float64")
    data = rain.copy()
    data["date"] = data["timestamp"].dt.date
    return data.groupby("date")["rain_mm"].sum()


def filter_flow(
    flow: pd.DataFrame,
    rain: pd.DataFrame,
    *,
    clean: bool = True,
    dry_only: bool = False,
    missing_rate_threshold: float = 0.1,
    expected_rows_per_day: int = 1440,
    rain_day_threshold: float = 2.0,
    zero_like_threshold: float = 0.02,
    high_zero_ratio_threshold: float = 0.5,
) -> tuple[pd.DataFrame, CleanReport]:
    if flow.empty:
        return flow.copy(), CleanReport(dry_only=dry_only)
    rain_by_day = daily_rain(rain)
    df = flow.copy()
    df["date"] = df["timestamp"].dt.date
    expected_by_point = df.groupby(["point_id", "date"]).size().groupby("point_id").max().to_dict()
    keep_dates: set[tuple[str, object]] = set()
    removed: dict[str, int] = {}
    total = 0
    for (point_id, date_key), day_df in df.groupby(["point_id", "date"], sort=True):
        total += 1
        reason = ""
        if clean:
            expected = max(int(expected_by_point.get(point_id, expected_rows_per_day)), 1)
            missing_rate = max(expected - len(day_df), 0) / expected
            zero_ratio = (pd.to_numeric(day_df["flow_lps"], errors="coerce").fillna(0.0) <= zero_like_threshold).mean()
            if missing_rate > missing_rate_threshold:
                reason = "缺失率超标"
            elif zero_ratio > high_zero_ratio_threshold and (day_df["flow_lps"] > zero_like_threshold).any():
                reason = "近零值比例过高"
        if not reason and dry_only and float(rain_by_day.get(date_key, 0.0)) >= rain_day_threshold:
            reason = "雨天剔除"
        if reason:
            removed[reason] = removed.get(reason, 0) + 1
        else:
            keep_dates.add((str(point_id), date_key))
    result = df[df.apply(lambda row: (str(row["point_id"]), row["date"]) in keep_dates, axis=1)].drop(columns=["date"])
    report = CleanReport(
        total_days=total,
        kept_days=len(keep_dates),
        removed_days=total - len(keep_dates),
        removed_by_reason=removed,
        dry_only=dry_only,
    )
    return result, report
