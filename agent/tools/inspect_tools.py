from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from agent.deps import AgentDeps
from agent.tools.manifest import data_fingerprint, load_manifest
from agent.types import ToolResult, error, ok


def _read_flow_head(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, nrows=2000)


def describe_data_impl(deps: AgentDeps) -> ToolResult:
    problems: list[str] = []
    flow_files = sorted(deps.paths.flow_dir.glob("*.csv"))
    if not deps.paths.flow_dir.exists():
        problems.append(f"缺少流量数据目录: {deps.paths.flow_dir}")
    if not flow_files:
        problems.append("未找到 flow/*.csv")
    if not deps.paths.site_info_file.exists():
        problems.append(f"缺少点位信息文件: {deps.paths.site_info_file.name}")
    if not deps.paths.rainfall_file.exists():
        problems.append(f"缺少降雨数据文件: {deps.paths.rainfall_file.name}")

    points: list[str] = []
    min_ts = None
    max_ts = None
    freq_counter: Counter[str] = Counter()
    missing_notes: list[str] = []

    for csv_path in flow_files:
        points.append(csv_path.stem.split("_", 1)[-1])
        try:
            df = _read_flow_head(csv_path)
            time_col = next((c for c in df.columns if str(c).lower() in {"timestamp", "time", "datetime"} or "时间" in str(c)), None)
            if time_col is None:
                problems.append(f"{csv_path.name}: 未找到时间列")
                continue
            ts = pd.to_datetime(df[time_col], errors="coerce").dropna()
            if ts.empty:
                problems.append(f"{csv_path.name}: 时间列无法解析")
                continue
            min_ts = ts.min() if min_ts is None else min(min_ts, ts.min())
            max_ts = ts.max() if max_ts is None else max(max_ts, ts.max())
            diffs = ts.sort_values().diff().dropna()
            if not diffs.empty:
                freq_counter[str(diffs.mode().iloc[0])] += 1
            missing_rate = float(df.isna().mean().mean())
            if missing_rate > 0:
                missing_notes.append(f"{csv_path.name}: 抽样缺失率约 {missing_rate:.1%}")
        except Exception as exc:
            problems.append(f"{csv_path.name}: 读取失败: {exc}")

    if problems and not flow_files:
        return error("数据目录未就绪；" + "；".join(problems), data={"problems": problems})

    summary = (
        f"发现 {len(flow_files)} 个流量文件、{len(set(points))} 个点位。"
        f"时间范围: {min_ts} 至 {max_ts}。"
    )
    if freq_counter:
        summary += f" 常见采样间隔: {freq_counter.most_common(1)[0][0]}。"
    if problems:
        summary += " 格式/缺失问题: " + "；".join(problems[:5])
    return ok(
        summary,
        flow_file_count=len(flow_files),
        points=sorted(set(points)),
        time_range=[str(min_ts), str(max_ts)],
        sampling_intervals=dict(freq_counter),
        missing_notes=missing_notes[:20],
        problems=problems,
    )


def _excel_sheets(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        with pd.ExcelFile(path) as xls:
            return list(xls.sheet_names)
    except Exception:
        return []


def list_results_impl(deps: AgentDeps) -> ToolResult:
    manifest = load_manifest(deps)
    current_fp = data_fingerprint(deps)["digest"]
    artifacts: list[str] = []
    results: dict[str, Any] = {}

    for path in sorted(deps.paths.outputs.rglob("*")):
        if path.is_file():
            rel = path.relative_to(deps.paths.root).as_posix()
            artifacts.append(rel)
            item: dict[str, Any] = {"mtime": path.stat().st_mtime, "size": path.stat().st_size}
            if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
                item["sheets"] = _excel_sheets(path)
            results[rel] = item

    manifest_results = {}
    for name, item in manifest.get("results", {}).items():
        manifest_results[name] = {
            **item,
            "fresh": item.get("data_fingerprint") == current_fp,
        }

    stale = [name for name, item in manifest_results.items() if not item.get("fresh")]
    summary = f"outputs 中发现 {len(artifacts)} 个文件。"
    if stale:
        summary += " 过期结果: " + ", ".join(stale)
    elif manifest_results:
        summary += " manifest 中的结果均为 fresh。"
    return ok(summary, artifacts=artifacts, results=results, manifest=manifest_results)
