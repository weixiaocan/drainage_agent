from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.conversations import ConversationRepository, ConversationRunner
from agent.deps import (
    AgentDeps,
    AgentSettings,
    Paths,
    SessionState,
    ensure_directories,
)
from agent.run_records import RunRecorder


class Result:
    output = "ok"

    def __init__(self, history: list[Any], message: str) -> None:
        self._history = [*history, {"message": message}]

    def all_messages(self) -> list[Any]:
        return self._history


class StatefulAgent:
    def run_sync(
        self,
        message: str,
        *,
        deps: AgentDeps,
        message_history: list[Any],
    ) -> Result:
        if message == "选择场次":
            deps.session.selected_event_ids = [2, 4]
        return Result(message_history, message)


def make_deps(root: Path) -> AgentDeps:
    paths = Paths.from_root(root)
    ensure_directories(paths)
    return AgentDeps(
        paths=paths,
        settings=AgentSettings(model="test", base_url=None, api_key=None),
        logger=logging.getLogger("test.conversations"),
        session=SessionState(),
    )


def test_conversation_persists_history_and_deterministic_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "var" / "drainage.sqlite3"
    repository = ConversationRepository(database)
    runner = ConversationRunner(
        repository,
        StatefulAgent(),
        make_deps(tmp_path),
        tmp_path / "var" / "projects",
        RunRecorder(database),
    )

    first = runner.run(
        project_id="project-a",
        batch_id="batch-a",
        message="选择场次",
    )
    runner.run(
        project_id="project-a",
        batch_id="batch-a",
        session_id=first.session_id,
        message="继续",
    )
    history, state = repository.load(
        first.session_id, "project-a", "batch-a"
    )

    assert len(history) == 2
    assert state.selected_event_ids == [2, 4]


def test_scoped_dependencies_use_only_current_batch_workspace(
    tmp_path: Path,
) -> None:
    database = tmp_path / "var" / "drainage.sqlite3"
    seen: dict[str, Path | str | None] = {}

    class InspectingAgent:
        def run_sync(
            self,
            message: str,
            *,
            deps: AgentDeps,
            message_history: list[Any],
        ) -> Result:
            seen["root"] = deps.paths.root
            seen["project_id"] = deps.current_project_id
            seen["batch_id"] = deps.current_batch_id
            return Result(message_history, message)

    runner = ConversationRunner(
        ConversationRepository(database),
        InspectingAgent(),
        make_deps(tmp_path),
        tmp_path / "var" / "projects",
        RunRecorder(database),
    )
    runner.run(
        project_id="project-a",
        batch_id="batch-a",
        message="检查",
    )

    assert seen["root"] == (
        tmp_path / "var" / "projects" / "project-a" / "batches" / "batch-a"
    ).resolve()
    assert seen["project_id"] == "project-a"
    assert seen["batch_id"] == "batch-a"
