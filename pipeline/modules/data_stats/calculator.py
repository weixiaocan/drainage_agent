"""数据收集率计算逻辑

计算每个监测点位的数据收集率统计指标。
"""

from pathlib import Path

import pandas as pd

from pipeline.core.data_utils import read_csv_with_fallback
from pipeline.core.schema import normalize_flow_df, parse_flow_filename


def calculate_point_stats(csv_path: Path) -> dict:
    """
    计算单个点位的数据收集率统计。

    Args:
        csv_path: 流量数据 CSV 文件路径

    Returns:
        {
            "point_name": 点位编号,
            "record_count": 监测数据条数,
            "monitoring_days": 监测天数,
            "theoretical_count": 理论数据条数,
            "collection_rate": 数据收集率（小数形式）
        }
    """
    # 读取数据
    df = read_csv_with_fallback(csv_path)
    df = normalize_flow_df(df, csv_path)

    # 解析点位编号
    point_name = parse_flow_filename(csv_path).point_id

    # 解析时间列
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])

    if df.empty:
        return {
            "point_name": point_name,
            "record_count": 0,
            "monitoring_days": 0,
            "theoretical_count": 0,
            "collection_rate": 0.0,
        }

    # 计算监测数据条数
    record_count = len(df)

    # 计算监测天数（时间跨度）
    min_time = df["timestamp"].min()
    max_time = df["timestamp"].max()
    monitoring_days = (max_time.date() - min_time.date()).days + 1

    # 计算理论数据条数
    theoretical_count = monitoring_days * 1440  # 每分钟一条

    # 计算数据收集率
    collection_rate = record_count / theoretical_count if theoretical_count > 0 else 0.0
    collection_rate = min(collection_rate, 1.0)  # 上限100%

    return {
        "point_name": point_name,
        "record_count": record_count,
        "monitoring_days": monitoring_days,
        "theoretical_count": theoretical_count,
        "collection_rate": collection_rate,
    }


def calculate_all_stats(flow_data_dir: Path) -> pd.DataFrame:
    """
    计算所有点位的数据收集率统计。

    Args:
        flow_data_dir: 流量数据目录

    Returns:
        统计结果 DataFrame，列：点位编号、监测数据条数、监测天数、理论数据条数、数据收集率(%)
    """
    results = []

    # 遍历所有 CSV 文件
    csv_files = sorted(flow_data_dir.glob("*.csv"))

    for csv_path in csv_files:
        try:
            stats = calculate_point_stats(csv_path)
            results.append(stats)
        except Exception as e:
            print(f"警告: 处理 {csv_path.name} 失败: {e}")
            continue

    # 构建 DataFrame
    df = pd.DataFrame(results)

    if df.empty:
        return df

    # 重命名列并格式化
    df = df.rename(
        columns={
            "point_name": "点位编号",
            "record_count": "监测数据条数",
            "monitoring_days": "监测天数",
            "theoretical_count": "理论数据条数",
            "collection_rate": "数据收集率(%)",
        }
    )

    # 收集率保持小数形式，写入 Excel 时设置百分比格式

    return df

