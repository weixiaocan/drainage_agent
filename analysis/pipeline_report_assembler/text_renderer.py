"""Semantic text replacement for the report template."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from docx import Document

from .data_context import ReportDataContext


def render_text(doc: Document, context: ReportDataContext) -> int:
    """Replace known report text snippets from canonical context fields."""
    values = _build_values(context)
    replaced = 0
    for para in doc.paragraphs:
        original = para.text
        text = _replace_site_text(original, values)
        if context.has_rainfall_data:
            text = _replace_rainfall_text(text, values)
        else:
            text = _replace_no_rain_text(text)
        if text != original:
            _set_paragraph_text(para, text)
            replaced += 1
    return replaced


def _build_values(context: ReportDataContext) -> dict[str, Any]:
    collection = context.df("data_collection")
    rainfall_daily = context.df("rainfall_daily")
    rainfall_events = context.df("rainfall_events")

    values: dict[str, Any] = {"point_count": len(context.point_ids)}
    if not collection.empty:
        if "record_count" in collection.columns:
            total = pd.to_numeric(collection["record_count"], errors="coerce").fillna(0).sum()
            values["data_count_wan"] = int(total // 10000)
        if "collection_rate" in collection.columns:
            rates = pd.to_numeric(collection["collection_rate"], errors="coerce").fillna(0)
            if rates.max() <= 1:
                rates = rates * 100
            high = int((rates >= 99).sum())
            if high == len(collection):
                values["collection_rate_desc"] = f"{len(collection)}个点位的有效数据收集率均超过99%"
            else:
                values["collection_rate_desc"] = f"{high}个点位的有效数据收集率超过99%，其余点位收集率良好"

    if not rainfall_daily.empty and "daily_rain_mm" in rainfall_daily.columns:
        rain = pd.to_numeric(rainfall_daily["daily_rain_mm"], errors="coerce").fillna(0)
        rainy = rainfall_daily[rain > 0].copy()
        values["rainy_days"] = len(rainy)
        values["total_rainfall"] = round(float(rain.sum()), 1)
        values["total_days"] = len(rainfall_daily)
        if len(rainy) > 0:
            rainy_rain = pd.to_numeric(rainy["daily_rain_mm"], errors="coerce").fillna(0)
            max_idx = rainy_rain.idxmax()
            values["max_daily_rainfall"] = round(float(rainy_rain.max()), 1)
            if "date" in rainy.columns:
                values["max_rainfall_date"] = _fmt_cn_date(rainy.loc[max_idx, "date"])

    if not rainfall_events.empty:
        values["rainfall_events"] = len(rainfall_events)
        if "total_rain_mm" in rainfall_events.columns:
            total = pd.to_numeric(rainfall_events["total_rain_mm"], errors="coerce").fillna(0)
            values["event_total_rainfall"] = round(float(total.sum()), 1)
            values["max_event_rainfall"] = round(float(total.max()), 1)

    return values


def _replace_site_text(text: str, values: dict[str, Any]) -> str:
    result = text
    if values.get("point_count"):
        result = re.sub(r"共布设\d+个流量监测点位", f"共布设{values['point_count']}个流量监测点位", result)
        result = re.sub(r"\d+个监测点位在监测期间运行状态良好", f"{values['point_count']}个监测点位在监测期间运行状态良好", result)
    if values.get("data_count_wan") is not None:
        result = re.sub(r"共收集分钟级监测数据超\d+万条", f"共收集分钟级监测数据超{values['data_count_wan']}万条", result)
    if values.get("collection_rate_desc"):
        result = re.sub(r"\d+个点位的有效数据收集率[^。，]*", values["collection_rate_desc"], result)
    return result


def _replace_rainfall_text(text: str, values: dict[str, Any]) -> str:
    result = text
    replacements = [
        (r"降雨日天数为\d+天", "rainy_days", lambda v: f"降雨日天数为{v}天"),
        (r"总降雨量[\d.]+\s*mm", "total_rainfall", lambda v: f"总降雨量{v}mm"),
        (r"日最大降雨量为[\d.]+\s*mm", "max_daily_rainfall", lambda v: f"日最大降雨量为{v}mm"),
        (r"发生在[\d年月日/-]+", "max_rainfall_date", lambda v: f"发生在{v}"),
        (r"监测期内共\d+个自然日", "total_days", lambda v: f"监测期内共{v}个自然日"),
        (r"有效降雨场次\d+场", "rainfall_events", lambda v: f"有效降雨场次{v}场"),
        (r"累计降雨量[\d.]+\s*mm", "event_total_rainfall", lambda v: f"累计降雨量{v}mm"),
        (r"最大场次降雨量为[\d.]+\s*mm", "max_event_rainfall", lambda v: f"最大场次降雨量为{v}mm"),
    ]
    for pattern, key, fmt in replacements:
        if values.get(key) is not None:
            result = re.sub(pattern, fmt(values[key]), result)
    return result


def _replace_no_rain_text(text: str) -> str:
    if "降雨" in text and ("总降雨量" in text or "降雨日" in text or "有效降雨场次" in text):
        return "监测期间未识别到有效降雨数据。"
    return text


def _set_paragraph_text(para, text: str) -> None:
    for run in para.runs:
        run.text = ""
    if para.runs:
        para.runs[0].text = text
    else:
        para.add_run(text)


def _fmt_cn_date(value: object) -> str:
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return str(value)[:10]
    return f"{dt.year}年{dt.month}月{dt.day}日"
