from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from analysis.io import StandardDataStore
from analysis.io.standard import STANDARD_FLOW_COLUMNS, STANDARD_FLOW_UNITS


BatchRecord = dict[str, Any]
BatchRecordReader = Callable[[Path], list[BatchRecord]]


class UnresolvedMergeConflicts(Exception):
    def __init__(
        self,
        summary: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> None:
        super().__init__("来源批次存在点位和时间冲突")
        self.summary = summary
        self.items = items


class InvalidConflictResolution(ValueError):
    pass


def standard_batch_record_reader(batch_workspace: Path) -> list[BatchRecord]:
    project_id = batch_workspace.parent.parent.name
    batch_id = batch_workspace.name
    frame = StandardDataStore(batch_workspace.parents[2]).load_flow(
        project_id,
        batch_id,
    )
    records: list[BatchRecord] = []
    for row in frame.to_dict(orient="records"):
        records.append(
            {
                "point_id": str(row["point_id"]),
                "timestamp": row["timestamp"].isoformat(),
                "values": {
                    field: row[field]
                    for field in STANDARD_FLOW_COLUMNS
                    if field not in {"timestamp", "point_id"}
                },
            }
        )
    return records


def write_derived_standard_flow(
    batch_workspace: Path,
    records: list[BatchRecord],
    source_batch_ids: list[str],
    conflict_resolutions: list[dict[str, str]],
) -> None:
    rows = []
    for record in records:
        values = record["values"]
        rows.append(
            {
                "timestamp": record["timestamp"],
                "device_id": values.get("device_id"),
                "point_id": record["point_id"],
                "flow_lps": values.get("flow_lps"),
                "level_m": values.get("level_m"),
                "velocity_mps": values.get("velocity_mps"),
            }
        )
    standard_root = batch_workspace / "standard"
    flow_path = standard_root / "flow.csv"
    pd.DataFrame(rows, columns=STANDARD_FLOW_COLUMNS).to_csv(
        flow_path,
        index=False,
        encoding="utf-8",
        date_format="%Y-%m-%dT%H:%M:%S",
    )
    manifest = {
        "contract_version": 1,
        "kind": "standard_flow",
        "columns": STANDARD_FLOW_COLUMNS,
        "units": STANDARD_FLOW_UNITS,
        "source_batch_ids": source_batch_ids,
        "conflict_resolutions": conflict_resolutions,
        "file": "flow.csv",
    }
    (standard_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_batch_records(
    sources: list[tuple[str, Path]],
    reader: BatchRecordReader,
    resolutions: list[dict[str, str]],
) -> list[BatchRecord]:
    records: list[BatchRecord] = []
    for source_batch_id, source_workspace in sources:
        records.extend(
            {**record, "source_batch_id": source_batch_id}
            for record in reader(source_workspace)
        )

    records_by_identity: dict[tuple[str, str], list[BatchRecord]] = {}
    for record in records:
        identity = (str(record["point_id"]), str(record["timestamp"]))
        records_by_identity.setdefault(identity, []).append(record)
    conflicts = {
        identity: candidates
        for identity, candidates in records_by_identity.items()
        if len({candidate["source_batch_id"] for candidate in candidates}) > 1
    }
    if not conflicts:
        return records

    resolution_by_identity = {
        (resolution["point_id"], resolution["timestamp"]): resolution[
            "source_batch_id"
        ]
        for resolution in resolutions
    }
    if (
        len(resolution_by_identity) != len(resolutions)
        or set(resolution_by_identity) != set(conflicts)
    ):
        raise UnresolvedMergeConflicts(
            _conflict_summary(conflicts),
            _conflict_items(conflicts),
        )
    for identity, source_batch_id in resolution_by_identity.items():
        candidate_sources = {
            candidate["source_batch_id"] for candidate in conflicts[identity]
        }
        if source_batch_id not in candidate_sources:
            raise InvalidConflictResolution(
                "冲突来源选择不是该点位和时间的候选来源"
            )
    return [
        record
        for record in records
        if (str(record["point_id"]), str(record["timestamp"])) not in conflicts
        or record["source_batch_id"]
        == resolution_by_identity[
            (str(record["point_id"]), str(record["timestamp"]))
        ]
    ]


def _conflict_summary(
    conflicts: dict[tuple[str, str], list[BatchRecord]],
) -> dict[str, Any]:
    duplicate_count = sum(
        1
        for candidates in conflicts.values()
        if all(
            candidate.get("values") == candidates[0].get("values")
            for candidate in candidates[1:]
        )
    )
    point_ids = sorted({identity[0] for identity in conflicts})
    timestamps = sorted(identity[1] for identity in conflicts)
    return {
        "count": len(conflicts),
        "duplicate_count": duplicate_count,
        "value_conflict_count": len(conflicts) - duplicate_count,
        "point_ids": point_ids,
        "time_start": timestamps[0],
        "time_end": timestamps[-1],
    }


def _conflict_items(
    conflicts: dict[tuple[str, str], list[BatchRecord]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for identity, candidates in conflicts.items():
        duplicate = all(
            candidate.get("values") == candidates[0].get("values")
            for candidate in candidates[1:]
        )
        items.append(
            {
                "point_id": identity[0],
                "timestamp": identity[1],
                "kind": "duplicate" if duplicate else "value_conflict",
                "source_batch_ids": list(
                    dict.fromkeys(
                        candidate["source_batch_id"] for candidate in candidates
                    )
                ),
            }
        )
    return items
