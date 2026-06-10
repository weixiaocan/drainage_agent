"""Canonical data context for report rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

from pipeline.core.schema import normalize_sheet_df, parse_flow_filename


@dataclass
class ReportDataContext:
    """Normalized data used by the report engine."""

    analysis: Dict[str, pd.DataFrame]
    site_info: pd.DataFrame
    point_ids: list[str]
    dry_curve_data: Dict[str, pd.DataFrame] = field(default_factory=dict)
    has_rainfall_data: bool = True
    warnings: list[str] = field(default_factory=list)

    def df(self, name: str) -> pd.DataFrame:
        return self.analysis.get(name, pd.DataFrame()).copy()


def build_report_context(
    combined_xlsx: Path,
    site_info_file: Path,
    dry_curve_data: Optional[Dict[str, pd.DataFrame]],
    has_rainfall_data: bool,
) -> ReportDataContext:
    """Load and normalize all report inputs once."""
    warnings: list[str] = []
    analysis = _load_analysis_results(combined_xlsx, warnings)
    site_info = _load_site_info(site_info_file, warnings)
    curves = _normalize_curve_keys(dry_curve_data or {})

    point_ids = _collect_point_ids(curves.keys(), analysis, site_info)
    context = ReportDataContext(
        analysis=analysis,
        site_info=site_info,
        point_ids=point_ids,
        dry_curve_data=curves,
        has_rainfall_data=has_rainfall_data,
        warnings=warnings,
    )
    return context


def clean_point_id(value: object) -> str:
    """Return the display/report point id, never the old template number."""
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "_" in text:
        return parse_flow_filename(text).point_id
    # Legacy template IDs like 1-15# and 1-2-19-1# are not canonical. They are
    # accepted only for matching old site-info rows and converted for display.
    if text.startswith("1-") and text.endswith("#"):
        body = text[2:-1]
        return f"#{body}" if body else text
    return text


def point_match_keys(point_id: object) -> set[str]:
    """Build compatible keys for matching data rows without changing display."""
    point = clean_point_id(point_id)
    bare = point.replace("#", "")
    keys = {point, bare}
    if bare:
        keys.add(f"1-{bare}#")
    return {k for k in keys if k}


def _load_analysis_results(xlsx_path: Path, warnings: list[str]) -> Dict[str, pd.DataFrame]:
    results: Dict[str, pd.DataFrame] = {}
    if not Path(xlsx_path).exists():
        warnings.append(f"综合分析结果不存在: {xlsx_path}")
        return results

    excel = pd.ExcelFile(xlsx_path)
    for sheet_name in excel.sheet_names:
        if sheet_name.startswith("特征曲线_"):
            continue
        df = excel.parse(sheet_name)
        results[sheet_name] = df
        logical_name, normalized_df = normalize_sheet_df(sheet_name, df)
        if "point_id" in normalized_df.columns:
            normalized_df["point_id"] = normalized_df["point_id"].map(clean_point_id)
        results[logical_name] = normalized_df
    return results


def _load_site_info(path: Path, warnings: list[str]) -> pd.DataFrame:
    if not Path(path).exists():
        warnings.append(f"点位信息文件不存在: {path}")
        return pd.DataFrame()
    df = pd.read_excel(path)
    rename_map = {}
    aliases = {
        "point_id": ("监测点编号", "监测点位", "安装监测点位", "安装点位", "点位编号"),
        "device_type": ("设备类型", "类型"),
        "shape": ("形状", "绑定管形状", "管形状"),
        "diameter_m": ("管径(m)", "管径", "管径（m）"),
        "well_depth_m": ("井深(m)", "井深", "井深（m）"),
        "install_time": ("设备安装时间", "安装时间"),
    }
    for canonical, candidates in aliases.items():
        col = _find_column(df, candidates)
        if col is not None:
            rename_map[col] = canonical
    normalized = df.rename(columns=rename_map).copy()
    if "point_id" in normalized.columns:
        normalized["point_id"] = normalized["point_id"].map(clean_point_id)
    return normalized


def _normalize_curve_keys(curves: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    return {clean_point_id(key): value for key, value in curves.items() if clean_point_id(key)}


def _collect_point_ids(
    curve_keys: Iterable[str],
    analysis: Dict[str, pd.DataFrame],
    site_info: pd.DataFrame,
) -> list[str]:
    point_ids: list[str] = []
    for source in [
        list(curve_keys),
        _series_values(analysis.get("data_collection"), "point_id"),
        _series_values(analysis.get("dry_risk"), "point_id"),
        _series_values(analysis.get("dry_analysis"), "point_id"),
        _series_values(site_info, "point_id"),
    ]:
        for value in source:
            point = clean_point_id(value)
            if point and point not in point_ids:
                point_ids.append(point)
        if point_ids:
            break
    return point_ids


def _series_values(df: Optional[pd.DataFrame], column: str) -> list[object]:
    if df is None or df.empty or column not in df.columns:
        return []
    return df[column].dropna().tolist()


def _find_column(df: pd.DataFrame, aliases: tuple[str, ...]) -> object | None:
    normalized = {str(c).strip(): c for c in df.columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    for alias in aliases:
        for col_text, col in normalized.items():
            if alias in col_text:
                return col
    return None

