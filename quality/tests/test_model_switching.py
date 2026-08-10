from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent.conversations import ConversationRepository, ConversationRunner
from agent.core import build_agent
from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories
from agent.run_records import RunRecorder
from web.app import create_app


class Result:
    def __init__(self, output: str, history: list[Any], message: str) -> None:
        self.output = output
        self._history = [*history, {"message": message}]

    def all_messages(self) -> list[Any]:
        return self._history


class NamedAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.history_lengths: list[int] = []

    def run_sync(
        self,
        message: str,
        *,
        deps: AgentDeps,
        message_history: list[Any],
    ) -> Result:
        self.history_lengths.append(len(message_history))
        return Result(f"{self.name}: {message}", message_history, message)


def make_deps(root: Path) -> AgentDeps:
    paths = Paths.from_root(root)
    ensure_directories(paths)
    return AgentDeps(
        paths=paths,
        settings=AgentSettings(model="deepseek-test", base_url=None, api_key=None),
        logger=logging.getLogger("test.model-switching"),
        session=SessionState(),
    )


def test_build_agent_uses_current_openai_chat_model_without_deprecation_warning(
    tmp_path: Path,
) -> None:
    import warnings

    deps = make_deps(tmp_path)
    deps.settings = AgentSettings(
        model="deepseek-test",
        base_url="https://api.example.test/v1",
        api_key="test-key-not-used",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        build_agent(deps)


def test_conversation_can_switch_model_without_losing_history(tmp_path: Path) -> None:
    deps = make_deps(tmp_path)
    deepseek = NamedAgent("deepseek")
    glm = NamedAgent("glm")
    glm_settings = AgentSettings(
        model="glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="secret",
        provider_id="glm",
        display_name="GLM-5.2",
    )
    database = tmp_path / "var" / "drainage.sqlite3"
    runner = ConversationRunner(
        ConversationRepository(database),
        deepseek,
        deps,
        tmp_path / "var" / "projects",
        RunRecorder(database),
        model_agents={"deepseek": (deepseek, deps.settings), "glm": (glm, glm_settings)},
    )

    first = runner.run(project_id="p", batch_id="b", message="one")
    second = runner.run(
        project_id="p",
        batch_id="b",
        session_id=first.session_id,
        message="two",
        model_id="glm",
    )

    assert second.reply == "glm: two"
    assert glm.history_lengths == [1]


def test_web_lists_configured_glm_and_accepts_model_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GLM_API_KEY", "not-returned-to-browser")
    agents: dict[str, NamedAgent] = {}

    def factory(deps: AgentDeps) -> NamedAgent:
        agent = NamedAgent(deps.settings.model)
        agents[deps.settings.model] = agent
        return agent

    client = TestClient(create_app(tmp_path, deps_factory=make_deps, agent_factory=factory))
    models = client.get("/api/models")

    assert models.status_code == 200
    assert {item["id"] for item in models.json()["models"]} == {"deepseek", "glm"}
    assert "not-returned-to-browser" not in models.text

    project = client.post("/api/projects", json={"name": "switch-test"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches", json={"name": "workspace"}
    ).json()
    response = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "project_id": project["id"],
            "batch_id": batch["id"],
            "model_id": "glm",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "glm-5.2: hello"


def test_index_contains_model_selector_and_sends_selected_model(tmp_path: Path) -> None:
    client = TestClient(
        create_app(tmp_path, deps_factory=make_deps, agent_factory=lambda deps: NamedAgent(deps.settings.model))
    )
    html = client.get("/").text

    assert 'id="chatModel"' in html
    assert 'fetch("/api/models")' in html
    assert "model_id: chatModel.value || null" in html
