from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path


def generate(
    destination: Path,
    *,
    points: int = 50,
    days: int = 30,
    interval_minutes: int = 1,
    seed: int = 20260728,
) -> int:
    """Write deterministic, non-identifying flow data without retaining rows."""
    if points < 1 or days < 1 or interval_minutes < 1:
        raise ValueError("points、days 和 interval_minutes 必须为正整数")
    destination.parent.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(seed)
    start = datetime(2026, 1, 1)
    samples = days * 24 * 60 // interval_minutes
    row_count = points * samples
    with destination.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "timestamp",
                "device_id",
                "point_id",
                "flow_lps",
                "level_m",
                "velocity_mps",
            ]
        )
        for point_index in range(1, points + 1):
            point_id = f"SYN{point_index:03d}"
            device_id = f"DEV{point_index:03d}"
            base_flow = 8.0 + point_index * 0.35
            phase = randomizer.random() * math.tau
            for sample in range(samples):
                timestamp = start + timedelta(
                    minutes=sample * interval_minutes
                )
                minute = timestamp.hour * 60 + timestamp.minute
                daily = math.sin(math.tau * minute / 1440 + phase)
                weekly = math.sin(
                    math.tau * timestamp.weekday() / 7 + phase / 2
                )
                flow = max(0.05, base_flow * (1 + 0.22 * daily + 0.06 * weekly))
                level = 0.35 + 0.012 * flow
                velocity = 0.18 + 0.009 * flow
                writer.writerow(
                    [
                        timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        device_id,
                        point_id,
                        f"{flow:.3f}",
                        f"{level:.3f}",
                        f"{velocity:.3f}",
                    ]
                )
    return row_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="生成可复现、无真实点位信息的容量测试 CSV"
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument("--points", type=int, default=50)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval-minutes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260728)
    args = parser.parse_args()
    rows = generate(
        args.destination,
        points=args.points,
        days=args.days,
        interval_minutes=args.interval_minutes,
        seed=args.seed,
    )
    print(f"{args.destination}: {rows} rows")


if __name__ == "__main__":
    main()
