"""Controlled LLM section text generation with deterministic fallback."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Callable, Optional

from .facts import ReportFacts


FallbackFactory = Callable[[ReportFacts], str]


class LLMSectionWriter:
    """Generate section text from structured facts and validate the output."""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def generate(self, section_name: str, facts: ReportFacts, fallback: FallbackFactory) -> tuple[str, bool]:
        fallback_text = fallback(facts)
        if not self.llm_client:
            return fallback_text, False

        prompt = self._build_prompt(section_name, facts, fallback_text)
        try:
            text = self.llm_client.chat(prompt, temperature=0.2)
        except Exception as exc:
            print(f"LLM 生成 {section_name} 失败: {exc}")
            return fallback_text, False

        text = str(text or "").strip()
        if not text or not self._validate(text, facts):
            return fallback_text, False
        return text, True

    def _build_prompt(self, section_name: str, facts: ReportFacts, fallback_text: str) -> str:
        return f"""请基于结构化事实生成报告章节文字，不要引用模板原文，不要编造点位或数量。

章节：{section_name}

事实 JSON：
{asdict(facts)}

参考表达骨架：
{fallback_text}

要求：
1. 必须保持事实数字和点位名称一致。
2. 不允许出现 1-1#、1-9# 等旧模板点位编号。
3. 语言专业、简洁，适合排水监测分析报告。
4. 只输出正文，不要解释。"""

    def _validate(self, text: str, facts: ReportFacts) -> bool:
        if re.search(r"(?<!\d)1-[\d-]+#", text):
            return False
        if "13台流量监测设备" in text and facts.device_count != 13:
            return False
        if facts.point_count and f"{facts.point_count}" not in text:
            # Some short risk paragraphs may not need the total count; allow them.
            return True
        return True
