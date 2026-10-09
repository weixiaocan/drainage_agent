import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agent.tools.manifest import load_manifest

STAGE_DIR = Path(__file__).resolve().parent
PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.deps import build_deps
from analysis import io
from analysis.io.standard import STANDARD_FLOW_COLUMNS
from agent.core import build_agent
from agent.core.logging_utils import TraceLogger, trace_event
from quality.eval.check import build_context, load_cases, print_summary_report, run_checks


CASE_ID_PREFIX_RE = re.compile(r"^(M\d{3}[A-Z]?)")


def canonical_case_id(case_id: str) -> str:
    match = CASE_ID_PREFIX_RE.match(str(case_id))
    return match.group(1) if match else str(case_id)


def cleanup_artifacts_for_case(case_id: str, *, artifacts_dir: Path | None = None) -> Path:
    artifacts_dir = artifacts_dir or (STAGE_DIR / "artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    canonical_id = canonical_case_id(case_id)
    for child in artifacts_dir.iterdir():
        if child.is_dir() and canonical_case_id(child.name) == canonical_id:
            shutil.rmtree(child)
    return artifacts_dir / canonical_id


def fresh_root(root: Path, setup: dict | None = None) -> Path:
    """每条用例一个隔离 root：只拷只读输入，var/outputs/记忆全空。"""
    setup = setup or {}
    fixture = setup.get("fixture", "default")
    if fixture != "default":
        raise ValueError(f"未知 Eval fixture: {fixture}")
    shutil.copytree(PROJECT / "resources" / "data", root / "resources" / "data")
    shutil.copytree(PROJECT / "resources" / "templates", root / "resources" / "templates")
    shutil.copytree(PROJECT / "agent" / "prompts", root / "agent" / "prompts")
    removable_inputs = {
        "rainfall": root / "resources" / "data" / "降雨数据.csv",
        "site_info": root / "resources" / "data" / "点位信息.xlsx",
        "report_template": root / "resources" / "templates" / "监测数据分析报告模板-更新.docx",
    }
    for name in setup.get("remove_inputs", []):
        path = removable_inputs.get(name)
        if path is None:
            raise ValueError(f"未知 remove_inputs 值: {name}")
        if path.exists():
            path.unlink()
    for name, source_text in setup.get("overlay_inputs", {}).items():
        destination = removable_inputs.get(name)
        if destination is None:
            raise ValueError(f"未知 overlay_inputs 值: {name}")
        source = (PROJECT / source_text).resolve()
        fixtures_root = (PROJECT / "quality" / "eval" / "fixtures").resolve()
        if not source.is_relative_to(fixtures_root) or not source.is_file():
            raise ValueError(f"Eval fixture 文件不存在或越界: {source_text}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for d in ("outputs", "workspace", "logs"):
        (root / "var" / d).mkdir(parents=True)
    return root


SITE_STANDARD_COLUMNS = {
    "安装监测点位": "point_id",
    "类型": "device_type",
    "形状": "shape",
    "管径(m)": "diameter_m",
    "井深(m)": "well_depth_m",
    "设备安装时间": "install_time",
}


def enable_python_execution(deps, root: Path, case_id: str) -> None:
    """When a sandbox is configured, give run_python the standard inputs a confirmed project batch has.

    The files live outside the analysis root so analysis tools keep reading the raw fixture.
    """
    if deps.python_sandbox is None:
        return
    inputs_root = root / "sandbox_inputs"
    standard = inputs_root / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    flow = io.load_flow(root=root)
    flow[STANDARD_FLOW_COLUMNS].to_csv(
        standard / "flow.csv", index=False, encoding="utf-8", date_format="%Y-%m-%dT%H:%M:%S"
    )
    rain = io.load_rain(root=root)
    if not rain.empty:
        rain[["timestamp", "rain_mm"]].to_csv(
            standard / "rainfall.csv", index=False, encoding="utf-8", date_format="%Y-%m-%dT%H:%M:%S"
        )
    sites = io.load_sites(root=root)
    if not sites.empty:
        sites = sites.rename(columns=SITE_STANDARD_COLUMNS)
        sites[[c for c in SITE_STANDARD_COLUMNS.values() if c in sites.columns]].to_csv(
            standard / "sites.csv", index=False, encoding="utf-8"
        )
    deps.sandbox_inputs_root = inputs_root
    deps.current_project_id = "eval"
    deps.current_batch_id = canonical_case_id(case_id)
    deps.cancel_session_id = f"eval-{canonical_case_id(case_id)}"


def tree_snapshot(root: Path) -> list[dict]:
    """记录隔离 root 的可比较文件清单，不读取或保存业务文件正文。"""
    snapshot = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        snapshot.append({"path": relative, "size": stat.st_size, "sha256": digest.hexdigest()})
    return snapshot


def trace_evidence(trace: TraceLogger | None, run_id: str) -> list[dict]:
    """提取本轮客观事件，供状态、工具结果和副作用自动判定。"""
    if trace is None or not trace.path.exists():
        return []
    evidence = []
    for line in trace.path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("run_id") == run_id:
            evidence.append(event)
    return evidence


def apply_after_seed_mutation(root: Path, setup: dict) -> None:
    """只在临时隔离 root 中制造结果失效，永不修改仓库夹具。"""
    mutation = setup.get("after_seed_mutation")
    if mutation in (None, ""):
        return
    if mutation != "append_flow_newline":
        raise ValueError(f"未知 after_seed_mutation: {mutation}")
    flow_root = (root / "resources" / "data" / "flow").resolve()
    candidates = sorted(flow_root.glob("*.csv"))
    if not candidates:
        raise ValueError("after_seed_mutation 找不到流量 CSV")
    target = candidates[0].resolve()
    if not target.is_relative_to(flow_root):
        raise ValueError("after_seed_mutation 目标越界")
    with target.open("a", encoding="utf-8", newline="") as stream:
        stream.write("\n")


def preserve_artifacts(root: Path, case_id: str, *, artifacts_dir: Path | None = None) -> Path:
    """保存人工判分需要的产物，然后允许临时 root 被安全清理。"""
    destination = cleanup_artifacts_for_case(case_id, artifacts_dir=artifacts_dir)
    destination.mkdir(parents=True)
    for name in ("outputs", "workspace", "logs"):
        source = root / "var" / name
        if source.exists():
            shutil.copytree(source, destination / name)
    generated = root / "results" / "generated"
    if generated.exists():
        shutil.copytree(generated, destination / "results" / "generated")
    return destination


def tool_seq(messages):
    seq = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if getattr(part, "part_kind", None) == "tool-call":
                seq.append({"tool": part.tool_name, "args": part.args})
    return seq


def run_turn(prompt, deps, agent, trace, message_history=None):
    run_id = uuid.uuid4().hex
    deps.session.current_run_id = run_id
    trace_event(trace, {"event": "turn_start", "run_id": run_id, "user": prompt})
    try:
        result = agent.run_sync(prompt, deps=deps, message_history=message_history)
    except Exception as exc:
        trace_event(trace, {"event": "turn_error", "run_id": run_id, "error": repr(exc)})
        raise
    trace_event(trace, {"event": "turn_end", "run_id": run_id, "reply": str(result.output)})
    return result, run_id


def normalize_case(case: dict) -> dict:
    """把单轮和多轮用例统一成 {id, turns:[{prompt, expect, key}], seed_prompts, rebuild_after_seed}。
    单轮用例 turns 只有一个元素，结构统一后下游（runner / view）无需区分两种格式。"""
    expected = case.get("expected", {})
    default_expect = expected.get("response", "") if isinstance(expected, dict) else ""
    if "turns" in case:
        turns = [
            {
                "prompt": t["prompt"],
                "expect": t.get("expect", "") or t.get("expected", {}).get("response", ""),
                "expected": t.get("expected", {}),
                "key": bool(t.get("key", False)),
                "category": case.get("category", ""),
            }
            for t in case["turns"]
        ]
    else:
        turns = [{
            "prompt": case["prompt"],
            "expect": case.get("pass_when", "") or default_expect,
            "expected": expected,
            "key": True,
            "category": case.get("category", ""),
        }]
    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "scenario": case.get("scenario", ""),
        "dimensions": case.get("dimensions", {}),
        "setup": case.get("setup", {"fixture": "default"}),
        "expected": expected,
        "conversation_goal": case.get("conversation_goal", ""),
        "state_under_test": case.get("state_under_test", []),
        "turns": turns,
        "seed_prompts": case.get("seed_prompts", []),
        "rebuild_after_seed": bool(case.get("rebuild_after_seed", False)),
    }


def validate_cases(cases: list[dict]) -> None:
    ids = [str(case.get("id") or "") for case in cases]
    if any(not case_id for case_id in ids):
        raise ValueError("Eval 用例 id 不能为空")
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"Eval 用例 id 重复: {duplicates}")
    for case in cases:
        normalized = normalize_case(case)
        if not normalized["turns"] or any(not turn["prompt"].strip() for turn in normalized["turns"]):
            raise ValueError(f"{case['id']}: prompt 不能为空")
        if case.get("scenario") and not normalized["dimensions"]:
            raise ValueError(f"{case['id']}: schema v2 用例缺少 dimensions")
        if case.get("scenario") and not normalized["expected"]:
            raise ValueError(f"{case['id']}: schema v2 用例缺少 expected")


