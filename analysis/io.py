from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pandas as pd

from .filtering import CleanReport, filter_flow
from .schema import normalize_flow_df, normalize_rain_df


LAST_CLEAN_REPORT = CleanReport()


def project_root() -> Path:
    env_root = os.getenv("DRAINAGE_AGENT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "data").exists() and (candidate / "agent").exists():
            return candidate
    return current


def _read_csv(path: Path) -> pd.DataFrame:
    last_exc: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    return pd.read_csv(path)


def _normalize_points(points: Iterable[str] | str | None) -> set[str] | None:
    if points is None or points == "全部":
        return None
    if isinstance(points, str):
        return {points}
    return {str(p) for p in points}


def _apply_time_range(df: pd.DataFrame, time_range: tuple[str, str] | list[str] | None) -> pd.DataFrame:
    if not time_range:
        return df
    start, end = time_range
    result = df
    if start:
        result = result[result["timestamp"] >= pd.to_datetime(start)]
    if end:
        result = result[result["timestamp"] <= pd.to_datetime(end)]
    return result.copy()


def load_rain(time_range: tuple[str, str] | list[str] | None = None, root: Path | None = None) -> pd.DataFrame:
    base = root or project_root()
    path = base / "data" / "降雨数据.csv"
    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "rain_mm"])
    rain = normalize_rain_df(_read_csv(path))
    return _apply_time_range(rain, time_range)


def load_flow(
    points: Iterable[str] | str | None = None,
    time_range: tuple[str, str] | list[str] | None = None,
    clean: bool = True,
    dry_only: bool = False,
    root: Path | None = None,
) -> pd.DataFrame:
    global LAST_CLEAN_REPORT
    base = root or project_root()
    flow_dir = base / "data" / "flow"
    selected_points = _normalize_points(points)
    frames: list[pd.DataFrame] = []
    for csv_path in sorted(flow_dir.glob("*.csv")):
        normalized = normalize_flow_df(_read_csv(csv_path), csv_path)
        if selected_points is not None and str(normalized["point_id"].iloc[0]) not in selected_points:
            continue
        frames.append(normalized)
    if not frames:
        LAST_CLEAN_REPORT = CleanReport()
        return pd.DataFrame(columns=["timestamp", "device_id", "point_id", "flow_lps", "level_m", "velocity_mps"])
    flow = pd.concat(frames, ignore_index=True)
    flow = _apply_time_range(flow, time_range)
    rain = load_rain(time_range=time_range, root=base)
    filtered, report = filter_flow(flow, rain, clean=clean, dry_only=dry_only)
    LAST_CLEAN_REPORT = report
    return filtered.reset_index(drop=True)


def load_sites(root: Path | None = None) -> pd.DataFrame:
    base = root or project_root()
    path = base / "data" / "点位信息.xlsx"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_excel(path)
    except Exception:
        return pd.DataFrame()


def last_clean_report() -> CleanReport:
    return LAST_CLEAN_REPORT

