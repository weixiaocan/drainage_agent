"""Explicit table specifications for Word report rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional

import pandas as pd
from docx.table import Table

from .data_context import ReportDataContext, clean_point_id, point_match_keys
from .style_writer import adjust_table_rows_preserve_style, set_cell_text

Formatter = Callable[[object], str]


@dataclass(frozen=True)
class ReportColumn:
    field: str
    formatter: Formatter = str
    required: bool = True


@dataclass(frozen=True)
class ReportTableSpec:
    role: str
    source: str
    columns: tuple[ReportColumn, ...]
    header_rows: int = 1
    template_row_idx: int = 1
    required: bool = True


def render_report_table(
    table: Table,
    spec: ReportTableSpec,
    context: ReportDataContext,
) -> list[str]:
    """Render a report table by explicit source-field mapping."""
    warnings: list[str] = []
    df = _source_df(spec, context)
    df = _prepare_df_for_spec(df, spec, context)

    missing = [col.field for col in spec.columns if col.required and col.field not in df.columns]
    if missing:
        message = f"{spec.role} 缺少必需字段: {', '.join(missing)}"
        if spec.required:
            raise ValueError(message)
        warnings.append(message)

    adjust_table_rows_preserve_style(table, len(df), template_row_idx=spec.template_row_idx)

    for row_offset, (_, row) in enumerate(df.iterrows()):
        word_row = table.rows[spec.header_rows + row_offset]
        for col_idx, col in enumerate(spec.columns):
            if col_idx >= len(word_row.cells):
                break
            value = row.get(col.field, "")
            set_cell_text(word_row.cells[col_idx], col.formatter(value))

    return warnings


def _source_df(spec: ReportTableSpec, context: ReportDataContext) -> pd.DataFrame:
    if spec.source == "site_info":
        return context.site_info.copy()
    return context.df(spec.source)


def _prepare_df_for_spec(
    df: pd.DataFrame,
    spec: ReportTableSpec,
    context: ReportDataContext,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[c.field for c in spec.columns])

    prepared = df.copy()
    if "point_id" in prepared.columns:
        prepared["point_id"] = prepared["point_id"].map(clean_point_id)

    if spec.role in {"site_info", "collection_rate"} and context.point_ids:
        prepared = _align_to_point_order(prepared, context.point_ids)

    if spec.role == "rainfall_daily":
        prepared["daily_rain_mm"] = pd.to_numeric(prepared.get("daily_rain_mm"), errors="coerce").fillna(0)
        prepared = prepared[prepared["daily_rain_mm"] > 0].copy()
        if "date" in prepared.columns:
            prepared = prepared.sort_values("date")

    if spec.role == "rainfall_events":
        if "event_id" not in prepared.columns:
            prepared["event_id"] = range(1, len(prepared) + 1)

    if spec.role == "dry_risk":
        if "index" not in prepared.columns:
            prepared.insert(0, "index", range(1, len(prepared) + 1))

    return prepared.reset_index(drop=True)


def _align_to_point_order(df: pd.DataFrame, point_ids: list[str]) -> pd.DataFrame:
    rows = []
    if "point_id" not in df.columns:
        return pd.DataFrame({"point_id": point_ids})
    keyed = {}
    for _, row in df.iterrows():
        for key in point_match_keys(row.get("point_id")):
            keyed.setdefault(key, row)
    for point in point_ids:
        matched = None
        for key in point_match_keys(point):
            if key in keyed:
                matched = keyed[key]
                break
        if matched is None:
            rows.append({"point_id": point})
        else:
            row_dict = matched.to_dict()
            row_dict["point_id"] = point
            rows.append(row_dict)
    return pd.DataFrame(rows)


def _fmt_number(value: object) -> str:
    if _is_blank(value):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text


def _fmt_int(value: object) -> str:
    if _is_blank(value):
        return ""
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return str(value)


def _fmt_percent(value: object) -> str:
    if _is_blank(value):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number <= 1:
        number *= 100
    return f"{number:.1f}%"


def _fmt_date(value: object) -> str:
    dt = _to_datetime(value)
    if dt is None:
        return "" if _is_blank(value) else str(value)[:10]
    return dt.strftime("%Y-%m-%d")


def _fmt_datetime(value: object) -> str:
    dt = _to_datetime(value)
    if dt is None:
        return "" if _is_blank(value) else str(value)[:16]
    return dt.strftime("%Y-%m-%d %H:%M")


def _to_datetime(value: object) -> Optional[datetime]:
    if _is_blank(value):
        return None
    if isinstance(value, datetime):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


TABLE_SPECS: Dict[str, ReportTableSpec] = {
    "site_info": ReportTableSpec(
        role="site_info",
        source="site_info",
        columns=(
            ReportColumn("point_id", clean_point_id),
            ReportColumn("device_type"),
            ReportColumn("shape"),
            ReportColumn("diameter_m", _fmt_number),
            ReportColumn("well_depth_m", _fmt_number),
            ReportColumn("install_time", _fmt_date),
        ),
    ),
    "collection_rate": ReportTableSpec(
        role="collection_rate",
        source="data_collection",
        columns=(
            ReportColumn("point_id", clean_point_id),
            ReportColumn("record_count", _fmt_int),
            ReportColumn("monitoring_days", _fmt_int),
            ReportColumn("theoretical_count", _fmt_int),
            ReportColumn("collection_rate", _fmt_percent),
        ),
    ),
    "rainfall_daily": ReportTableSpec(
        role="rainfall_daily",
        source="rainfall_daily",
        columns=(
            ReportColumn("date", _fmt_date),
            ReportColumn("daily_rain_mm", _fmt_number),
        ),
    ),
    "rainfall_events": ReportTableSpec(
        role="rainfall_events",
        source="rainfall_events",
        columns=(
            ReportColumn("event_id", _fmt_int),
            ReportColumn("start_time", _fmt_datetime),
            ReportColumn("end_time", _fmt_datetime),
            ReportColumn("total_rain_mm", _fmt_number),
            ReportColumn("duration_h", _fmt_number),
            ReportColumn("avg_intensity_mmh", _fmt_number),
            ReportColumn("rain_level"),
        ),
    ),
    "dry_risk": ReportTableSpec(
        role="dry_risk",
        source="dry_risk",
        header_rows=2,
        template_row_idx=2,
        columns=(
            ReportColumn("index", _fmt_int),
            ReportColumn("point_id", clean_point_id),
            ReportColumn("diameter_m", _fmt_number),
            ReportColumn("well_depth_m", _fmt_number),
            ReportColumn("dry_velocity_mps", _fmt_number),
            ReportColumn("max_level_m", _fmt_number),
            ReportColumn("max_fullness", _fmt_number),
            ReportColumn("overflow_value", _fmt_number),
            ReportColumn("silting_risk"),
            ReportColumn("running_risk"),
            ReportColumn("overflow_risk"),
        ),
    ),
    "rainy_overflow_risk": ReportTableSpec(
        role="rainy_overflow_risk",
        source="rainy_overflow_risk",
        columns=(
            ReportColumn("point_id", clean_point_id),
            ReportColumn("max_level_m", _fmt_number),
            ReportColumn("well_depth_m", _fmt_number),
            ReportColumn("overflow_value", _fmt_number),
            ReportColumn("overflow_risk"),
        ),
    ),
}
