"""CLI 入口：python -m pipeline.report_assembler"""

import argparse
from pathlib import Path

from .assembler import run_report_assembler


def main() -> None:
    parser = argparse.ArgumentParser(description="报告组装：将分析结果组装成 Word 报告")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("监测数据分析报告模板-更新.docx"),
        help="报告模板文件 (默认: 监测数据分析报告模板-更新.docx)",
    )
    parser.add_argument(
        "--analysis-results",
        type=Path,
        default=Path("outputs/分析结果.xlsx"),
        help="分析结果 xlsx 文件 (默认: outputs/分析结果.xlsx)",
    )
    parser.add_argument(
        "--site-info",
        type=Path,
        default=Path("点位信息.xlsx"),
        help="点位信息 xlsx 文件 (默认: 点位信息.xlsx)",
    )
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

    print(f"报告模板: {args.template}")
    print(f"分析结果: {args.analysis_results}")
    print(f"点位信息: {args.site_info}")
    print(f"旱天特征曲线: {args.dry_curve}")
    print(f"输出目录: {args.output_dir}")
    print()

    result = run_report_assembler(
        template_file=args.template,
        analysis_results_file=args.analysis_results,
        site_info_file=args.site_info,
        dry_curve_file=args.dry_curve,
        output_dir=args.output_dir,
    )

    print(f"\n报告已生成: {result['output_file']}")


if __name__ == "__main__":
    main()
