"""
core.logger - 日志配置

配置全局日志，输出到文件和控制台。
"""

import logging
from datetime import datetime
from pathlib import Path


def setup_logger(output_dir: Path) -> Path:
    """
    配置全局日志。

    日志文件命名: outputs/logs/YYYY-MM-DD-HH-MM-SS.log
    同时输出到控制台。
    格式: 时间 | 级别 | 模块名 | 消息

    参数:
        output_dir: 输出根目录

    返回:
        日志文件路径
    """
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    log_file = log_dir / f"{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    return log_file

