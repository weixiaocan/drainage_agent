"""Lightweight field schema for pipeline inputs and report outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class FlowFileInfo:
    """Parsed identifiers from a flow CSV filename."""

    device_id: str
    point_id: str


FLOW_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("数据时间", "时间", "date", "datetime", "time", "t"),
    "device_id": ("设备编号", "device_id", "device", "设备ID"),
    "flow_lps": ("流量(L/s)(均值)", "流量(L/s)", "流量", "flow_lps", "flow"),
    "level_m": ("液位(m)(均值)", "液位(m)", "液位", "level_m", "level"),
    "velocity_mps": ("流速(m/s)(均值)", "流速(m/s)", "流速", "velocity_mps", "velocity"),
}

RAINFALL_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("date", "datetime", "time", "t", "时间", "数据时间", "日期"),
    "rain_mm": ("rain", "rain_mm", "降雨量", "雨量", "降雨量(mm)", "日降雨量(mm)", "日降雨量"),
}

SHEET_ALIASES: dict[str, str] = {
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
        "record_count": ("监测数据条数",),
        "monitoring_days": ("监测天数",),
        "theoretical_count": ("理论数据条数",),
        "collection_rate": ("数据收集率(%)", "数据收集率"),
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

DISPLAY_COLUMNS: dict[str, dict[str, str]] = {
    "flow": {
        "timestamp": "数据时间",
        "device_id": "设备编号",
        "point_id": "点位编号",
        "flow_lps": "流量(L/s)",
        "level_m": "液位(m)",
        "velocity_mps": "流速(m/s)",
    },
    "rainfall": {
        "timestamp": "时间",
        "rain_mm": "降雨量(mm)",
    },
    "rainfall_events": {
        "event_id": "场次编号",
        "start_time": "开始时间",
        "end_time": "结束时间",
        "total_rain_mm": "总降雨量(mm)",
        "duration_h": "降雨历时(h)",
        "peak_intensity_mmh": "峰值雨强(mm/h)",
        "avg_intensity_mmh": "平均强度(mm/h)",
        "rain_level": "降雨等级",
    },
}


def parse_flow_filename(path: str | Path) -> FlowFileInfo:
    """Parse ``{device_id}_{point_id}.csv`` flow filenames."""
    stem = Path(path).stem
    if "_" not in stem:
        return FlowFileInfo(device_id="", point_id=stem)
    device_id, point_id = stem.split("_", 1)
    return FlowFileInfo(device_id=device_id.strip(), point_id=point_id.strip())


def canonical_sheet_name(sheet_name: str) -> str:
    """Return the logical schema name for a workbook sheet."""
    return SHEET_ALIASES.get(str(sheet_name).strip(), str(sheet_name).strip())


def find_column(df: pd.DataFrame, aliases: tuple[str, ...], required: bool = True) -> Optional[str]:
    """Find a DataFrame column by exact, case-insensitive, then keyword aliases."""
    columns = [str(c).strip() for c in df.columns]
    original_by_str = {str(c).strip(): c for c in df.columns}

    for alias in aliases:
        if alias in original_by_str:
            return original_by_str[alias]

    lowered = {str(c).strip().lower(): c for c in df.columns}
    for alias in aliases:
        match = lowered.get(alias.lower())
        if match is not None:
            return match

    for alias in aliases:
        alias_lower = alias.lower()
        for col in columns:
            if alias_lower in col.lower():
                return original_by_str[col]

    if required:
        raise ValueError(f"无法识别必需列 {aliases[0]}，可用列: {columns}")
    return None


def normalize_flow_df(df: pd.DataFrame, path: str | Path | None = None) -> pd.DataFrame:
    """Normalize a raw flow CSV DataFrame to canonical internal columns."""
    result = pd.DataFrame(index=df.index)

    for field in ("timestamp", "flow_lps", "level_m"):
        col = find_column(df, FLOW_COLUMN_ALIASES[field], required=True)
        result[field] = df[col]

    velocity_col = find_column(df, FLOW_COLUMN_ALIASES["velocity_mps"], required=False)
    if velocity_col is not None:
        result["velocity_mps"] = df[velocity_col]

    device_col = find_column(df, FLOW_COLUMN_ALIASES["device_id"], required=False)
    if device_col is not None:
        result["device_id"] = df[device_col].astype(str).str.strip()

    if path is not None:
        info = parse_flow_filename(path)
        result["point_id"] = info.point_id
        result["device_id"] = info.device_id

    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce")
    result = result.dropna(subset=["timestamp"]).copy()

    for field in ("flow_lps", "level_m", "velocity_mps"):
        if field in result.columns:
            result[field] = pd.to_numeric(result[field], errors="coerce").fillna(0.0)

    ordered = ["timestamp", "device_id", "point_id", "flow_lps", "level_m", "velocity_mps"]
    return result[[c for c in ordered if c in result.columns]]


def flow_to_legacy_df(df: pd.DataFrame) -> pd.DataFrame:
    """Convert canonical flow columns to the legacy names used by current algorithms."""
    legacy = df.rename(
        columns={
            "timestamp": "数据时间",
            "flow_lps": "f",
            "level_m": "l",
            "velocity_mps": "velo",
        }
    )
    return legacy


def normalize_rainfall_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize raw rainfall data to ``timestamp`` and ``rain_mm``."""
    time_col = find_column(df, RAINFALL_COLUMN_ALIASES["timestamp"], required=True)
    rain_col = find_column(df, RAINFALL_COLUMN_ALIASES["rain_mm"], required=True)

    result = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(df[time_col], errors="coerce"),
            "rain_mm": pd.to_numeric(df[rain_col], errors="coerce").fillna(0.0),
        }
    )
    return result.dropna(subset=["timestamp"]).copy()


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


def to_display_columns(df: pd.DataFrame, table_type: str) -> pd.DataFrame:
    """Convert canonical columns to user-facing Chinese display columns."""
    mapping = DISPLAY_COLUMNS.get(table_type, {})
    return df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})
