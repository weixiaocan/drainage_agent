from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


STANDARD_FLOW_COLUMNS = [
    "timestamp",
    "device_id",
    "point_id",
    "flow_lps",
    "level_m",
    "velocity_mps",
]
STANDARD_FLOW_UNITS = {
    "flow_lps": "L/s",
    "level_m": "m",
    "velocity_mps": "m/s",
}


class StandardDataUnavailable(ValueError):
    """Raised when a batch has no valid confirmed standard-data artifact."""


class StandardDataStore:
    """Read-only analysis boundary for confirmed batch standard data."""

    def __init__(self, files_root: Path) -> None:
        self.files_root = files_root.resolve()

    def load_flow(self, project_id: str, batch_id: str) -> pd.DataFrame:
        standard_root = self._batch_root(project_id, batch_id) / "standard"
        manifest_path = standard_root / "manifest.json"
        flow_path = standard_root / "flow.csv"
        if not manifest_path.is_file() or not flow_path.is_file():
            raise StandardDataUnavailable("标准数据尚未确认生成")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StandardDataUnavailable("标准数据 manifest 无法读取") from exc
        if (
            manifest.get("contract_version") != 1
            or manifest.get("kind") != "standard_flow"
            or manifest.get("columns") != STANDARD_FLOW_COLUMNS
            or manifest.get("units") != STANDARD_FLOW_UNITS
            or manifest.get("file") != "flow.csv"
        ):
            raise StandardDataUnavailable("标准数据 manifest 不符合 v1 契约")

        frame = pd.read_csv(
            flow_path,
            dtype={"device_id": "string", "point_id": "string"},
        )
        if list(frame.columns) != STANDARD_FLOW_COLUMNS:
            raise StandardDataUnavailable("标准流量文件字段不符合 v1 契约")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        for col in ("flow_lps", "level_m", "velocity_mps"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if frame.empty:
            raise StandardDataUnavailable("标准流量文件不包含数据")
        if frame["timestamp"].isna().any():
            raise StandardDataUnavailable("标准流量文件包含无效时间")
        return frame

    def load_rainfall(self, project_id: str, batch_id: str) -> pd.DataFrame:
        path = self._batch_root(project_id, batch_id) / "standard" / "rainfall.csv"
        if not path.is_file():
            raise StandardDataUnavailable("当前分析批次缺少标准降雨数据")
        frame = pd.read_csv(path)
        if list(frame.columns) != ["timestamp", "rain_mm"]:
            raise StandardDataUnavailable("标准降雨文件字段必须为 timestamp,rain_mm")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame["rain_mm"] = pd.to_numeric(frame["rain_mm"], errors="coerce")
        if frame.empty or frame.isna().any().any():
            raise StandardDataUnavailable("标准降雨文件包含空值或无效数据")
        return frame

    def load_sites(self, project_id: str, batch_id: str) -> pd.DataFrame:
        path = self._batch_root(project_id, batch_id) / "standard" / "sites.csv"
        if not path.is_file():
            raise StandardDataUnavailable("当前分析批次缺少标准点位资料")
        frame = pd.read_csv(path, dtype={"point_id": "string"})
        legacy = ["point_id", "diameter_m", "well_depth_m", "pipe_type"]
        current = [
            "point_id",
            "device_type",
            "shape",
            "diameter_m",
            "well_depth_m",
            "install_time",
            "pipe_type",
        ]
        if list(frame.columns) not in (legacy, current):
            raise StandardDataUnavailable(
                "标准点位资料字段不符合点位信息契约"
            )
        for column in ("diameter_m", "well_depth_m"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame.empty or frame[["point_id", "diameter_m", "well_depth_m"]].isna().any().any():
            raise StandardDataUnavailable("标准点位资料包含空值或无效数据")
        return frame

    def _batch_root(self, project_id: str, batch_id: str) -> Path:
        batch_root = (
            self.files_root / project_id / "batches" / batch_id
        ).resolve()
        if not batch_root.is_relative_to(self.files_root):
            raise StandardDataUnavailable("项目或批次标识超出标准数据目录")
        return batch_root
