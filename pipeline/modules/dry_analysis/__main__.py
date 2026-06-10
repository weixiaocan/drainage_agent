"""CLI 入口：python -m pipeline.dry_analysis"""

import argparse
from pathlib import Path

from .analyzer import run_dry_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="旱天分析：计算旱天特征曲线和统计指标")
    parser.add_argument(
        "--flow-dir",
        type=Path,
        default=Path("data/flow"),
        help="流量数据 CSV 目录 (默认: data/flow)",
    )
    parser.add_argument(
        "--filter-result",
        type=Path,
        default=Path("outputs/筛选结果.xlsx"),
        help="筛选结果 xlsx 文件 (默认: outputs/筛选结果.xlsx)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="输出目录 (默认: outputs)",
    )
    parser.add_argument(
        "--site-info",
        type=Path,
        default=Path("点位信息.xlsx"),
        help="点位信息 xlsx 文件 (默认: 点位信息.xlsx)",
    )
    args = parser.parse_args()

    print(f"流量数据目录: {args.flow_dir}")
    print(f"筛选结果文件: {args.filter_result}")
    print(f"点位信息文件: {args.site_info}")
    print(f"输出目录: {args.output_dir}")
    print()

    result = run_dry_analysis(
        flow_dir=args.flow_dir,
        filter_result=args.filter_result,
        output_dir=args.output_dir,
        site_info=args.site_info,
    )

    print(f"\n旱天分析完成:")
    print(f"  - 处理点位数: {len(result['dry_curve_data'])}")
    print(f"  - 统计指标: {len(result['statistics'])} 个点位")

    print("\n统计结果:")
    for _, row in result["statistics"].iterrows():
        daily_flow = row['日均流量(m³/d)']
        max_level = row['最大液位(m)']
        print(f"  {row['点位编号']}: 日均流量 {daily_flow} m3/d, 最大液位 {max_level} m")


if __name__ == "__main__":
    main()

