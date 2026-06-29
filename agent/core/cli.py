from __future__ import annotations

import uuid

from agent.core import build_agent
from agent.deps import AgentDeps
from .logging_utils import TraceLogger, trace_event


EXIT_COMMANDS = {"exit", "quit", "q", "退出"}


def _result_text(result) -> str:
    for attr in ("output", "data"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            return value() if callable(value) else str(value)
    return str(result)


def run_cli(deps: AgentDeps) -> None:
    agent = build_agent(deps)
    trace = TraceLogger(deps.paths.logs)
    deps.trace = trace
    history = []
    print("排水监测数据分析 Agent 已启动。输入 `退出` 结束。")

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return

        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            print("已退出。")
            return

        run_id = uuid.uuid4().hex
        deps.session.current_run_id = run_id
        trace_event(trace, {"event": "turn_start", "run_id": run_id, "user": user_input})
        try:
            result = agent.run_sync(user_input, deps=deps, message_history=history)
            text = _result_text(result)
            print(f"\nAgent> {text}")
            if hasattr(result, "all_messages"):
                history = result.all_messages()
            trace_event(trace, {"event": "turn_end", "run_id": run_id, "reply": text})
        except Exception as exc:
            deps.logger.exception("Agent turn failed")
            msg = f"Agent 调用失败: {exc}"
            print(f"\nAgent> {msg}")
            trace_event(trace, {"event": "turn_error", "run_id": run_id, "error": msg})
        finally:
            deps.session.current_run_id = None