def select_cases(cases: list[dict], case_ids: list[str] | None) -> list[dict]:
    if not case_ids:
        return cases
    by_id = {str(case["id"]): case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"未知 Eval case id: {missing}")
    return [by_id[case_id] for case_id in dict.fromkeys(case_ids)]


def try_usage(result) -> dict | None:
    """尽力取 token 用量；不同 pydantic-ai 版本接口不一，取不到就返回 None。"""
    try:
        usage = result.usage
        u = usage() if callable(usage) else usage
        return {
            "requests": getattr(u, "requests", None),
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
            "total_tokens": getattr(u, "total_tokens", None),
        }
    except Exception:
        return None


def completed_case_ids(pending_path: Path) -> set[str]:
    """读取中断执行留下的临时 JSONL，返回已经完整落盘的 case id。"""
    completed: set[str] = set()
    if not pending_path.exists():
        return completed
    for line in pending_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("id"):
            completed.add(str(record["id"]))
    return completed


def compact_pending_results(pending_path: Path) -> None:
    """按 case id 去重中断恢复结果，保留最后一条完整记录和首条元数据。"""
    meta: dict | None = None
    order: list[str] = []
    records: dict[str, dict] = {}
    for line in pending_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "_meta" in record:
            if meta is None:
                meta = record
            continue
        case_id = record.get("id")
        if not case_id:
            continue
        case_id = str(case_id)
        if case_id not in records:
            order.append(case_id)
        records[case_id] = record
    lines = []
    if meta is not None:
        lines.append(json.dumps(meta, ensure_ascii=False))
    lines.extend(json.dumps(records[case_id], ensure_ascii=False) for case_id in order)
    pending_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_objective_check(results_path: Path) -> None:
    print("\n客观项自动判分:")
    try:
        cases = load_cases(results_path)
        ctx = build_context(PROJECT)
        results = run_checks(cases, ctx)
        checks_path = results_path.with_name(f"{results_path.stem}_checks.json")
        checks_path.write_text(
            json.dumps({"checks": [asdict(item) for item in results]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"自动检查明细 -> {checks_path}")
        print_summary_report(results)
    except Exception as exc:
        print(f"客观项自动判分失败: {exc!r}")


def run_case(case: dict, *, auto_confirm: bool = True, artifacts_dir: Path | None = None) -> dict:
    """跑一条（已归一化的）用例：在同一个隔离 root、同一条 message_history 上逐轮推进。"""
    rec = {
        "id": case["id"],
        "category": case["category"],
        "scenario": case["scenario"],
        "dimensions": case["dimensions"],
        "setup": case["setup"],
        "expected": case["expected"],
        "conversation_goal": case["conversation_goal"],
        "state_under_test": case["state_under_test"],
        "turns": [],
        "state": {"before": [], "after": []},
        "root": "",
        "trace": "",
        "error": None,
    }
    trace = None
    with tempfile.TemporaryDirectory(prefix=f"eval-{case['id']}-") as temp_dir:
        root = Path(temp_dir)
        try:
            fresh_root(root, case["setup"])
            rec["state"]["before"] = tree_snapshot(root)
            deps = build_deps(root)
            deps.session.auto_confirm_filter_result = auto_confirm
            enable_python_execution(deps, root, case["id"])
            trace = TraceLogger(deps.paths.logs)
            deps.trace = trace
            agent = build_agent(deps)

            message_history = []
            # 前置（仅有状态的用例需要）
            for seed in case["seed_prompts"]:
                seed_result, _ = run_turn(seed, deps, agent, trace, message_history)
                message_history = seed_result.all_messages()
            apply_after_seed_mutation(root, case["setup"])
            if case["rebuild_after_seed"]:
                deps = build_deps(root)
                deps.session.auto_confirm_filter_result = auto_confirm
                enable_python_execution(deps, root, case["id"])
                deps.trace = trace
                agent = build_agent(deps)
                message_history = []

            # 逐轮推进：每轮把全量历史接力给下一轮
            for i, turn in enumerate(case["turns"]):
                manifest_before = set(load_manifest(deps).get("results", {}).keys())
                result, run_id = run_turn(turn["prompt"], deps, agent, trace, message_history)
                captured = tool_seq(result.new_messages())
                if not captured:
                    # Pydantic AI edge case: tool calls may not appear in new_messages()
                    # for certain multi-turn patterns.  Fall back to manifest delta.
                    manifest_after = set(load_manifest(deps).get("results", {}).keys())
                    new_tools = manifest_after - manifest_before
                    captured = [{"tool": t, "args": {}} for t in new_tools]
                rec["turns"].append({
                    "n": i + 1,
                    "run_id": run_id,
                    "prompt": turn["prompt"],
                    "expect": turn["expect"],
                    "expected": turn["expected"],
                    "key": turn["key"],
                    "output": str(result.output),
                    "tool_calls": captured,
                    "_debug_all_tool_calls": tool_seq(result.all_messages()),
                    "trace_events": trace_evidence(trace, run_id),
                    "usage": try_usage(result),
                })
                message_history = result.all_messages()
        except Exception as exc:
            rec["error"] = repr(exc)
        finally:
            rec["state"]["after"] = tree_snapshot(root)

        # 无论成功失败都尽量保全已产生的产物
        try:
            artifact_root = preserve_artifacts(root, case["id"], artifacts_dir=artifacts_dir)
            rec["root"] = str(artifact_root)
            if trace is not None:
                rec["trace"] = str(artifact_root / "logs" / trace.path.name)
        except Exception as exc:
            err = f"artifact preservation failed: {exc!r}"
            rec["error"] = f"{rec['error']}; {err}" if rec["error"] else err
    return rec


def main():
    ap = argparse.ArgumentParser(description="排水 agent eval runner（单轮/多轮）")
    ap.add_argument("cases_file", nargs="?", default=str(STAGE_DIR / "cases_multiturn_v2.yaml"),
                    help="用例文件，默认 quality/eval/eval_stage2/cases_multiturn_v2.yaml")
    ap.add_argument("-o", "--out", default=None, help="输出 jsonl，默认 quality/eval/eval_stage2/results.jsonl")
    ap.add_argument("--resume", action="store_true", help="从已有 .tmp 结果断点续跑，跳过已完整写入的 case")
    ap.add_argument("--auto-confirm", dest="auto_confirm", action="store_true", default=True,
                    help="auto-confirm data_filter results; default for regression eval")
    ap.add_argument("--no-auto-confirm", dest="auto_confirm", action="store_false",
                    help="pause after data_filter for HITL hook eval")
    ap.add_argument("--validate-only", action="store_true",
                    help="只验证用例结构和隔离 fixture，不调用模型")
    ap.add_argument("--case-id", action="append", dest="case_ids",
                    help="只运行指定 case id；可重复传入")
    ap.add_argument("--artifacts-dir", default=None,
                    help="产物证据目录；只读部署中应指向显式可写路径")
    ap.add_argument("--repeat", type=int, default=1,
                    help="同一组用例连续运行的轮次；大于 1 时每轮单独输出并汇总各用例通过轮次")
    args = ap.parse_args()
    if args.repeat < 1:
        ap.error("--repeat 必须大于等于 1")
    if args.repeat > 1 and args.resume:
        ap.error("--repeat 与 --resume 不能同时使用")

    load_dotenv(PROJECT / ".env")

    cases_path = (PROJECT / args.cases_file) if not Path(args.cases_file).is_absolute() else Path(args.cases_file)
    raw_cases = yaml.safe_load(cases_path.read_text("utf-8"))
    validate_cases(raw_cases)
    raw_cases = select_cases(raw_cases, args.case_ids)
    cases = [normalize_case(c) for c in raw_cases]

    if args.validate_only:
        for case in cases:
            with tempfile.TemporaryDirectory(prefix=f"eval-validate-{case['id']}-") as temp_dir:
                fresh_root(Path(temp_dir), case["setup"])
        print(f"validated {len(cases)} cases: {cases_path}")
        return

    out_path = Path(args.out) if args.out else (STAGE_DIR / "results.jsonl")
    if not out_path.is_absolute():
        out_path = PROJECT / out_path
    artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else None
    if artifacts_dir is not None and not artifacts_dir.is_absolute():
        artifacts_dir = PROJECT / artifacts_dir

    # 可复现元数据：尽力从一个临时 deps 读出 model/参数
    meta = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": str(cases_path.relative_to(PROJECT)) if cases_path.is_relative_to(PROJECT) else str(cases_path),
        "case_count": len(cases),
        "model": None,
        "model_settings": None,
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "schema_version": 2,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="eval-meta-") as td:
            r = fresh_root(Path(td))
            d = build_deps(r)
            settings = getattr(d, "settings", None)
            meta["model"] = getattr(settings, "model", None) or getattr(d, "model", None)
            meta["model_settings"] = {
                k: getattr(settings, k, None) for k in ("temperature", "max_tokens")
            } if settings else None
    except Exception:
        pass

    if args.repeat == 1:
        run_once(cases, out_path, meta, auto_confirm=args.auto_confirm,
                 artifacts_dir=artifacts_dir, resume=args.resume)
        return
    round_paths = []
    for index in range(1, args.repeat + 1):
        print(f"\n===== 第 {index}/{args.repeat} 轮 =====")
        round_path = out_path.with_name(f"{out_path.stem}.r{index}{out_path.suffix}")
        round_artifacts = artifacts_dir / f"r{index}" if artifacts_dir is not None else None
        run_once(cases, round_path, {**meta, "round": index}, auto_confirm=args.auto_confirm,
                 artifacts_dir=round_artifacts, resume=False)
        round_paths.append(round_path)
    print_repeat_summary(round_paths)


def run_once(cases: list[dict], out_path: Path, meta: dict, *, auto_confirm: bool,
             artifacts_dir: Path | None, resume: bool) -> None:
    pending_path = out_path.with_name(f"{out_path.name}.tmp")
    completed = completed_case_ids(pending_path) if resume else set()
    mode = "a" if resume and pending_path.exists() else "w"
    with pending_path.open(mode, encoding="utf-8") as out:
        if mode == "w":
            # 第一行写元数据，便于日后复现与对比
            out.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
            out.flush()
        for case in cases:
            if case["id"] in completed:
                print(f"{case['id']}: 已完成，跳过")
                continue
            rec = run_case(case, auto_confirm=auto_confirm, artifacts_dir=artifacts_dir)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n_tools = sum(len(t["tool_calls"]) for t in rec["turns"])
            print(f"{rec['id']}: {len(rec['turns'])} 轮 / {n_tools} 次工具调用"
                  f"{' [报错]' if rec['error'] else ''}")
    compact_pending_results(pending_path)
    pending_path.replace(out_path)
    print(f"\n→ {out_path}  (model={meta['model']})")
    run_objective_check(out_path)


def repeat_summary(round_paths: list[Path]) -> dict[str, list[bool]]:
    """Per case: whether every objective check (non-skip) passed in each round."""
    passed: dict[str, list[bool]] = {}
    for round_path in round_paths:
        checks_path = round_path.with_name(f"{round_path.stem}_checks.json")
        if not checks_path.exists():
            continue
        failed_cases: set[str] = set()
        seen_cases: set[str] = set()
        for item in json.loads(checks_path.read_text(encoding="utf-8"))["checks"]:
            case_id = canonical_case_id(str(item["case_id"]))
            seen_cases.add(case_id)
            if item["status"] == "fail":
                failed_cases.add(case_id)
        for case_id in sorted(seen_cases):
            passed.setdefault(case_id, []).append(case_id not in failed_cases)
    return passed


def print_repeat_summary(round_paths: list[Path]) -> None:
    summary = repeat_summary(round_paths)
    stable = sum(all(values) for values in summary.values())
    print(f"\n多轮次客观项汇总（{len(round_paths)} 轮，客观项全部通过的轮次 / 总轮次）:")
    for case_id, values in summary.items():
        marker = "" if all(values) else ("  <-- 不稳定" if any(values) else "  <-- 每轮都失败")
        print(f"  {case_id}: {sum(values)}/{len(values)}{marker}")
    print(f"每轮都通过的用例: {stable}/{len(summary)}；客观项只覆盖可自动判定的部分，仍需人工复核回答质量。")


if __name__ == "__main__":
    main()
