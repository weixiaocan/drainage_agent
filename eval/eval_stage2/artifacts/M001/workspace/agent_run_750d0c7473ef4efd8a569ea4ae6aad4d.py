
from pathlib import Path
import sys

PROJECT_ROOT = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M001-p7bwrgi4')
PACKAGE_ROOT = Path('D:\\huangxh\\AI_Projects_100\\P11_sewage_agent\\drainage_agent')
DATA_DIR = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M001-p7bwrgi4\\data')
OUTPUTS_DIR = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M001-p7bwrgi4\\outputs')
WORKSPACE_DIR = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M001-p7bwrgi4\\workspace')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
from analysis.io import load_filtered_flow, load_flow, load_rain, load_sites


import pandas as pd
import numpy as np
from analysis.io import load_filtered_flow

# 读取 W1 和 W4 的旱天过滤后数据
df = load_filtered_flow()

# 只保留 W1 和 W4
df = df[df['point_id'].isin(['W1', 'W4'])].copy()

# 提取日期
df['date'] = df['timestamp'].dt.date

print("=== 基础统计对比 ===")
stats = df.groupby('point_id')['flow_lps'].agg(['count', 'mean', 'std', 'min', 'max', 'median'])
print(stats)
print()

# 按小时统计日变化
df['hour'] = df['timestamp'].dt.hour
hourly = df.groupby(['point_id', 'hour'])['flow_lps'].agg(['mean', 'std', 'count'])
print("=== 各点位逐小时均值 ===")
for pid in ['W1', 'W4']:
    print(f"\n--- {pid} ---")
    h = hourly.loc[pid]
    print(h.to_string())
print()

# 负值比例
for pid in ['W1', 'W4']:
    sub = df[df['point_id'] == pid]
    neg_ratio = (sub['flow_lps'] < 0).mean()
    zero_ratio = (sub['flow_lps'] == 0).mean()
    print(f"{pid}: 负值比例={neg_ratio:.4f} ({neg_ratio*100:.2f}%), 零值比例={zero_ratio:.4f} ({zero_ratio*100:.2f}%)")

# 有效日数
print("\n=== 有效旱天日数 ===")
for pid in ['W1', 'W4']:
    sub = df[df['point_id'] == pid]
    days = sub['date'].nunique()
    print(f"{pid}: {days} 天")

# 变异系数 CV（标准差/均值）
print("\n=== 变异系数(CV) ===")
for pid in ['W1', 'W4']:
    sub = df[df['point_id'] == pid]
    cv = sub['flow_lps'].std() / sub['flow_lps'].mean() if sub['flow_lps'].mean() != 0 else np.nan
    print(f"{pid}: CV={cv:.4f}")
