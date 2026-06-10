"""CLI 入口：python -m pipeline.data_filter"""

import argparse
from pathlib import Path

from .filter import run_data_filter


def main() -> None:
    parser = argparse.ArgumentParser(description="数据筛选：从监测数据中筛选有效旱天")
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=Path("data/flow"),
        help="流量数据 CSV 目录 (默认: data/flow)",
    )
    parser.add_argument(
        "--rainfall",
        type=Path,
        default=Path("data/rainfall/降雨数据.csv"),
        help="降雨数据 CSV 文件 (默认: data/rainfall/降雨数据.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/筛选结果.xlsx"),
        help="输出筛选结果 xlsx (默认: outputs/筛选结果.xlsx)",
    )
    args = parser.parse_args()

    print(f"流量数据目录: {args.csv_dir}")
    print(f"降雨数据文件: {args.rainfall}")
    print(f"输出文件: {args.output}")
    print()

    result = run_data_filter(
        csv_dir=args.csv_dir,
        rainfall_file=args.rainfall,
        output_xlsx=args.output,
    )

    total_days = sum(len(days) for days in result.values())
    print(f"\n筛选完成:")
    print(f"  - 处理点位数: {len(result)}")
    print(f"  - 有效旱天总数: {total_days}")
    print(f"  - 输出文件: {args.output}")

    for point_name, days in result.items():
        print(f"  - {point_name}: {len(days)} 天")


if __name__ == "__main__":
    main()

