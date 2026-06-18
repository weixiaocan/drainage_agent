"""Monitoring site overview section."""

from __future__ import annotations

from docx import Document

from ..facts import ReportFacts
from ..report_tables import TABLE_SPECS, render_report_table
from ..template_scanner import TemplateMap
from .common import replace_first_paragraph


def render_site_overview(
    doc: Document,
    template_map: TemplateMap,
    context,
    facts: ReportFacts,
    warnings: list[str],
) -> dict[str, int]:
    stats = {"tables_filled": 0, "text_replaced": 0}

    for role in ("site_info", "collection_rate"):
        table = template_map.get(role)
        if table is None:
            warnings.append(f"监测概况缺少表格: {role}")
            continue
        warnings.extend(render_report_table(table, TABLE_SPECS[role], context))
        stats["tables_filled"] += 1

    if replace_first_paragraph(
        doc,
        "本轮共布设",
        f"本轮共布设{facts.point_count}个流量监测点位，时间段选择{facts.monitoring_period_text}。",
    ):
        stats["text_replaced"] += 1

    if replace_first_paragraph(
        doc,
        "期间对设备持续进行运维",
        (
            f"{facts.operation_period_text}期间对设备持续进行运维，"
            f"{facts.point_count}个监测点位在监测期间运行状态良好，"
            f"共收集分钟级监测数据超{facts.record_count_wan}万条，具体每台设备的点位获取情况如下表所示。"
        ),
    ):
        stats["text_replaced"] += 1

    if replace_first_paragraph(doc, "有效数据收集率", _collection_summary(facts)):
        stats["text_replaced"] += 1

    return stats


def _collection_summary(facts: ReportFacts) -> str:
    if facts.point_count == 0:
        return "本轮监测未识别到有效点位数据，需复核数据源。"

    if facts.collection_all_over_99:
        lead = f"{facts.point_count}个点位的有效数据收集率均超过99%"
    else:
        lead = (
            f"{facts.point_count}个点位的数据收集率范围为"
            f"{facts.collection_min:.2f}%-{facts.collection_max:.2f}%"
        )

    parts = [lead]
    if facts.collection_999_count:
        parts.append(f"{facts.collection_999_count}个监测点位的数据收集率处于99.9%-100%之间")
    if facts.full_collection_points:
        parts.append(f"{'、'.join(facts.full_collection_points)}数据获取率达到100%，数据无缺失")
    parts.append(f"{facts.device_count}台流量监测设备获取的监测数据真实、有效，可支撑整个项目的分析工作")
    return "，".join(parts) + "。"
