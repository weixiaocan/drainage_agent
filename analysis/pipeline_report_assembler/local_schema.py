"""Report-workbook adapters built on the project's canonical field schema."""

from __future__ import annotations

import pandas as pd

from analysis.schema import find_column

SHEET_ALIASES: dict[str, str] = {
    "数据体检": "data_collection",
    "数据收集率统计": "data_collection",
    "降雨概况": "rainfall_daily",
    "日降雨量统计": "rainfall_daily",
    "降雨场次分析": "rainfall_events",
    "场次降雨统计": "rainfall_events",
    "旱天分析": "dry_analysis",
    "雨天事件统计": "rainy_event_stats",
    "排污规律分析": "pattern_analysis",
    "RDII总量统计": "rdii_total",
    "旱天风险": "dry_risk",
    "雨天溢流风险": "rainy_overflow_risk",
}

SHEET_COLUMN_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "data_collection": {
        "point_id": ("点位编号", "监测点编号", "监测点位", "安装点位"),
        "record_count": ("监测数据条数", "记录数"),
        "monitoring_days": ("监测天数",),
        "theoretical_count": ("理论数据条数",),
        "collection_rate": ("数据收集率(%)", "数据收集率", "收集率"),
    },
    "rainfall_daily": {
        "date": ("日期", "date"),
        "daily_rain_mm": ("日降雨量(mm)", "日降雨量", "rain", "rain_mm"),
    },
    "rainfall_events": {
        "event_id": ("场次编号", "降雨场次编号"),
        "start_time": ("开始时间",),
        "end_time": ("结束时间",),
        "total_rain_mm": ("总降雨量(mm)", "总降雨量"),
        "duration_h": ("降雨历时(h)", "降雨历时"),
        "peak_intensity_mmh": ("峰值雨强(mm/h)", "峰值雨强"),
        "avg_intensity_mmh": ("平均强度(mm/h)", "平均强度"),
        "rain_level": ("降雨等级",),
    },
    "dry_analysis": {
        "point_id": ("点位编号",),
        "daily_flow_m3d": ("日均流量(m³/d)", "日均流量(m3/d)"),
        "max_flow_lps": ("日最大流量(L/s)",),
        "min_flow_lps": ("日最小流量(L/s)",),
        "max_level_m": ("最大液位(m)",),
        "max_fullness": ("最大充满度",),
        "overflow_value": ("外溢风险", "溢流风险值"),
        "avg_velocity_mps": ("平均流速(m/s)", "旱天流速(m/s)"),
        "avg_level_m": ("平均液位(m)",),
    },
    "dry_risk": {
        "index": ("序号",),
        "point_id": ("点位编号",),
        "diameter_m": ("管径(m)",),
        "well_depth_m": ("井深(m)",),
        "daily_flow_m3d": ("日均流量(m³/d)", "日均流量(m3/d)"),
        "dry_velocity_mps": ("旱天流速(m/s)", "平均流速(m/s)"),
        "max_level_m": ("最大液位(m)",),
        "max_fullness": ("最大充满度",),
        "overflow_value": ("溢流风险值", "外溢风险"),
        "silting_risk": ("淤积风险",),
        "running_risk": ("运行风险",),
        "overflow_risk": ("溢流风险",),
    },
    "rainy_overflow_risk": {
        "event_id": ("降雨场次编号", "场次编号"),
        "rain_level": ("降雨等级",),
        "point_id": ("点位编号",),
        "max_level_m": ("最大液位(m)",),
        "well_depth_m": ("井深(m)",),
        "overflow_value": ("溢流风险值",),
        "overflow_risk": ("溢流风险",),
    },
    "pattern_analysis": {
        "point_id": ("点位编号",),
        "category": ("分类",),
        "category_name": ("分类名称",),
        "kz": ("Kz值", "Kz"),
        "peak_valley_ratio": ("峰谷比",),
        "peak_count": ("峰数量",),
        "peak_periods": ("波峰时段",),
        "valley_periods": ("波谷时段",),
        "diagnosis_reason": ("诊断理由",),
        "description": ("排污规律描述",),
    },
}

def canonical_sheet_name(sheet_name: str) -> str:
    """Return the logical schema name for a workbook sheet."""
    return SHEET_ALIASES.get(str(sheet_name).strip(), str(sheet_name).strip())


def normalize_sheet_df(sheet_name: str, df: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    """Normalize workbook sheet name and known columns to canonical names."""
    logical_name = canonical_sheet_name(sheet_name)
    aliases = SHEET_COLUMN_ALIASES.get(logical_name)
    if not aliases:
        return logical_name, df.copy()

    rename_map: dict[object, str] = {}
    for canonical, candidates in aliases.items():
        col = find_column(df, candidates, required=False)
        if col is not None:
            rename_map[col] = canonical

    normalized = df.rename(columns=rename_map).copy()
    return logical_name, normalized
