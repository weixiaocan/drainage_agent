"""雨天事件统计模块 - 统计降雨事件下各点位的基本数据"""

from .runner import run
from .analyzer import run_event_stats

__all__ = ["run", "run_event_stats"]

