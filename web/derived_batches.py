from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


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


def unconfigured_batch_record_reader(_batch_workspace: Path) -> list[BatchRecord]:
    raise RuntimeError("标准数据读取适配器尚未配置")


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
