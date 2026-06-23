import argparse
import json
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
    destination = STAGE_DIR / "artifacts" / case_id
    if destination.exists():
        shutil.rmtree(destination)
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
        u = result.usage()
        return {
            "requests": getattr(u, "requests", None),
            "request_tokens": getattr(u, "request_tokens", None),
            "response_tokens": getattr(u, "response_tokens", None),
            "total_tokens": getattr(u, "total_tokens", None),
        }
    except Exception:
        return None


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

    with pending_path.open("w", encoding="utf-8") as out:
        # 第一行写元数据，便于日后复现与对比
        out.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
        out.flush()
        for case in cases:
            rec = run_case(case)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n_tools = sum(len(t["tool_calls"]) for t in rec["turns"])
            print(f"{rec['id']}: {len(rec['turns'])} 轮 / {n_tools} 次工具调用"
                  f"{' [报错]' if rec['error'] else ''}")
    pending_path.replace(out_path)
    print(f"\n→ {out_path}  (model={meta['model']})")


if __name__ == "__main__":
    main()
