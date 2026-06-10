"""
core.config - 统一配置类

三层配置加载:
- 密钥层 (.env): API 密钥等敏感信息
- 技术层 (config.yaml): 输入输出路径、分析参数、LLM 开关
- 用户层 (baseinfo.xlsx): 项目信息、分析参数、选中场次
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml
from dotenv import load_dotenv

from .exceptions import ConfigLoadError


class Config:
    """
    运行时配置对象，聚合三层配置。

    使用方式:
        # 生产环境
        config = Config.load()

        # 测试环境
        config = Config.for_testing(output_dir=tmp_path)
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        env_path: Optional[Path] = None,
        baseinfo_path: Optional[Path] = None,
    ):
        """
        初始化配置。

        参数:
            config_path: config.yaml 路径，默认项目根目录
            env_path: .env 路径，默认项目根目录
            baseinfo_path: baseinfo.xlsx 路径，默认 data/baseinfo.xlsx
        """
        self._project_root = self._find_project_root()

        config_path = config_path or self._project_root / "config.yaml"
        env_path = env_path or self._project_root / ".env"

        self._config_path = config_path
        self._env_path = env_path

        self._env: dict[str, str] = {}
        self._yaml: dict[str, Any] = {}
        self._baseinfo: dict[str, Any] = {}

        self._load_env(env_path)
        self._load_yaml(config_path)

        # baseinfo_path 可以从 config.yaml 中配置
        if baseinfo_path is None:
            baseinfo_file = self._yaml.get("baseinfo_file")
            if baseinfo_file:
                baseinfo_path = self._resolve_path(baseinfo_file)
            else:
                baseinfo_path = self._project_root / "data" / "baseinfo.xlsx"

        self._baseinfo_path = baseinfo_path
        self._load_baseinfo(baseinfo_path)

    @classmethod
    def load(cls) -> Config:
        """生产环境加载配置。"""
        return cls()

    @classmethod
    def for_testing(cls, **kwargs) -> Config:
        """
        测试环境构造配置。

        直接设置属性，跳过文件加载。

        参数:
            output_dir: 输出目录路径
            flow_data_dir: 流量数据目录路径
            rainfall_data_path: 降雨数据路径
            ... 其他参数
        """
        config = cls.__new__(cls)
        config._project_root = Path(kwargs.get("project_root", "."))
        config._config_path = config._project_root / "config.yaml"
        config._env_path = config._project_root / ".env"
        config._baseinfo_path = config._project_root / "data" / "baseinfo.xlsx"

        config._env = {}
        config._yaml = {}
        config._baseinfo = {}

        output_dir = kwargs.get("output_dir")
        if output_dir:
            config._yaml["output"] = {"root_dir": str(output_dir)}

        flow_data_dir = kwargs.get("flow_data_dir")
        if flow_data_dir:
            config._yaml.setdefault("input", {})["data_dir"] = str(flow_data_dir)

        rainfall_data_path = kwargs.get("rainfall_data_path")
        if rainfall_data_path:
            config._yaml.setdefault("input", {})["rainfall_file"] = str(rainfall_data_path)

        site_info_path = kwargs.get("site_info_path")
        if site_info_path:
            config._yaml.setdefault("input", {})["site_info_file"] = str(site_info_path)

        report_template_path = kwargs.get("report_template_path")
        if report_template_path:
            config._yaml.setdefault("input", {})["report_template"] = str(report_template_path)

        # LLM 配置
        config._env["DEEPSEEK_API_KEY"] = kwargs.get("llm_api_key", "")
        config._env["DEEPSEEK_BASE_URL"] = kwargs.get("llm_base_url", "https://api.deepseek.com")
        config._env["DEEPSEEK_MODEL"] = kwargs.get("llm_model", "deepseek-chat")
        config._yaml["llm"] = {"enabled": kwargs.get("llm_enabled", False)}

        # 分析参数
        config._yaml["analysis"] = {
            "missing_rate_threshold": kwargs.get("missing_rate_threshold", 0.1),
            "expected_rows_per_day": kwargs.get("expected_rows_per_day", 1440),
            "smooth_window": kwargs.get("smooth_window_minutes", 20),
        }

        return config

    def reload_baseinfo(self) -> None:
        """重新加载 baseinfo.xlsx（介入点 2 后调用）。"""
        self._load_baseinfo(self._baseinfo_path)

    # ===== 内部加载方法 =====

    def _find_project_root(self) -> Path:
        """查找项目根目录（包含 config.yaml 的目录）。"""
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            if (parent / "config.yaml").exists():
                return parent
        return current

    def _load_env(self, path: Path) -> None:
        """加载 .env 文件。"""
        if path.exists():
            load_dotenv(path)
        # 从环境变量读取
        self._env = {
            "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
            "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "DEEPSEEK_MODEL": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        }

    def _load_yaml(self, path: Path) -> None:
        """加载 config.yaml 文件。"""
        if not path.exists():
            raise ConfigLoadError(f"配置文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            self._yaml = yaml.safe_load(f) or {}

    def _load_baseinfo(self, path: Path) -> None:
        """
        加载 baseinfo.xlsx 文件。

        文件不存在时使用默认值，不抛错。
        """
        defaults = {
            "project_name": "",
            "start_date": None,
            "end_date": None,
            "report_title": "",
            "author": "",
            "smooth_window_minutes": 20,
            "rainfall_gap_hours": 12,
            "rainfall_delay_hours": 48,
            "selected_rainfall_events": [],
        }

        if not path.exists():
            self._baseinfo = defaults
            return

        try:
            xlsx = pd.ExcelFile(path)

            # 读取项目基本信息
            if "项目基本信息" in xlsx.sheet_names:
                df = pd.read_excel(xlsx, sheet_name="项目基本信息")
                for _, row in df.iterrows():
                    key = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
                    value = row.iloc[1] if len(row) > 1 and pd.notna(row.iloc[1]) else ""
                    if "项目名称" in key:
                        defaults["project_name"] = str(value)
                    elif "监测开始时间" in key or "开始时间" in key:
                        defaults["start_date"] = value
                    elif "监测结束时间" in key or "结束时间" in key:
                        defaults["end_date"] = value
                    elif "报告标题" in key:
                        defaults["report_title"] = str(value)
                    elif "撰写人" in key:
                        defaults["author"] = str(value)

            # 读取分析参数
            if "分析参数" in xlsx.sheet_names:
                df = pd.read_excel(xlsx, sheet_name="分析参数")
                for _, row in df.iterrows():
                    key = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
                    value = row.iloc[1] if len(row) > 1 and pd.notna(row.iloc[1]) else None
                    if "平滑窗口" in key:
                        defaults["smooth_window_minutes"] = int(value) if value else 20
                    elif "降雨场次划分间隔" in key or "划分间隔" in key:
                        defaults["rainfall_gap_hours"] = int(value) if value else 12
                    elif "降雨影响延迟" in key or "影响延迟" in key:
                        defaults["rainfall_delay_hours"] = int(value) if value else 48

            # 读取降雨场次选择
            if "降雨场次选择" in xlsx.sheet_names:
                df = pd.read_excel(xlsx, sheet_name="降雨场次选择")
                events = []
                for _, row in df.iterrows():
                    val = row.iloc[0] if pd.notna(row.iloc[0]) else None
                    if val is not None:
                        try:
                            events.append(int(val))
                        except (ValueError, TypeError):
                            pass
                defaults["selected_rainfall_events"] = events

            self._baseinfo = defaults

        except Exception as e:
            self._baseinfo = defaults

    def _resolve_path(self, raw: str) -> Path:
        """解析相对路径为绝对路径。"""
        p = Path(raw)
        if p.is_absolute():
            return p
        return (self._config_path.parent / p).resolve()

    # ===== 输入路径 =====

    @property
    def flow_data_dir(self) -> Path:
        """流量数据目录。"""
        input_cfg = self._yaml.get("input", {})
        raw = input_cfg.get("data_dir", "data")
        return self._resolve_path(raw)

    @property
    def rainfall_data_path(self) -> Path:
        """降雨数据文件路径。"""
        input_cfg = self._yaml.get("input", {})
        raw = input_cfg.get("rainfall_file", "降雨数据.csv")
        return self._resolve_path(raw)

    @property
    def site_info_path(self) -> Path:
        """点位信息文件路径。"""
        input_cfg = self._yaml.get("input", {})
        raw = input_cfg.get("site_info_file", "点位信息.xlsx")
        return self._resolve_path(raw)

    @property
    def report_template_path(self) -> Path:
        """报告模板文件路径。"""
        input_cfg = self._yaml.get("input", {})
        raw = input_cfg.get("report_template", "监测数据分析报告模板.docx")
        return self._resolve_path(raw)

    @property
    def baseinfo_path(self) -> Path:
        """baseinfo.xlsx 文件路径。"""
        return self._baseinfo_path

    # ===== 输出路径 =====

    @property
    def output_dir(self) -> Path:
        """输出根目录。"""
        output_cfg = self._yaml.get("output", {})
        raw = output_cfg.get("root_dir", "outputs")
        return self._resolve_path(raw)

    @property
    def combined_xlsx_path(self) -> Path:
        """综合分析结果 Excel 路径。"""
        output_cfg = self._yaml.get("output", {})
        filename = output_cfg.get("combined_results_file", "综合分析结果.xlsx")
        return self.output_dir / filename

    @property
    def filter_result_path(self) -> Path:
        """筛选结果 Excel 路径。"""
        output_cfg = self._yaml.get("output", {})
        filename = output_cfg.get("filter_result_file", "筛选结果.xlsx")
        return self.output_dir / filename

    @property
    def report_output_path(self) -> Path:
        """报告输出 Word 路径。"""
        output_cfg = self._yaml.get("output", {})
        filename = output_cfg.get("report_file", "分析报告.docx")
        return self.output_dir / filename

    @property
    def charts_dir(self) -> Path:
        """图表输出目录。"""
        output_cfg = self._yaml.get("output", {})
        dirname = output_cfg.get("charts_dirname", "charts")
        return self.output_dir / dirname

    @property
    def logs_dir(self) -> Path:
        """日志输出目录。"""
        return self.output_dir / "logs"

    # ===== 用户参数（从 baseinfo.xlsx）=====

    @property
    def project_name(self) -> str:
        """项目名称。"""
        return self._baseinfo.get("project_name", "")

    @property
    def smooth_window_minutes(self) -> int:
        """平滑窗口（分钟），默认 20。"""
        return self._baseinfo.get("smooth_window_minutes", 20)

    @property
    def rainfall_gap_hours(self) -> int:
        """降雨场次划分间隔（小时），默认 12。"""
        return self._baseinfo.get("rainfall_gap_hours", 12)

    @property
    def rainfall_delay_hours(self) -> int:
        """降雨影响延迟（小时），默认 48。"""
        return self._baseinfo.get("rainfall_delay_hours", 48)

    @property
    def selected_rainfall_events(self) -> list[int]:
        """选中的降雨场次编号列表（介入点 2 后填写）。"""
        return self._baseinfo.get("selected_rainfall_events", [])

    # ===== 分析参数（从 config.yaml）=====

    @property
    def missing_rate_threshold(self) -> float:
        """缺失率阈值。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("missing_rate_threshold", 0.1))

    @property
    def expected_rows_per_day(self) -> int:
        """每天期望的行数（1 分钟采样为 1440）。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return int(analysis_cfg.get("expected_rows_per_day", 1440))

    @property
    def smooth_window(self) -> int:
        """平滑窗口（分钟），从 config.yaml 读取。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return int(analysis_cfg.get("smooth_window", 30))

    @property
    def sudden_zero_window_minutes(self) -> int:
        """突变零值检测窗口（分钟）。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return int(analysis_cfg.get("sudden_zero_window_minutes", 360))

    @property
    def rain_day_filter_threshold(self) -> float:
        """雨天过滤阈值（mm）。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("rain_day_filter_threshold", 2.0))

    @property
    def zero_like_threshold(self) -> float:
        """近似零值阈值。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("zero_like_threshold", 0.02))

    @property
    def high_zero_ratio_threshold(self) -> float:
        """高零值比例阈值。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("high_zero_ratio_threshold", 0.5))

    @property
    def iqr_factor(self) -> float:
        """IQR 因子。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("iqr_factor", 1.5))

    @property
    def mean_lower_ratio(self) -> float:
        """均值下限比例。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("mean_lower_ratio", 0.5))

    @property
    def mean_upper_ratio(self) -> float:
        """均值上限比例。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return float(analysis_cfg.get("mean_upper_ratio", 2.0))

    @property
    def plot_dpi(self) -> int:
        """图表 DPI。"""
        analysis_cfg = self._yaml.get("analysis", {})
        return int(analysis_cfg.get("plot_dpi", 120))

    # ===== LLM 配置 =====

    @property
    def llm_enabled(self) -> bool:
        """是否启用 LLM。"""
        llm_cfg = self._yaml.get("llm", {})
        return bool(llm_cfg.get("enabled", True))

    @property
    def llm_api_key(self) -> str:
        """LLM API 密钥。"""
        return self._env.get("DEEPSEEK_API_KEY", "")

    @property
    def llm_base_url(self) -> str:
        """LLM API 基础 URL。"""
        return self._env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    @property
    def llm_model(self) -> str:
        """LLM 模型名称。"""
        return self._env.get("DEEPSEEK_MODEL", "deepseek-chat")

