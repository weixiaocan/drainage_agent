import argparse
import json
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

STAGE_DIR = Path(__file__).resolve().parent
PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.deps import build_deps
from agent.core import build_agent
from agent.logging_utils import TraceLogger, trace_event


CASE_ID_PREFIX_RE = re.compile(r"^(M\d{3}[A-Z]?)")


def canonical_case_id(case_id: str) -> str:
    match = CASE_ID_PREFIX_RE.match(str(case_id))
    return match.group(1) if match else str(case_id)


def cleanup_artifacts_for_case(case_id: str) -> Path:
    artifacts_dir = STAGE_DIR / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    canonical_id = canonical_case_id(case_id)
    for child in artifacts_dir.iterdir():
        if child.is_dir() and canonical_case_id(child.name) == canonical_id:
            shutil.rmtree(child)
    return artifacts_dir / canonical_id


def fresh_root(root: Path) -> Path:
    """每条用例一个隔离 root：只拷只读输入，outputs/记忆全空。"""
    shutil.copytree(PROJECT / "data", root / "data")
    shutil.copytree(PROJECT / "templates", root / "templates")
    shutil.copytree(PROJECT / "agent" / "prompts", root / "agent" / "prompts")
    for d in ("outputs", "workspace", "logs"):
        (root / d).mkdir()
    (root / "PROJECT_NOTES.md").write_text("# Project Notes\n\n", "utf-8")
    return root


def preserve_artifacts(root: Path, case_id: str) -> Path:
    """保存人工判分需要的产物，然后允许临时 root 被安全清理。"""
    destination = cleanup_artifacts_for_case(case_id)
    destination.mkdir(parents=True)
    for name in ("outputs", "workspace", "logs"):
        source = root / name
        if source.exists():
            shutil.copytree(source, destination / name)
    notes = root / "PROJECT_NOTES.md"
    if notes.exists():
        shutil.copy2(notes, destination / notes.name)
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
    if "turns" in case:
        turns = [
            {
                "prompt": t["prompt"],
                "expect": t.get("expect", ""),
                "key": bool(t.get("key", False)),
                "category": case.get("category", ""),
            }
            for t in case["turns"]
        ]
    else:
        turns = [{
            "prompt": case["prompt"],
            "expect": case.get("pass_when", ""),
            "key": True,
            "category": case.get("category", ""),
        }]
    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "turns": turns,
        "seed_prompts": case.get("seed_prompts", []),
        "rebuild_after_seed": bool(case.get("rebuild_after_seed", False)),
    }


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
    for line in pending_path.read_text(encoding="utf-8").splitlines():
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
    for line in pending_path.read_text(encoding="utf-8").splitlines():
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


def run_case(case: dict) -> dict:
    """跑一条（已归一化的）用例：在同一个隔离 root、同一条 message_history 上逐轮推进。"""
    rec = {
        "id": case["id"],
        "category": case["category"],
        "turns": [],
        "root": "",
        "trace": "",
        "error": None,
    }
    trace = None
    with tempfile.TemporaryDirectory(prefix=f"eval-{case['id']}-") as temp_dir:
        root = Path(temp_dir)
        try:
            fresh_root(root)
            deps = build_deps(root)
            trace = TraceLogger(deps.paths.logs)
            deps.trace = trace
            agent = build_agent(deps)

            message_history = []
            # 前置（仅有状态的用例需要）
            for seed in case["seed_prompts"]:
                seed_result, _ = run_turn(seed, deps, agent, trace, message_history)
                message_history = seed_result.all_messages()
            if case["rebuild_after_seed"]:
                deps = build_deps(root)
                deps.trace = trace
                agent = build_agent(deps)
                message_history = []

            # 逐轮推进：每轮把全量历史接力给下一轮
            for i, turn in enumerate(case["turns"]):
                result, run_id = run_turn(turn["prompt"], deps, agent, trace, message_history)
                rec["turns"].append({
                    "n": i + 1,
                    "run_id": run_id,
                    "prompt": turn["prompt"],
                    "expect": turn["expect"],
                    "key": turn["key"],
                    "output": str(result.output),
                    "tool_calls": tool_seq(result.new_messages()),
                    "usage": try_usage(result),
                })
                message_history = result.all_messages()
        except Exception as exc:
            rec["error"] = repr(exc)

        # 无论成功失败都尽量保全已产生的产物
        try:
            artifact_root = preserve_artifacts(root, case["id"])
            rec["root"] = str(artifact_root)
            if trace is not None:
                rec["trace"] = str(artifact_root / "logs" / trace.path.name)
        except Exception as exc:
            err = f"artifact preservation failed: {exc!r}"
            rec["error"] = f"{rec['error']}; {err}" if rec["error"] else err
    return rec


def main():
    ap = argparse.ArgumentParser(description="排水 agent eval runner（单轮/多轮）")
    ap.add_argument("cases_file", nargs="?", default=str(STAGE_DIR / "cases_multiturn.yaml"),
                    help="用例文件，默认 eval/eval_stage2/cases_multiturn.yaml")
    ap.add_argument("-o", "--out", default=None, help="输出 jsonl，默认 eval/eval_stage2/results.jsonl")
    ap.add_argument("--resume", action="store_true", help="从已有 .tmp 结果断点续跑，跳过已完整写入的 case")
    args = ap.parse_args()

    load_dotenv(PROJECT / ".env")

    cases_path = (PROJECT / args.cases_file) if not Path(args.cases_file).is_absolute() else Path(args.cases_file)
    raw_cases = yaml.safe_load(cases_path.read_text("utf-8"))
    cases = [normalize_case(c) for c in raw_cases]

    out_path = Path(args.out) if args.out else (STAGE_DIR / "results.jsonl")
    if not out_path.is_absolute():
        out_path = PROJECT / out_path
    pending_path = out_path.with_name(f"{out_path.name}.tmp")

    # 可复现元数据：尽力从一个临时 deps 读出 model/参数
    meta = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": str(cases_path.relative_to(PROJECT)) if cases_path.is_relative_to(PROJECT) else str(cases_path),
        "case_count": len(cases),
        "model": None,
        "model_settings": None,
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

    completed = completed_case_ids(pending_path) if args.resume else set()
    mode = "a" if args.resume and pending_path.exists() else "w"
    with pending_path.open(mode, encoding="utf-8") as out:
        if mode == "w":
            # 第一行写元数据，便于日后复现与对比
            out.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
            out.flush()
        for case in cases:
            if case["id"] in completed:
                print(f"{case['id']}: 已完成，跳过")
                continue
            rec = run_case(case)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n_tools = sum(len(t["tool_calls"]) for t in rec["turns"])
            print(f"{rec['id']}: {len(rec['turns'])} 轮 / {n_tools} 次工具调用"
                  f"{' [报错]' if rec['error'] else ''}")
    compact_pending_results(pending_path)
    pending_path.replace(out_path)
    print(f"\n→ {out_path}  (model={meta['model']})")


if __name__ == "__main__":
    main()
