"""降雨分析模块入口

统一接口: run(config: Config, logger) -> dict

输出:
    - config.combined_xlsx_path 的 "日降雨量统计"/"场次降雨统计" Sheet
    - 返回值: {daily_rain, event_rain, event_data_dict, freq, ...}
"""

import logging
from pathlib import Path
from typing import Any

from pipeline.core.config import Config

from .analyzer import run_rainfall_analysis


def run(config: Config, logger: logging.Logger) -> dict[str, Any]:
    """
    降雨分析入口。

    输入:
        - 降雨数据文件（从 config.rainfall_data_path），支持分钟级或小时级数据

    输出:
        - config.combined_xlsx_path 的 "日降雨量统计"/"场次降雨统计" Sheet

    返回:
        {
            "daily_rain": pd.DataFrame,      # 日降雨量统计
            "event_rain": pd.DataFrame,       # 场次降雨统计
            "rain_data": pd.DataFrame,        # 预处理后的降雨数据
            "event_data_dict": dict,          # 场次降雨详细数据（供后续模块使用）
            "freq": str,                      # 数据频率 ("minute" 或 "hourly")
        }
    """
    rainfall_file = config.rainfall_data_path
    combined_xlsx = config.combined_xlsx_path

    logger.info(f"开始降雨分析")
    logger.info(f"  降雨数据文件: {rainfall_file}")
    logger.info(f"  综合分析结果: {combined_xlsx}")

    # 构建配置参数（从 baseinfo.xlsx 获取）
    analysis_config = {
        "min_interval": config.rainfall_gap_hours,
        "min_rainfall": 1.0,  # 默认最小降雨量阈值
    }

    # 执行分析
    result = run_rainfall_analysis(
        rainfall_file=rainfall_file,
        combined_xlsx=combined_xlsx,
        config=analysis_config,
    )

    rainy_days = (result["daily_rain"]["日降雨量(mm)"] > 0).sum()
    logger.info(f"降雨分析完成")
    logger.info(f"  降雨日数: {rainy_days}")
    logger.info(f"  场次降雨数: {len(result['event_rain'])}")

    return result

