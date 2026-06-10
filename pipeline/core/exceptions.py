"""
core.exceptions - 自定义异常

定义项目中使用的自定义异常类。
"""


class LLMDisabledError(Exception):
    """LLM 已禁用时调用 LLM 抛出的异常。"""

    def __init__(self, message: str = "LLM 已关闭"):
        super().__init__(message)


class LLMFailedAfterRetry(Exception):
    """LLM 调用重试 3 次后仍失败时抛出的异常。"""

    def __init__(self, message: str = "LLM 调用失败 3 次"):
        super().__init__(message)


class ConfigLoadError(Exception):
    """配置加载失败时抛出的异常。"""

    def __init__(self, message: str = "配置加载失败"):
        super().__init__(message)

