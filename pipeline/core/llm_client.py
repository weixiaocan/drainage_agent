"""
core.llm_client - LLM 客户端

统一的 LLM 调用入口，支持:
- 重试机制（3 次，指数退避）
- 全局禁用
- Prompt 模板加载
"""

import logging
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

from .config import Config
from .exceptions import LLMDisabledError, LLMFailedAfterRetry

logger = logging.getLogger(__name__)


class LLMClient:
    """
    统一的 LLM 客户端。

    使用方式:
        llm = LLMClient(config)
        try:
            result = llm.chat("分析这段数据...")
        except LLMDisabledError:
            result = "LLM 已关闭"
        except LLMFailedAfterRetry:
            result = "LLM 调用失败"
    """

    def __init__(self, config: Config):
        """
        初始化 LLM 客户端。

        参数:
            config: 配置对象
        """
        self.config = config
        self.enabled = config.llm_enabled

        if self.enabled and config.llm_api_key:
            self._client = OpenAI(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
            )
        else:
            self._client = None

    def chat(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.1,
    ) -> str:
        """
        统一的 LLM 调用入口。

        参数:
            prompt: 用户消息
            system: 系统消息（可选）
            temperature: 温度参数，默认 0.1

        返回:
            LLM 的文本响应

        异常:
            LLMDisabledError: config.llm_enabled = False 时抛出
            LLMFailedAfterRetry: 3 次重试都失败时抛出
        """
        if not self.enabled:
            raise LLMDisabledError("LLM 已关闭")

        if not self._client:
            raise LLMDisabledError("LLM API 密钥未配置")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error: Optional[Exception] = None

        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=self.config.llm_model,
                    messages=messages,
                    temperature=temperature,
                )
                return response.choices[0].message.content

            except Exception as e:
                last_error = e
                if attempt < 2:
                    wait = 2**attempt  # 1, 2, 4 秒
                    logger.warning(
                        f"LLM 调用失败(第 {attempt + 1} 次)，{wait} 秒后重试: {e}"
                    )
                    time.sleep(wait)

        raise LLMFailedAfterRetry(f"LLM 调用失败 3 次: {last_error}")

    def chat_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.1,
    ) -> str:
        """
        调用 LLM 并要求返回 JSON 格式。

        参数:
            prompt: 用户消息
            system: 系统消息（可选）
            temperature: 温度参数

        返回:
            LLM 的 JSON 文本响应

        异常:
            LLMDisabledError: LLM 禁用时
            LLMFailedAfterRetry: 重试后仍失败
        """
        if not self.enabled:
            raise LLMDisabledError("LLM 已关闭")

        if not self._client:
            raise LLMDisabledError("LLM API 密钥未配置")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error: Optional[Exception] = None

        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=self.config.llm_model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                return response.choices[0].message.content

            except Exception as e:
                last_error = e
                if attempt < 2:
                    wait = 2**attempt
                    logger.warning(
                        f"LLM 调用失败(第 {attempt + 1} 次)，{wait} 秒后重试: {e}"
                    )
                    time.sleep(wait)

        raise LLMFailedAfterRetry(f"LLM 调用失败 3 次: {last_error}")

    @staticmethod
    def load_prompt(name: str) -> str:
        """
        加载 prompts/ 目录下的模板文件。

        参数:
            name: 模板名称（不含 .txt 后缀）

        返回:
            模板内容

        异常:
            FileNotFoundError: 模板文件不存在
        """
        prompt_path = Path("prompts") / f"{name}.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt 模板不存在: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

