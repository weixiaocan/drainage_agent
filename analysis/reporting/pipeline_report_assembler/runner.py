"""Compatibility entry point for the in-memory report assembler."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .assembler import run_report_assembler


class Config(Protocol):
    report_template_path: Path
    site_info_path: Path
    report_output_path: Path
    baseinfo_path: Path


def run(
    config: Config,
    logger: logging.Logger,
    analysis_results: dict[str, pd.DataFrame],
    dry_curve_data: dict[str, pd.DataFrame] | None = None,
    has_rainfall_data: bool = True,
    llm_client=None,
    sections: list[str] | None = None,
    point_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Render a report from in-memory analysis results using the fixed template."""
    logger.info("开始报告组装")
    logger.info("  报告模板: %s", config.report_template_path)
    logger.info("  点位信息: %s", config.site_info_path)
    logger.info("  输出文件: %s", config.report_output_path)

    result = run_report_assembler(
        template_file=config.report_template_path,
        analysis_results=analysis_results,
        site_info_file=config.site_info_path,
        output_file=config.report_output_path,
        dry_curve_data=dry_curve_data,
        config={"baseinfo_path": str(config.baseinfo_path)},
        has_rainfall_data=has_rainfall_data,
        llm_client=llm_client,
        sections=sections,
        point_ids=point_ids,
    )
    stats = result["stats"]
    logger.info("报告组装完成: 表格=%s, 图片=%s, 点位=%s", stats["tables_filled"], stats.get("images_inserted", 0), stats["points_processed"])
    return result
