"""Dry-weather filter tool and the human confirmation of its result."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


from agent.deps import AgentDeps
from agent.tools.manifest import data_fingerprint, load_manifest
from agent.types import ToolResult, needs_confirmation, ok
from analysis import io
from analysis.modules.filtering import FilterConfig, run_data_filter
from agent.tools.tool_support import _file_sha256, _rel, _run


def _resolve_filter_output_path(deps: AgentDeps, output_file: str | None) -> Path:
    out_path = Path(output_file) if output_file else deps.paths.filter_result
    if output_file and out_path.suffix == "":
        out_path = out_path.with_suffix(".xlsx")
    if not out_path.is_absolute():
        out_path = deps.paths.root / out_path
    return out_path


def _filter_result_identity(deps: AgentDeps, path: Path, params: dict[str, Any]) -> str | None:
    digest = _file_sha256(path)
    if digest is None:
        return None
    payload = {
        "data_fingerprint": data_fingerprint(deps)["digest"],
        "params": params,
        "path": _rel(deps, path),
        "sha256": digest,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _manifest_data_filter_item(deps: AgentDeps) -> dict[str, Any] | None:
    item = load_manifest(deps).get("results", {}).get("data_filter")
    return item if isinstance(item, dict) else None


def _fresh_manifest_filter_path(deps: AgentDeps, params: dict[str, Any], out_path: Path) -> Path | None:
    item = _manifest_data_filter_item(deps)
    if not item:
        return None
    if item.get("data_fingerprint") != data_fingerprint(deps)["digest"]:
        return None
    if item.get("params") != params:
        return None
    artifacts = [str(path) for path in item.get("artifacts") or []]
    candidates = artifacts or [_rel(deps, out_path)]
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = deps.paths.root / path
        if path.exists() and path.resolve() == out_path.resolve():
            return path
    return out_path if out_path.exists() else None


def _filter_confirmation_result(
    deps: AgentDeps,
    path: Path,
    params: dict[str, Any],
    summary_prefix: str,
) -> ToolResult:
    identity = _filter_result_identity(deps, path, params)
    rel_path = _rel(deps, path)
    deps.session.pending_filter_result_path = str(path)
    deps.session.pending_filter_result_identity = identity
    deps.session.pending_filter_result_params = deepcopy(params)
    deps.session.pending_filter_result_request = deps.session.current_user_prompt
    summary = f"{summary_prefix}{rel_path}。请确认或修改后告知继续。"
    deps.session.pending_filter_result_message = summary
    return needs_confirmation(
        "filter_result",
        "请确认是否使用当前筛选结果继续后续分析；如已人工修改文件，请回复“确认”或“改好了”。",
        summary=summary,
        artifacts=[rel_path] if path.exists() else [],
        output_file=str(path),
        filter_result_identity=identity,
    )


def _confirmed_filter_result_path(deps: AgentDeps) -> Path | None:
    if (
        deps.filter_baselines is not None
        and deps.current_project_id is not None
        and deps.current_batch_id is not None
    ):
        try:
            return deps.filter_baselines.baseline_artifact_path(
                deps.current_project_id, deps.current_batch_id
            )
        except ValueError:
            return None
    path_text = deps.session.confirmed_filter_result_path
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists():
        return None
    identity = _filter_result_identity(deps, path, deps.session.confirmed_filter_result_params)
    if identity and identity == deps.session.confirmed_filter_result_identity:
        return path
    return None


def confirm_pending_filter_result(deps: AgentDeps) -> Path:
    if (
        deps.session.pending_filter_id
        and deps.filter_baselines is not None
        and deps.current_project_id is not None
        and deps.current_batch_id is not None
    ):
        baseline = deps.filter_baselines.confirm(
            deps.current_project_id,
            deps.current_batch_id,
            deps.session.pending_filter_id,
        )
        deps.session.pending_filter_id = None
        deps.session.pending_filter_result_request = None
        deps.session.pending_filter_result_message = None
        return deps.filter_baselines.baseline_artifact_path(
            baseline.project_id, baseline.batch_id
        )
    path_text = deps.session.pending_filter_result_path
    if not path_text:
        raise ValueError("no pending filter result")
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(path)
    params = deepcopy(deps.session.pending_filter_result_params)
    identity = _filter_result_identity(deps, path, params)
    if identity is None:
        raise FileNotFoundError(path)
    deps.session.confirmed_filter_result_path = str(path)
    deps.session.confirmed_filter_result_identity = identity
    deps.session.confirmed_filter_result_params = params
    deps.session.pending_filter_result_path = None
    deps.session.pending_filter_result_identity = None
    deps.session.pending_filter_result_params = {}
    deps.session.pending_filter_result_message = None
    return path


_REFILTER_MARKERS = ("重新筛选", "重新运行筛选", "重新生成筛选", "再次筛选", "重跑筛选", "重做筛选")


def _requests_refilter(text: str) -> bool:
    return any(marker in text for marker in _REFILTER_MARKERS)


def data_filter_impl(
    deps: AgentDeps,
    missing_rate_threshold: float = 0.1,
    expected_rows_per_day: int = 1440,
    rain_day_filter_threshold: float = 2.0,
    zero_like_threshold: float = 0.02,
    high_zero_ratio_threshold: float = 0.5,
    high_zero_ratio_normal_days_threshold: int = 5,
    zero_day_drop_min_nonzero_keep_days: int = 3,
    mean_lower_ratio: float = 0.5,
    mean_upper_ratio: float = 2.0,
    output_file: str | None = None,
) -> ToolResult:
    params = {
        "missing_rate_threshold": missing_rate_threshold,
        "expected_rows_per_day": expected_rows_per_day,
        "rain_day_filter_threshold": rain_day_filter_threshold,
        "zero_like_threshold": zero_like_threshold,
        "high_zero_ratio_threshold": high_zero_ratio_threshold,
        "high_zero_ratio_normal_days_threshold": high_zero_ratio_normal_days_threshold,
        "zero_day_drop_min_nonzero_keep_days": zero_day_drop_min_nonzero_keep_days,
        "mean_lower_ratio": mean_lower_ratio,
        "mean_upper_ratio": mean_upper_ratio,
        "output_file": output_file or "",
    }
    if (
        deps.filter_baselines is not None
        and deps.current_project_id is not None
        and deps.current_batch_id is not None
    ):
        existing = deps.filter_baselines.current_baseline(
            deps.current_project_id, deps.current_batch_id
        )
        if existing is not None and not _requests_refilter(
            deps.session.current_user_prompt or ""
        ):
            return ok(
                f"当前已存在确认的第 {existing.version} 版分析基线（{existing.artifact}）。"
                "后续旱天分析直接读取该基线，无需重新筛选；如需重新筛选请明确告知。",
                artifacts=[existing.artifact],
                baseline_id=existing.baseline_id,
                identity=existing.identity,
            )
        from agent.tools.filter_baselines import run_filter_analysis

        shared_parameters = dict(params)
        shared_parameters.pop("output_file")
        result = run_filter_analysis(
            deps.filter_baselines,
            project_id=deps.current_project_id,
            batch_id=deps.current_batch_id,
            **shared_parameters,
        )
        filter_id = str(result.get("data", {}).get("filter_id") or "")
        if deps.session.auto_confirm_filter_result and filter_id:
            baseline = deps.filter_baselines.confirm(
                deps.current_project_id,
                deps.current_batch_id,
                filter_id,
            )
            return ok(
                "自动筛选已完成并确认为分析基线。",
                artifacts=[baseline.artifact],
                baseline_id=baseline.baseline_id,
                identity=baseline.identity,
            )
        deps.session.pending_filter_id = filter_id or None
        deps.session.pending_filter_result_request = (
            deps.session.current_user_prompt
        )
        deps.session.pending_filter_result_message = result.get("summary")
        return result
    out_path = _resolve_filter_output_path(deps, output_file)

    fresh_path = _fresh_manifest_filter_path(deps, params, out_path)
    if fresh_path is not None:
        identity = _filter_result_identity(deps, fresh_path, params)
        rel_path = _rel(deps, fresh_path)
        already_confirmed = (
            identity is not None
            and deps.session.confirmed_filter_result_identity == identity
            and deps.session.confirmed_filter_result_path == str(fresh_path)
        )
        if deps.session.auto_confirm_filter_result:
            deps.session.confirmed_filter_result_path = str(fresh_path)
            deps.session.confirmed_filter_result_identity = identity
            deps.session.confirmed_filter_result_params = deepcopy(params)
            return ok(f"筛选结果已存在且为 fresh，自动确认使用：{rel_path}。", artifacts=[rel_path], output_file=str(fresh_path))
        if already_confirmed:
            return ok(f"筛选结果已确认且为 fresh，直接使用：{rel_path}。", artifacts=[rel_path], output_file=str(fresh_path))
        return _filter_confirmation_result(deps, fresh_path, params, "已有 fresh 筛选结果：")

    def work() -> tuple[str, dict[str, Any]]:
        flow = io.load_flow(root=deps.paths.root)
        rain = io.load_rain(root=deps.paths.root)
        selected = run_data_filter(
            flow=flow,
            rain=rain,
            output_xlsx=out_path,
            config=FilterConfig(
                missing_rate_threshold=missing_rate_threshold,
                expected_rows_per_day=expected_rows_per_day,
                rain_day_filter_threshold=rain_day_filter_threshold,
                zero_like_threshold=zero_like_threshold,
                high_zero_ratio_threshold=high_zero_ratio_threshold,
                high_zero_ratio_normal_days_threshold=high_zero_ratio_normal_days_threshold,
                zero_day_drop_min_nonzero_keep_days=zero_day_drop_min_nonzero_keep_days,
                mean_lower_ratio=mean_lower_ratio,
                mean_upper_ratio=mean_upper_ratio,
            ),
        )
        point_count = len(selected)
        total_days = sum(len(days) for days in selected.values())
        summary = f"数据筛选完成：处理 {point_count} 个点位，筛出有效旱天 {total_days} 个点位日，输出 {_rel(deps, out_path)}。"
        return summary, {"selected": selected, "output_file": str(out_path)}

    result = _run(deps, "data_filter", work, params=params)
    if result.get("status") != "ok":
        return result
    identity = _filter_result_identity(deps, out_path, params)
    if deps.session.auto_confirm_filter_result:
        deps.session.confirmed_filter_result_path = str(out_path)
        deps.session.confirmed_filter_result_identity = identity
        deps.session.confirmed_filter_result_params = deepcopy(params)
        return result
    return _filter_confirmation_result(deps, out_path, params, "筛选结果已生成于 ")
