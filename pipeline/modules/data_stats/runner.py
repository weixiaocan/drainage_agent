"""数据收集率统计模块入口

统一接口: run(config: Config, logger) -> dict

输出:
    - 综合分析结果.xlsx 的 "数据收集率统计" Sheet
    - 返回值: {"stats_df": DataFrame, "collection_rates": {点位编号: 收集率}}
"""

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.core.config import Config

from .calculator import calculate_all_stats


def run(config: Config, logger: logging.Logger) -> dict[str, Any]:
    """
    数据收集率统计入口。

    输入:
        - 流量数据目录（从 config.flow_data_dir）

    输出:
        - config.combined_xlsx_path 的 "数据收集率统计" Sheet

    返回:
        {
            "stats_df": pd.DataFrame,           # 统计结果
            "collection_rates": dict[str, float]  # {点位编号: 收集率}
        }
    """
    csv_dir = config.flow_data_dir
    combined_xlsx = config.combined_xlsx_path

    logger.info("开始数据收集率统计")
    logger.info(f"  流量数据目录: {csv_dir}")
    logger.info(f"  输出文件: {combined_xlsx}")

    # 计算所有点位的统计数据
    stats_df = calculate_all_stats(csv_dir)

    if stats_df.empty:
        logger.warning("未找到有效的流量数据文件")
        return {"stats_df": pd.DataFrame(), "collection_rates": {}}

    # 写入 Excel
    _save_to_excel(stats_df, combined_xlsx)

    # 构建返回值
    collection_rates = dict(
        zip(stats_df["点位编号"], stats_df["数据收集率(%)"] / 100)
    )

    logger.info(f"数据收集率统计完成")
    logger.info(f"  处理点位数: {len(stats_df)}")
    logger.info(f"  平均收集率: {stats_df['数据收集率(%)'].mean():.2f}%")

    return {
        "stats_df": stats_df,
        "collection_rates": collection_rates,
    }


def _save_to_excel(df: pd.DataFrame, combined_xlsx: Path) -> None:
    """
    保存统计结果到 Excel。

    如果文件已存在，追加新 Sheet；否则创建新文件。
    数据收集率列以百分比格式显示。
    """
    if combined_xlsx.exists():
        with pd.ExcelWriter(
            combined_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace"
        ) as writer:
            df.to_excel(writer, sheet_name="数据收集率统计", index=False)
            _format_percentage(writer.sheets["数据收集率统计"], df, "数据收集率(%)")
    else:
        with pd.ExcelWriter(combined_xlsx, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="数据收集率统计", index=False)
            _format_percentage(writer.sheets["数据收集率统计"], df, "数据收集率(%)")


def _format_percentage(ws, df: pd.DataFrame, col_name: str) -> None:
    """设置指定列为百分比格式"""
    from openpyxl.styles import numbers

    # 找到列索引
    col_idx = df.columns.get_loc(col_name) + 1  # openpyxl 从 1 开始

    # 设置百分比格式（从第 2 行开始，跳过表头）
    for row_idx in range(2, len(df) + 2):
        ws.cell(row=row_idx, column=col_idx).number_format = "0.00%"

