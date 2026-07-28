import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import yaml
from dotenv import load_dotenv

STAGE_DIR = Path(__file__).resolve().parent
PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.deps import build_deps
from agent.core import build_agent
from agent.core.logging_utils import TraceLogger, trace_event

CASES = yaml.safe_load((STAGE_DIR / "cases.yaml").read_text("utf-8"))

def fresh_root(root: Path) -> Path:
    """每条用例一个隔离 root：只拷只读输入，var/outputs/记忆全空。"""
    shutil.copytree(PROJECT / "resources" / "data", root / "resources" / "data")
    shutil.copytree(PROJECT / "resources" / "templates", root / "resources" / "templates")
    shutil.copytree(PROJECT / "agent" / "prompts", root / "agent" / "prompts")
    for d in ("outputs", "workspace", "logs"):
        (root / "var" / d).mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "PROJECT_NOTES.md").write_text("# Project Notes\n\n", "utf-8")
    return root


def preserve_artifacts(root: Path, case_id: str) -> Path:
    """保存人工判分需要的产物，然后允许临时 root 被安全清理。"""
    destination = STAGE_DIR / "artifacts" / case_id
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for name in ("outputs", "workspace", "logs"):
        source = root / "var" / name
        if source.exists():
            shutil.copytree(source, destination / name)
    notes = root / "docs" / "PROJECT_NOTES.md"
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


def main():
    load_dotenv(PROJECT / ".env")
    out_path = STAGE_DIR / "results.jsonl"
    pending_path = out_path.with_name(f"{out_path.name}.tmp")
    with pending_path.open("w", encoding="utf-8") as out:
        for case in CASES:
            trace = None
            rec = {
                "id": case["id"],
                "prompt": case["prompt"],
                "output": "",
                "tool_calls": [],
                "root": "",
                "trace": "",
                "run_id": None,
                "error": None,
            }
            with tempfile.TemporaryDirectory(prefix=f"eval-{case['id']}-") as temp_dir:
                root = Path(temp_dir)
                try:
                    fresh_root(root)
                    deps = build_deps(root)
                    trace = TraceLogger(deps.paths.logs)
                    deps.trace = trace
                    agent = build_agent(deps)
                    message_history = []
                    for seed in case.get("seed_prompts", []):
                        seed_result, _ = run_turn(seed, deps, agent, trace, message_history)
                        message_history = seed_result.all_messages()
                    if case.get("rebuild_after_seed"):
                        deps = build_deps(root)
                        deps.trace = trace
                        agent = build_agent(deps)
                        message_history = []
                    result, rec["run_id"] = run_turn(
                        case["prompt"], deps, agent, trace, message_history
                    )
                    rec["output"] = str(result.output)
                    rec["tool_calls"] = tool_seq(result.new_messages())
                except Exception as exc:
                    rec["error"] = repr(exc)
                try:
                    artifact_root = preserve_artifacts(root, case["id"])
                    rec["root"] = str(artifact_root)
                    if trace is not None:
                        rec["trace"] = str(artifact_root / "logs" / trace.path.name)
                except Exception as exc:
                    artifact_error = f"artifact preservation failed: {exc!r}"
                    rec["error"] = f"{rec['error']}; {artifact_error}" if rec["error"] else artifact_error
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{case['id']}: {len(rec['tool_calls'])} 次工具调用"
                  f"{' [报错]' if rec['error'] else ''}")
    pending_path.replace(out_path)
    print(f"\n→ {out_path}")

if __name__ == "__main__":
    main()
