"""报告组装模块 - 将分析结果组装成 Word 报告"""

from .runner import run
from .assembler import run_report_assembler
from .table_manager import adjust_table_rows, adjust_curve_image_tables
from .text_replacer import TextReplacer, build_context_from_data

__all__ = [
    "run",
    "run_report_assembler",
    "adjust_table_rows",
    "adjust_curve_image_tables",
    "TextReplacer",
    "build_context_from_data",
]
