from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class FlowFileInfo:
    device_id: str
    point_id: str


FLOW_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("数据时间", "时间", "date", "datetime", "time", "timestamp", "t"),
    "device_id": ("设备编号", "设备ID", "device_id", "device"),
    "flow_lps": ("流量(L/s)(均值)", "流量(L/s)", "流量", "flow_lps", "flow", "f"),
    "level_m": ("液位(m)(均值)", "液位(m)", "液位", "level_m", "level", "l"),
    "velocity_mps": ("流速(m/s)(均值)", "流速(m/s)", "流速", "velocity_mps", "velocity", "velo"),
}

RAINFALL_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("date", "datetime", "time", "timestamp", "t", "时间", "数据时间", "日期"),
    "rain_mm": ("rain", "rain_mm", "降雨量", "雨量", "降雨量(mm)", "日降雨量(mm)", "日降雨量"),
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
    "rainfall_daily": {
        "date": "日期",
        "rain_mm": "日降雨量(mm)",
        "is_rainy": "是否雨天",
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
    stem = Path(path).stem
    if "_" not in stem:
        return FlowFileInfo(device_id="", point_id=stem.strip())
    device_id, point_id = stem.split("_", 1)
    return FlowFileInfo(device_id=device_id.strip(), point_id=point_id.strip())


def find_column(df: pd.DataFrame, aliases: tuple[str, ...], required: bool = True) -> Optional[object]:
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
    result = pd.DataFrame(index=df.index)
    for field in ("timestamp", "flow_lps", "level_m"):
        col = find_column(df, FLOW_COLUMN_ALIASES[field], required=True)
        result[field] = df[col]
    velocity_col = find_column(df, FLOW_COLUMN_ALIASES["velocity_mps"], required=False)
    result["velocity_mps"] = df[velocity_col] if velocity_col is not None else 0.0
    device_col = find_column(df, FLOW_COLUMN_ALIASES["device_id"], required=False)
    result["device_id"] = df[device_col].astype(str).str.strip() if device_col is not None else ""
    if path is not None:
        info = parse_flow_filename(path)
        result["point_id"] = info.point_id
        result["device_id"] = info.device_id or result["device_id"]
    elif "point_id" not in result:
        result["point_id"] = ""
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce")
    result = result.dropna(subset=["timestamp"]).copy()
    for field in ("flow_lps", "level_m", "velocity_mps"):
        result[field] = pd.to_numeric(result[field], errors="coerce")
    result = result.sort_values(["point_id", "timestamp"]).reset_index(drop=True)
    return result[["timestamp", "device_id", "point_id", "flow_lps", "level_m", "velocity_mps"]]


def normalize_rain_df(df: pd.DataFrame) -> pd.DataFrame:
    time_col = find_column(df, RAINFALL_COLUMN_ALIASES["timestamp"], required=True)
    rain_col = find_column(df, RAINFALL_COLUMN_ALIASES["rain_mm"], required=True)
    result = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(df[time_col], errors="coerce"),
            "rain_mm": pd.to_numeric(df[rain_col], errors="coerce").fillna(0.0),
        }
    )
    return result.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def to_display_columns(df: pd.DataFrame, table_type: str) -> pd.DataFrame:
    mapping = DISPLAY_COLUMNS.get(table_type, {})
    return df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})

