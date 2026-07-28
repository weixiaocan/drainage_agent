from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import tracemalloc
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from analysis.runs import AnalysisRequest, AnalysisRunner
from scripts.generate_synthetic_capacity_data import generate
from web.projects import ProjectRepository
from web.standard_data import BatchDataImporter


def measured(action):
    tracemalloc.start()
    started = time.perf_counter()
    result = action()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {
        "seconds": round(elapsed, 3),
        "python_peak_mib": round(peak / 1024 / 1024, 1),
    }


def benchmark(root: Path, *, points: int, days: int) -> dict[str, object]:
    database = root / "var" / "drainage.sqlite3"
    files = root / "var" / "projects"
    source = root / "synthetic-capacity.csv"
    rows, generation = measured(
        lambda: generate(source, points=points, days=days)
    )
    projects = ProjectRepository(database, files)
    project = projects.create("Ticket 13 合成容量基准")
    batch = projects.create_batch(project.id, "容量基准批次")
    importer = BatchDataImporter(database, files)
    content = source.read_bytes()
    inspection, inspect = measured(
        lambda: importer.inspect_upload(
            project.id, batch.id, source.name, content
        )
    )
    _, confirm = measured(
        lambda: importer.confirm_mapping(
            project.id,
            batch.id,
            inspection.id,
            {
                "timestamp": "timestamp",
                "device_id": "device_id",
                "point_id": "point_id",
                "flow_lps": "flow_lps",
                "level_m": "level_m",
                "velocity_mps": "velocity_mps",
            },
            {
                "flow_lps": "L/s",
                "level_m": "m",
                "velocity_mps": "m/s",
            },
        )
    )
    runner = AnalysisRunner(database, files)
    result, data_quality = measured(
        lambda: runner.run(
            AnalysisRequest(
                project.id,
                batch.id,
                "data_quality",
                force_rerun=True,
            )
        )
    )
    return {
        "shape": {
            "points": points,
            "days": days,
            "interval_minutes": 1,
            "rows": rows,
            "source_bytes": source.stat().st_size,
        },
        "generation": generation,
        "inspection": inspect,
        "standardization": confirm,
        "data_quality": data_quality,
        "result_run_id": result.run_id,
        "notes": [
            "python_peak_mib 由 tracemalloc 测量，仅覆盖 Python 分配器；"
            "pandas/NumPy 原生分配可能更高。",
            "第一版容量承诺止于 50 点位、30 天、1 分钟采样；"
            "半年数据仅作探索，不属于支持容量。",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/capacity"))
    parser.add_argument("--points", type=int, default=50)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/PERFORMANCE_BASELINE.json"),
    )
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if args.clean and args.root.exists():
        shutil.rmtree(args.root)
    args.root.mkdir(parents=True, exist_ok=True)
    report = benchmark(args.root, points=args.points, days=args.days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
