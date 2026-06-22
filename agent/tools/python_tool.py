from __future__ import annotations

import os
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
    package_root = Path(__file__).resolve().parents[2]
    prelude = f"""
from pathlib import Path
import sys

PROJECT_ROOT = Path({str(deps.paths.root)!r})
PACKAGE_ROOT = Path({str(package_root)!r})
DATA_DIR = Path({str(deps.paths.data)!r})
OUTPUTS_DIR = Path({str(deps.paths.outputs)!r})
WORKSPACE_DIR = Path({str(deps.paths.workspace)!r})
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
from analysis.io import load_filtered_flow, load_flow, load_rain, load_sites
"""
    script_path.write_text(textwrap.dedent(prelude) + "\n" + code, encoding="utf-8")
    env = os.environ.copy()
    env["DRAINAGE_AGENT_ROOT"] = str(deps.paths.root)

    try:
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=deps.paths.workspace,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return error(
            f"run_python 超时（{TIMEOUT_SECONDS}s）。",
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
