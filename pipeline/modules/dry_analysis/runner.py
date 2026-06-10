"""旱天分析模块入口

统一接口: run(config: Config, logger) -> dict

输出:
    - config.combined_xlsx_path 的 "旱天分析" Sheet
    - 返回值: {dry_curve_data, statistics, ...}
"""

import logging
from pathlib import Path
from typing import Any

from pipeline.core.config import Config

from .analyzer import run_dry_analysis


def run(config: Config, logger: logging.Logger) -> dict[str, Any]:
    """
    旱天分析入口。

    输入:
        - 流量数据目录（从 config.flow_data_dir）
        - 筛选结果（从 config.filter_result_path）
        - 点位信息（从 config.site_info_path）

    输出:
        - config.combined_xlsx_path 的 "旱天分析" Sheet

    返回:
        {
            "dry_curve_data": dict[str, pd.DataFrame],  # 平滑后特征曲线
            "dry_curve_data_workday": dict[str, pd.DataFrame],
            "dry_curve_data_weekend": dict[str, pd.DataFrame],
            "statistics": pd.DataFrame,
            "day_num": pd.DataFrame,
        }
    """
    flow_dir = config.flow_data_dir
    filter_result = config.filter_result_path
    combined_xlsx = config.combined_xlsx_path
    site_info = config.site_info_path

    logger.info(f"开始旱天分析")
    logger.info(f"  流量数据目录: {flow_dir}")
    logger.info(f"  筛选结果文件: {filter_result}")
    logger.info(f"  综合分析结果: {combined_xlsx}")

    # 构建配置参数
    analysis_config = {
        "smooth_window": config.smooth_window_minutes,
        "expected_rows_per_day": config.expected_rows_per_day,
    }

    # 执行分析
    result = run_dry_analysis(
        flow_dir=flow_dir,
        filter_result=filter_result,
        combined_xlsx=combined_xlsx,
        site_info=site_info,
        config=analysis_config,
    )

    logger.info(f"旱天分析完成")
    logger.info(f"  处理点位数: {len(result['dry_curve_data'])}")
    logger.info(f"  统计指标: {len(result['statistics'])} 个点位")

    return result

