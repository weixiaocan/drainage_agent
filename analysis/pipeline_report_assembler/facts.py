"""Structured facts used by report sections and LLM prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from .data_context import ReportDataContext, clean_point_id


MONITORING_PERIOD_PLACEHOLDER = "____/__/__日-____/__/__日"
OPERATION_PERIOD_PLACEHOLDER = "____年__月__日至__月__日"


@dataclass
class PatternPointFact:
    point_id: str
    category: int
    category_name: str = ""
    kz: Any = ""
    peak_valley_ratio: Any = ""
    peak_count: Any = ""
    peak_periods: str = ""
    valley_periods: str = ""
    diagnosis_reason: str = ""
    description: str = ""


@dataclass
class ReportFacts:
    point_ids: list[str]
    point_count: int
    device_count: int
    monitoring_period_text: str
    operation_period_text: str
    record_count: int = 0
    record_count_wan: int = 0
    collection_min: float = 0.0
    collection_max: float = 0.0
    collection_all_over_99: bool = False
    collection_999_count: int = 0
    full_collection_points: list[str] = field(default_factory=list)
    total_days: int = 0
    rainy_days: int = 0
    non_rainy_days: int = 0
    total_rain_mm: float = 0.0
    max_daily_rain_mm: float = 0.0
    max_daily_rain_date: str = ""
    rainfall_event_count: int = 0
    event_total_rain_mm: float = 0.0
    max_event_rain_mm: float = 0.0
    pattern_groups: Dict[int, list[str]] = field(default_factory=dict)
    pattern_details: list[PatternPointFact] = field(default_factory=list)
    risk_counts: Dict[str, int] = field(default_factory=dict)
    baseinfo: Dict[str, Any] = field(default_factory=dict)


def build_report_facts(context: ReportDataContext, baseinfo_path: Path | None = None) -> ReportFacts:
    baseinfo = _load_baseinfo(baseinfo_path) if baseinfo_path else {}
    collection = context.df("data_collection")
    rainfall_daily = context.df("rainfall_daily")
    rainfall_events = context.df("rainfall_events")
    pattern = context.df("pattern_analysis")
    dry_risk = context.df("dry_risk")

    facts = ReportFacts(
        point_ids=context.point_ids,
        point_count=len(context.point_ids),
        device_count=len(context.point_ids),
        monitoring_period_text=_format_monitoring_period(baseinfo),
        operation_period_text=_format_operation_period(baseinfo),
        baseinfo=baseinfo,
    )

    _fill_collection_facts(facts, collection)
    _fill_rainfall_facts(facts, rainfall_daily, rainfall_events)
    _fill_pattern_facts(facts, pattern)
    _fill_risk_facts(facts, dry_risk)
    return facts


def _load_baseinfo(path: Path) -> Dict[str, Any]:
    if not Path(path).exists():
        return {}
    result: Dict[str, Any] = {}
    try:
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            df = xl.parse(sheet, header=None)
            for _, row in df.iterrows():
                if len(row) < 2:
                    continue
                key = str(row.iloc[0]).strip()
                if key and key.lower() != "nan":
                    result[key] = row.iloc[1]
    except Exception:
        return {}
    return result


def _format_monitoring_period(baseinfo: Dict[str, Any]) -> str:
    start = _first_value(baseinfo, "监测开始时间", "监测开始日期", "开始时间")
    end = _first_value(baseinfo, "监测结束时间", "监测结束日期", "结束时间")
    if not start or not end:
        return MONITORING_PERIOD_PLACEHOLDER
    return f"{_fmt_date_slash(start)}日-{_fmt_date_slash(end)}日"


def _format_operation_period(baseinfo: Dict[str, Any]) -> str:
    start = _first_value(baseinfo, "运维开始时间", "运维开始日期")
    end = _first_value(baseinfo, "运维结束时间", "运维结束日期")
    if not start or not end:
        return OPERATION_PERIOD_PLACEHOLDER
    start_dt = pd.to_datetime(start, errors="coerce")
    end_dt = pd.to_datetime(end, errors="coerce")
    if pd.isna(start_dt) or pd.isna(end_dt):
        return OPERATION_PERIOD_PLACEHOLDER
    return f"{start_dt.year}年{start_dt.month}月{start_dt.day}日至{end_dt.month}月{end_dt.day}日"


def _fill_collection_facts(facts: ReportFacts, df: pd.DataFrame) -> None:
    if df.empty:
        return
    if "record_count" in df.columns:
        facts.record_count = int(pd.to_numeric(df["record_count"], errors="coerce").fillna(0).sum())
        facts.record_count_wan = int(facts.record_count // 10000)
    if "collection_rate" in df.columns:
        rates = pd.to_numeric(df["collection_rate"], errors="coerce").fillna(0)
        if rates.max() <= 1:
            rates = rates * 100
        facts.collection_min = round(float(rates.min()), 2)
        facts.collection_max = round(float(rates.max()), 2)
        facts.collection_all_over_99 = bool((rates >= 99).all())
        facts.collection_999_count = int((rates >= 99.9).sum())
        if "point_id" in df.columns:
            facts.full_collection_points = [
                clean_point_id(point)
                for point in df.loc[rates >= 100, "point_id"].dropna().tolist()
            ]


def _fill_rainfall_facts(facts: ReportFacts, daily: pd.DataFrame, events: pd.DataFrame) -> None:
    if not daily.empty and "daily_rain_mm" in daily.columns:
        rain = pd.to_numeric(daily["daily_rain_mm"], errors="coerce").fillna(0)
        rainy = daily[rain > 0].copy()
        facts.total_days = len(daily)
        facts.rainy_days = len(rainy)
        facts.non_rainy_days = facts.total_days - facts.rainy_days
        facts.total_rain_mm = round(float(rain.sum()), 1)
        if len(rainy) > 0:
            rainy_rain = pd.to_numeric(rainy["daily_rain_mm"], errors="coerce").fillna(0)
            max_idx = rainy_rain.idxmax()
            facts.max_daily_rain_mm = round(float(rainy_rain.max()), 1)
            if "date" in rainy.columns:
                facts.max_daily_rain_date = _fmt_date_cn(rainy.loc[max_idx, "date"])
    if not events.empty:
        facts.rainfall_event_count = len(events)
        if "total_rain_mm" in events.columns:
            total = pd.to_numeric(events["total_rain_mm"], errors="coerce").fillna(0)
            facts.event_total_rain_mm = round(float(total.sum()), 1)
            facts.max_event_rain_mm = round(float(total.max()), 1)


def _fill_pattern_facts(facts: ReportFacts, df: pd.DataFrame) -> None:
    groups = {1: [], 2: [], 3: []}
    details: list[PatternPointFact] = []
    if df.empty:
        facts.pattern_groups = groups
        facts.pattern_details = details
        return
    class_col = "category" if "category" in df.columns else "分类"
    point_col = "point_id" if "point_id" in df.columns else "点位编号"
    if class_col not in df.columns or point_col not in df.columns:
        facts.pattern_groups = groups
        facts.pattern_details = details
        return
    for _, row in df.iterrows():
        class_id = _parse_category(row.get(class_col))
        if class_id is None:
            continue
        if class_id in groups:
            point_id = clean_point_id(row.get(point_col))
            groups[class_id].append(point_id)
            details.append(
                PatternPointFact(
                    point_id=point_id,
                    category=class_id,
                    category_name=str(_row_value(row, "category_name", "分类名称") or ""),
                    kz=_row_value(row, "kz", "Kz值", "Kz"),
                    peak_valley_ratio=_row_value(row, "peak_valley_ratio", "峰谷比"),
                    peak_count=_row_value(row, "peak_count", "峰数量"),
                    peak_periods=str(_row_value(row, "peak_periods", "波峰时段") or ""),
                    valley_periods=str(_row_value(row, "valley_periods", "波谷时段") or ""),
                    diagnosis_reason=str(_row_value(row, "diagnosis_reason", "诊断理由") or ""),
                    description=str(_row_value(row, "description", "排污规律描述") or ""),
                )
            )
    facts.pattern_groups = groups
    facts.pattern_details = details


def _fill_risk_facts(facts: ReportFacts, df: pd.DataFrame) -> None:
    if df.empty:
        facts.risk_counts = {}
        return
    result: Dict[str, int] = {}
    for col in ("running_risk", "overflow_risk", "silting_risk"):
        if col in df.columns:
            counts = df[col].fillna("").astype(str).value_counts()
            for key, value in counts.items():
                if key:
                    result[f"{col}:{key}"] = int(value)
    facts.risk_counts = result


def _first_value(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None and not pd.isna(value) and str(value).strip():
            return value
    return None


def _row_value(row: pd.Series, *keys: str) -> Any:
    for key in keys:
        if key in row.index:
            value = row.get(key)
            if value is not None and not pd.isna(value) and str(value).strip():
                return value
    return None


def _parse_category(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    for category in (1, 2, 3):
        if text == str(category) or f"分类{category}" in text or f"第{category}类" in text:
            return category
    try:
        category = int(float(text))
    except ValueError:
        return None
    return category if category in (1, 2, 3) else None


def _fmt_date_slash(value: Any) -> str:
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return str(value)
    return f"{dt.year}/{dt.month}/{dt.day}"


def _fmt_date_cn(value: Any) -> str:
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return str(value)[:10]
    return f"{dt.year}年{dt.month}月{dt.day}日"
