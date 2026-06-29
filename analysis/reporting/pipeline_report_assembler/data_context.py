"""Canonical data context for report rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

from analysis.schema import parse_flow_filename

from .local_schema import normalize_sheet_df


@dataclass
class ReportDataContext:
    """Normalized data used by the report engine."""

    analysis: Dict[str, pd.DataFrame]
    site_info: pd.DataFrame
    point_ids: list[str]
    dry_curve_data: Dict[str, pd.DataFrame] = field(default_factory=dict)
    has_rainfall_data: bool = True
    rainfall_chart_paths: Dict[str, str] = field(default_factory=dict)
    pattern_chart_paths: Dict[str, list[str]] = field(default_factory=dict)
    artifact_scope: str = "全网_全时段"
    warnings: list[str] = field(default_factory=list)

    def df(self, name: str) -> pd.DataFrame:
        return self.analysis.get(name, pd.DataFrame()).copy()


def build_report_context(
    analysis_results: Dict[str, pd.DataFrame],
    site_info_file: Path,
    dry_curve_data: Optional[Dict[str, pd.DataFrame]],
    has_rainfall_data: bool,
    point_ids: Optional[list[str]] = None,
    rainfall_chart_paths: Optional[Dict[str, str]] = None,
    pattern_chart_paths: Optional[Dict[str, list[str]]] = None,
    artifact_scope: str = "全网_全时段",
) -> ReportDataContext:
    """Normalize in-memory analysis results for report rendering."""
    warnings: list[str] = []
    analysis = _normalize_analysis_results(analysis_results)
    site_info = _load_site_info(site_info_file, warnings)
    curves = _normalize_curve_keys(dry_curve_data or {})

    resolved_points = [clean_point_id(point) for point in point_ids or [] if clean_point_id(point)]
    if not resolved_points:
        resolved_points = _collect_point_ids(curves.keys(), analysis, site_info)
    if resolved_points:
        site_info = _filter_points(site_info, resolved_points)
        analysis = {name: _filter_points(frame, resolved_points) for name, frame in analysis.items()}
    context = ReportDataContext(
        analysis=analysis,
        site_info=site_info,
        point_ids=resolved_points,
        dry_curve_data=curves,
        has_rainfall_data=has_rainfall_data,
        rainfall_chart_paths=dict(rainfall_chart_paths or {}),
        pattern_chart_paths={key: list(value) for key, value in (pattern_chart_paths or {}).items()},
        artifact_scope=artifact_scope,
        warnings=warnings,
    )
    return context


def _normalize_analysis_results(analysis_results: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    results: Dict[str, pd.DataFrame] = {}
    for name, frame in analysis_results.items():
        if not isinstance(frame, pd.DataFrame):
            continue
        logical_name, normalized = normalize_sheet_df(name, frame)
        if "point_id" in normalized.columns:
            normalized["point_id"] = normalized["point_id"].map(clean_point_id)
        results[logical_name] = normalized
    return results


def _filter_points(df: pd.DataFrame, point_ids: list[str]) -> pd.DataFrame:
    if df.empty or "point_id" not in df.columns:
        return df.copy()
    wanted = {clean_point_id(point) for point in point_ids}
    return df[df["point_id"].map(clean_point_id).isin(wanted)].reset_index(drop=True)


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


def _load_site_info(path: Path, warnings: list[str]) -> pd.DataFrame:
    if not Path(path).is_file():
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
