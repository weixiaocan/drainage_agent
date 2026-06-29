"""CLI for rendering a report from a serialized in-memory analysis bundle."""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path

from .assembler import run_report_assembler


def main() -> None:
    parser = argparse.ArgumentParser(description="从分析结果包组装 Word 报告")
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--analysis-bundle", type=Path, required=True, help="包含 DataFrame 字典的 pickle 文件")
    parser.add_argument("--site-info", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.analysis_bundle.open("rb") as file:
        analysis_results = pickle.load(file)
    if not isinstance(analysis_results, dict):
        raise TypeError("analysis bundle 必须是 dict[str, DataFrame]")

    result = run_report_assembler(
        template_file=args.template,
        analysis_results=analysis_results,
        site_info_file=args.site_info,
        output_file=args.output,
    )
    print(f"报告已生成: {result['output_file']}")


if __name__ == "__main__":
    main()
