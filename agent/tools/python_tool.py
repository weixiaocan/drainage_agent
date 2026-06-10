from __future__ import annotations

import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

from agent.deps import AgentDeps
from agent.types import ToolResult, error, ok


TIMEOUT_SECONDS = 60


def _workspace_files(deps: AgentDeps) -> list[str]:
    if not deps.paths.workspace.exists():
        return []
    return [
        p.resolve().relative_to(deps.paths.root).as_posix()
        for p in deps.paths.workspace.rglob("*")
        if p.is_file()
    ]


def run_python_impl(deps: AgentDeps, code: str) -> ToolResult:
    deps.paths.workspace.mkdir(parents=True, exist_ok=True)
    script_path = deps.paths.workspace / f"agent_run_{uuid.uuid4().hex}.py"
    prelude = f"""
from pathlib import Path
DATA_DIR = Path({str(deps.paths.data)!r})
OUTPUTS_DIR = Path({str(deps.paths.outputs)!r})
WORKSPACE_DIR = Path({str(deps.paths.workspace)!r})
"""
    script_path.write_text(textwrap.dedent(prelude) + "\n" + code, encoding="utf-8")

    try:
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=deps.paths.workspace,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        return error(
            f"run_python 超时（>{TIMEOUT_SECONDS}s）。",
            artifacts=_workspace_files(deps),
            stdout=(exc.stdout or "")[:4000],
            stderr=(exc.stderr or "")[:4000],
            script=str(script_path),
        )
    except Exception as exc:
        return error(f"run_python 启动失败: {exc}", artifacts=_workspace_files(deps), script=str(script_path))

    artifacts = _workspace_files(deps)
    payload = {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "script": str(script_path),
    }
    if completed.returncode == 0:
        return ok("run_python 执行成功。", artifacts=artifacts, **payload)
    return error(f"run_python 执行失败，returncode={completed.returncode}。", artifacts=artifacts, **payload)

