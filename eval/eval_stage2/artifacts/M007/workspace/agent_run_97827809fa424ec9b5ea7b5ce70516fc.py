
from pathlib import Path
import sys

PROJECT_ROOT = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M007-wxa4zfnv')
PACKAGE_ROOT = Path('D:\\huangxh\\AI_Projects_100\\P11_sewage_agent\\drainage_agent')
DATA_DIR = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M007-wxa4zfnv\\data')
OUTPUTS_DIR = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M007-wxa4zfnv\\outputs')
WORKSPACE_DIR = Path('C:\\Users\\mugua\\AppData\\Local\\Temp\\eval-M007-wxa4zfnv\\workspace')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
from analysis.io import load_filtered_flow, load_flow, load_rain, load_sites

from analysis.io import load_flow
df = load_flow(points=["W1"])
print(f"数据时间范围: {df['timestamp'].min()} 到 {df['timestamp'].max()}")
print(f"数据天数: {df['timestamp'].dt.date.nunique()}")
print(sorted(df['timestamp'].dt.date.unique()))
