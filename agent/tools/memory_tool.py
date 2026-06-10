from __future__ import annotations

from datetime import datetime

from agent.deps import AgentDeps
from agent.types import ToolResult, ok


def record_note_impl(deps: AgentDeps, note: str) -> ToolResult:
    note = note.strip()
    if not note:
        return ok("没有写入空笔记。")
    deps.paths.notes.parent.mkdir(parents=True, exist_ok=True)
    with deps.paths.notes.open("a", encoding="utf-8") as f:
        f.write(f"\n- {datetime.now().date().isoformat()}: {note}\n")
    deps.project_notes = deps.paths.notes.read_text(encoding="utf-8")
    return ok(f"已写入项目记忆: {note}", artifacts=[deps.paths.notes.relative_to(deps.paths.root).as_posix()])

