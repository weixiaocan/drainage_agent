"""CLI 入口：python -m pipeline.pattern_analysis"""

import argparse
from pathlib import Path

from .analyzer import run_pattern_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="排污规律分析：基于旱天特征曲线判断排污规律")
    parser.add_argument(
        "--dry-curve",
        type=Path,
        default=Path("outputs/旱天特征曲线.pickle"),
        help="旱天特征曲线 pickle 文件 (默认: outputs/旱天特征曲线.pickle)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="输出目录 (默认: outputs)",
    )
    args = parser.parse_args()

    print(f"旱天特征曲线: {args.dry_curve}")
    print(f"输出目录: {args.output_dir}")
    print()

    result = run_pattern_analysis(
        dry_curve_file=args.dry_curve,
        output_dir=args.output_dir,
    )

    print("\n排污规律描述:")
    for point_name, desc in result["descriptions"].items():
        print(f"\n{point_name}:")
        print(f"  {desc[:100]}..." if len(desc) > 100 else f"  {desc}")


if __name__ == "__main__":
    main()

