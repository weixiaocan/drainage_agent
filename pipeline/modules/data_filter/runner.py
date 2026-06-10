"""数据筛选模块入口

统一接口: run(config: Config, logger) -> dict

输出:
    - config.filter_result_path（筛选结果.xlsx，介入点1）
    - 返回值: {"selected": {点位编号: [有效日期列表]}}
"""

import logging
from pathlib import Path
from typing import Any

from pipeline.core.config import Config

from .filter import run_data_filter


def run(config: Config, logger: logging.Logger) -> dict[str, Any]:
    """
    数据筛选入口。

    输入:
        - 流量数据目录（从 config.flow_data_dir）
        - 降雨数据文件（从 config.rainfall_data_path）

    输出:
        - config.filter_result_path（筛选结果.xlsx，介入点1）

    返回:
        {
            "selected": {点位编号: [有效日期 'yyyy-mm-dd', ...]},
        }
    """
    csv_dir = config.flow_data_dir
    rainfall_file = config.rainfall_data_path
    output_xlsx = config.filter_result_path

    logger.info(f"开始数据筛选")
    logger.info(f"  流量数据目录: {csv_dir}")
    logger.info(f"  降雨数据文件: {rainfall_file}")
    logger.info(f"  输出文件: {output_xlsx}")

    # 构建配置参数
    filter_config = {
        "missing_rate_threshold": config.missing_rate_threshold,
        "expected_rows_per_day": config.expected_rows_per_day,
        "rain_day_filter_threshold": config.rain_day_filter_threshold,
        "zero_like_threshold": config.zero_like_threshold,
        "high_zero_ratio_threshold": config.high_zero_ratio_threshold,
        "iqr_factor": config.iqr_factor,
        "mean_lower_ratio": config.mean_lower_ratio,
        "mean_upper_ratio": config.mean_upper_ratio,
    }

    # 执行筛选
    result = run_data_filter(
        csv_dir=csv_dir,
        rainfall_file=rainfall_file,
        output_xlsx=output_xlsx,
        config=filter_config,
    )

    total_days = sum(len(days) for days in result.values())
    logger.info(f"数据筛选完成")
    logger.info(f"  处理点位数: {len(result)}")
    logger.info(f"  有效旱天总数: {total_days}")

    return {"selected": result}

