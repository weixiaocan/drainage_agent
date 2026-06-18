"""Chapter-oriented report assembly orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from docx import Document

from .data_context import build_report_context
from .facts import build_report_facts
from .llm_section_writer import LLMSectionWriter
from .sections.pattern import render_pattern_section
from .sections.rainfall import render_rainfall_section
from .sections.risk import render_risk_section
from .sections.site_overview import render_site_overview
from .template_scanner import scan_template
from .validator import validate_report


@dataclass
class ReportConfig:
    """Report assembly configuration."""

    monitoring_start: str = ""
    monitoring_end: str = ""
    monitoring_round: str = "第一轮"
    rainfall_threshold_mm: float = 2.0
    baseinfo_path: str = ""


def run_report_assembler(
    template_file: Path,
    combined_xlsx: Path,
    site_info_file: Path,
    output_file: Path,
    dry_curve_data: Dict[str, pd.DataFrame] | None = None,
    filter_result_path: Path | None = None,
    config: Dict[str, Any] | None = None,
    has_rainfall_data: bool = True,
    llm_client=None,
) -> Dict[str, Any]:
    """Assemble the Word report by template sections."""
    cfg = _build_config(config)
    template_file = Path(template_file)
    combined_xlsx = Path(combined_xlsx)
    site_info_file = Path(site_info_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"读取报告模板: {template_file}")
    doc = Document(template_file)
    context = build_report_context(
        combined_xlsx=combined_xlsx,
        site_info_file=site_info_file,
        dry_curve_data=dry_curve_data,
        has_rainfall_data=has_rainfall_data,
    )
    template_map = scan_template(doc)
    baseinfo_path = Path(cfg.baseinfo_path) if cfg.baseinfo_path else _default_baseinfo_path(template_file)
    facts = build_report_facts(context, baseinfo_path=baseinfo_path)
    llm_writer = LLMSectionWriter(llm_client)

    warnings: list[str] = []
    warnings.extend(context.warnings)
    warnings.extend(template_map.warnings)
    stats = {
        "tables_filled": 0,
        "images_inserted": 0,
        "points_processed": facts.point_count,
        "text_replaced": 0,
        "llm_generated": 0,
        "warnings": 0,
    }

    print(f"报告包含 {len(doc.tables)} 个表格")
    print(f"识别点位: {facts.point_ids}")

    for section_stats in [
        render_site_overview(doc, template_map, context, facts, warnings),
        render_rainfall_section(doc, template_map, context, facts, output_file.parent, warnings),
        render_pattern_section(doc, template_map, facts, output_file.parent / "特征曲线图", llm_writer, warnings),
        render_risk_section(doc, template_map, context, facts, llm_writer, warnings),
    ]:
        _merge_stats(stats, section_stats)

    validation = validate_report(doc, facts)
    warnings.extend(validation.warnings)
    if validation.critical:
        warnings.extend(validation.critical)
        raise ValueError("报告校验失败: " + "；".join(validation.critical))

    doc.save(output_file)
    stats["warnings"] = len(warnings)
    print(f"保存报告: {output_file}")
    _print_warnings(warnings)
    return {"output_file": output_file, "stats": stats, "warnings": warnings}


def _build_config(config: Optional[Dict[str, Any]]) -> ReportConfig:
    cfg = ReportConfig()
    if config:
        for key, value in config.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    return cfg


def _default_baseinfo_path(template_file: Path) -> Path:
    project_root = template_file.parent.parent
    return project_root / "data" / "baseinfo.xlsx"


def _merge_stats(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def _print_warnings(warnings: list[str]) -> None:
    if not warnings:
        return
    print("报告组装 warnings:")
    for warning in warnings[:20]:
        print(f"  - {warning}")
    if len(warnings) > 20:
        print(f"  - ... 另有 {len(warnings) - 20} 条")
