from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RiskConfig:
    running_risk_low: float = 0.75
    running_risk_medium: float = 1.0
    running_risk_high: float = 2.0
    overflow_risk_low: float = 0.7
    overflow_risk_medium: float = 0.9
    silting_risk_medium: float = 0.3
    combined_min_velocity: float = 0.75
    separate_min_velocity: float = 0.6
    rain_effect_delay_hours: float = 12.0


def _running_risk(max_fullness: float, cfg: RiskConfig) -> str:
    if max_fullness < cfg.running_risk_low:
        return "运行良好"
    if max_fullness < cfg.running_risk_medium:
        return "低风险"
    if max_fullness <= cfg.running_risk_high:
        return "中风险"
    return "高风险"


def _overflow_risk(overflow_value: float, cfg: RiskConfig) -> str:
    if overflow_value < cfg.overflow_risk_low:
        return "低溢流风险"
    if overflow_value < cfg.overflow_risk_medium:
        return "中溢流风险"
    if overflow_value <= 1.0:
        return "高溢流风险"
    return "已发生溢流"


def _silting_risk(avg_velocity: float, pipe_type: str, cfg: RiskConfig) -> str:
    min_speed = cfg.combined_min_velocity if "合流" in str(pipe_type or "") else cfg.separate_min_velocity
    if avg_velocity > min_speed:
        return "低淤积风险"
    if avg_velocity > cfg.silting_risk_medium:
        return "中淤积风险"
    return "高淤积风险"


def _find_col(df: pd.DataFrame, keywords: tuple[str, ...]) -> object | None:
    for col in df.columns:
        text = str(col).strip().lower()
        if any(keyword.lower() in text for keyword in keywords):
            return col
    return None


def _site_info(sites: pd.DataFrame | None) -> dict[str, dict[str, object]]:
    if sites is None or sites.empty:
        return {}
    point_col = _find_col(sites, ("点位", "监测点", "名称", "point"))
    diameter_col = _find_col(sites, ("管径", "diameter"))
    depth_col = _find_col(sites, ("井深", "深度", "depth"))
    pipe_type_col = _find_col(sites, ("管道类型", "管类型", "管网类型", "pipe"))
    if point_col is None:
        return {}

    result: dict[str, dict[str, object]] = {}
    for _, row in sites.iterrows():
        point_id = str(row[point_col]).strip() if pd.notna(row[point_col]) else ""
        if not point_id:
            continue
        diameter = pd.to_numeric(row[diameter_col], errors="coerce") if diameter_col is not None else 0.0
        depth = pd.to_numeric(row[depth_col], errors="coerce") if depth_col is not None else 0.0
        pipe_type = str(row[pipe_type_col]).strip() if pipe_type_col is not None and pd.notna(row[pipe_type_col]) else ""
        result[point_id] = {
            "diameter": float(diameter) if pd.notna(diameter) else 0.0,
            "depth": float(depth) if pd.notna(depth) else 0.0,
            "pipe_type": pipe_type,
        }
    return result


def _match_site(point_id: str, sites: dict[str, dict[str, object]]) -> dict[str, object]:
    if point_id in sites:
        return sites[point_id]
    if "_" in point_id:
        tail = point_id.split("_", 1)[1]
        if tail in sites:
            return sites[tail]
    return {"diameter": 0.0, "depth": 0.0, "pipe_type": ""}


def _dry_risk(dry_stats: pd.DataFrame, sites: pd.DataFrame | None, cfg: RiskConfig) -> pd.DataFrame:
    site_info = _site_info(sites)
    rows: list[dict[str, object]] = []
    for idx, (_, row) in enumerate(dry_stats.iterrows(), start=1):
        point_id = str(row.get("point_id", "")).strip()
        if not point_id:
            continue
        info = _match_site(point_id, site_info)
        diameter = float(info.get("diameter") or 0.0)
        depth = float(info.get("depth") or 0.0)
        pipe_type = str(info.get("pipe_type") or "")
        max_level = float(pd.to_numeric(row.get("max_level_m", 0.0), errors="coerce") or 0.0)
        avg_velocity = float(pd.to_numeric(row.get("avg_velocity_mps", 0.0), errors="coerce") or 0.0)
        daily_flow = float(pd.to_numeric(row.get("daily_flow_m3d", 0.0), errors="coerce") or 0.0)
        max_fullness = max_level / diameter if diameter > 0 else 0.0
        overflow_value = max_level / depth if depth > 0 else 0.0
        rows.append(
            {
                "serial_no": idx,
                "point_id": point_id,
                "diameter_m": round(diameter, 3),
                "well_depth_m": round(depth, 2),
                "daily_flow_m3d": round(daily_flow, 2),
                "dry_velocity_mps": round(avg_velocity, 4),
                "max_level_m": round(max_level, 3),
                "max_fullness": round(max_fullness, 2),
                "overflow_value": round(overflow_value, 2),
                "silting_risk": _silting_risk(avg_velocity, pipe_type, cfg),
                "running_risk": _running_risk(max_fullness, cfg),
                "overflow_risk": _overflow_risk(overflow_value, cfg),
            }
        )
    return pd.DataFrame(rows)


def _rainy_risk(
    flow: pd.DataFrame | None,
    events: pd.DataFrame | None,
    sites: pd.DataFrame | None,
    event_ids: list[int] | None,
    cfg: RiskConfig,
) -> pd.DataFrame:
    if flow is None or events is None or flow.empty or events.empty:
        return pd.DataFrame()
    site_info = _site_info(sites)
    selected = set(event_ids or [])
    rows: list[dict[str, object]] = []
    for _, event in events.iterrows():
        event_id = int(event.get("event_id", 0))
        if selected and event_id not in selected:
            continue
        start = pd.to_datetime(event.get("start_time"), errors="coerce")
        end = pd.to_datetime(event.get("end_time"), errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        end = end + pd.Timedelta(hours=cfg.rain_effect_delay_hours)
        rain_level = str(event.get("rain_level", ""))
        event_flow = flow[(flow["timestamp"] >= start) & (flow["timestamp"] <= end)]
        if event_flow.empty:
            continue
        for point_id, point_df in event_flow.groupby("point_id", sort=True):
            info = _match_site(str(point_id), site_info)
            depth = float(info.get("depth") or 0.0)
            max_level = float(point_df["level_m"].max())
            overflow_value = max_level / depth if depth > 0 else 0.0
            rows.append(
                {
                    "event_id": event_id,
                    "rain_level": rain_level,
                    "point_id": point_id,
                    "max_level_m": round(max_level, 3),
                    "well_depth_m": round(depth, 2),
                    "overflow_value": round(overflow_value, 3),
                    "overflow_risk": _overflow_risk(overflow_value, cfg),
                }
            )
    return pd.DataFrame(rows)


def assess_risk(
    dry_stats: pd.DataFrame,
    event_response: pd.DataFrame | None = None,
    scope: str = "all",
    sites: pd.DataFrame | None = None,
    flow: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
    event_ids: list[int] | None = None,
    config: RiskConfig | None = None,
) -> dict[str, pd.DataFrame]:
    cfg = config or RiskConfig()
    dry = pd.DataFrame()
    rainy = pd.DataFrame()
    if scope in {"all", "dry"} and not dry_stats.empty:
        dry = _dry_risk(dry_stats, sites, cfg)
    if scope in {"all", "rainy"}:
        rainy = _rainy_risk(flow, events, sites, event_ids, cfg)
    return {"dry_risk": dry, "rainy_risk": rainy}
