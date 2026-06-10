"""命令行入口：独立运行数据收集率统计模块"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from pipeline.core.config import Config
from pipeline.core.logger import setup_logger

from .runner import run


def main():
    """命令行入口"""
    config = Config.load()
    logger = setup_logger(config.output_dir)
    result = run(config, logger)

    if result["stats_df"] is not None and not result["stats_df"].empty:
        print("\n数据收集率统计结果：")
        print(result["stats_df"].to_string(index=False))
    else:
        print("未找到有效数据")


if __name__ == "__main__":
    main()

