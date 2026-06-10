"""CLI 入口：python -m pipeline.rdii_analysis"""

import argparse
from pathlib import Path

from .analyzer import run_rdii_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="RDII分析：降雨事件下的流量统计和RDII计算")
    parser.add_argument(
        "--flow-dir",
        type=Path,
        default=Path("data/flow"),
        help="流量数据 CSV 目录 (默认: data/flow)",
    )
    parser.add_argument(
        "--dry-curve",
        type=Path,
        default=Path("outputs/旱天特征曲线.pickle"),
        help="旱天特征曲线 pickle 文件 (默认: outputs/旱天特征曲线.pickle)",
    )
    parser.add_argument(
        "--event-data",
        type=Path,
        default=Path("outputs/场次降雨数据.pickle"),
        help="场次降雨数据 pickle 文件 (默认: outputs/场次降雨数据.pickle)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="输出目录 (默认: outputs)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=12.0,
        help="降雨效应延迟时间/小时 (默认: 12)",
    )
    args = parser.parse_args()

    print(f"流量数据目录: {args.flow_dir}")
    print(f"旱天特征曲线: {args.dry_curve}")
    print(f"场次降雨数据: {args.event_data}")
    print(f"输出目录: {args.output_dir}")
    print(f"降雨效应延迟: {args.delay} 小时")
    print()

    result = run_rdii_analysis(
        flow_dir=args.flow_dir,
        dry_curve_file=args.dry_curve,
        event_data_file=args.event_data,
        output_dir=args.output_dir,
        config={"rain_effect_delay": args.delay},
    )

    print(f"\nRDII分析完成:")

    if not result["rdii_total"].empty:
        print("\nRDII总量统计 (m3):")
        print(result["rdii_total"].to_string(index=False))

    if not result["max_level"].empty:
        print("\n降雨事件最大液位 (m):")
        print(result["max_level"].to_string(index=False))


if __name__ == "__main__":
    main()

