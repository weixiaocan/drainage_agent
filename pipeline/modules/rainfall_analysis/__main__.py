"""CLI 入口：python -m pipeline.rainfall_analysis"""

import argparse
from pathlib import Path

from .analyzer import run_rainfall_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="降雨分析：场次降雨划分和统计")
    parser.add_argument(
        "--rainfall-file",
        type=Path,
        default=Path("data/rainfall/降雨数据.csv"),
        help="降雨数据 CSV 文件 (默认: data/rainfall/降雨数据.csv)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="输出目录 (默认: outputs)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=12.0,
        help="场次降雨划分时间间隔/小时 (默认: 12)",
    )
    parser.add_argument(
        "--min-rainfall",
        type=float,
        default=1.0,
        help="最小降雨量阈值/mm (默认: 1)",
    )
    args = parser.parse_args()

    print(f"降雨数据文件: {args.rainfall_file}")
    print(f"输出目录: {args.output_dir}")
    print(f"划分间隔: {args.min_interval} 小时")
    print(f"最小降雨量: {args.min_rainfall} mm")
    print()

    result = run_rainfall_analysis(
        rainfall_file=args.rainfall_file,
        output_dir=args.output_dir,
        config={
            "min_interval": args.min_interval,
            "min_rainfall": args.min_rainfall,
        },
    )

    print(f"\n降雨分析完成:")
    print(f"  - 降雨日数: {(result['daily_rain']['日降雨量(mm)'] > 0).sum()}")
    print(f"  - 场次降雨数: {len(result['event_rain'])}")

    if not result["event_rain"].empty:
        print("\n场次降雨统计:")
        for _, row in result["event_rain"].iterrows():
            print(f"  场次{row['场次编号']}: {row['开始时间'].strftime('%m-%d %H:%M')} ~ {row['结束时间'].strftime('%m-%d %H:%M')}, "
                  f"降雨量 {row['总降雨量(mm)']}mm, {row['降雨等级']}")


if __name__ == "__main__":
    main()

