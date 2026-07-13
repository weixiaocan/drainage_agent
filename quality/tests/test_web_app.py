from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")
TestClient = testclient.TestClient

from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories
from web.app import create_app


class FakeResult:
    def __init__(self, output: str, messages: list[Any]):
        self.output = output
        self._messages = messages

    def all_messages(self) -> list[Any]:
        return self._messages


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_sync(self, message: str, *, deps: AgentDeps, message_history: list[Any]) -> FakeResult:
        self.calls.append({"message": message, "history_len": len(message_history)})
        return FakeResult(f"reply: {message}", [*message_history, {"user": message}])


def make_deps(root: Path) -> AgentDeps:
    paths = Paths.from_root(root)
    ensure_directories(paths)
    return AgentDeps(
        paths=paths,
        settings=AgentSettings(model="test", base_url=None, api_key=None),
        logger=logging.getLogger("test.web"),
        session=SessionState(),
        project_notes="",
    )


@pytest.fixture()
def fake_agent() -> FakeAgent:
    return FakeAgent()


@pytest.fixture()
def client(tmp_path: Path, fake_agent: FakeAgent) -> TestClient:
    app = create_app(
        tmp_path,
        deps_factory=make_deps,
        agent_factory=lambda _deps: fake_agent,
    )
    return TestClient(app)


def test_chat_maintains_session_history(client: TestClient, fake_agent: FakeAgent) -> None:
    first = client.post("/api/chat", json={"message": "描述当前数据"})
    assert first.status_code == 200
    session_id = first.json()["session_id"]
    assert first.json()["reply"] == "reply: 描述当前数据"

    second = client.post("/api/chat", json={"message": "列出已有结果", "session_id": session_id})
    assert second.status_code == 200
    assert second.json()["session_id"] == session_id
    assert fake_agent.calls[0]["history_len"] == 0
    assert fake_agent.calls[1]["history_len"] == 1


def test_index_returns_utf8_html_with_expected_copy(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert "快捷指令" in response.text


def test_index_renders_agent_markdown(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "function renderMarkdown(text)" in response.text
    assert 'role === "agent"' in response.text
    assert "div.appendChild(renderMarkdown(text));" in response.text


def test_index_sends_message_on_enter(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'message.addEventListener("keydown"' in response.text
    assert 'event.key === "Enter" && !event.shiftKey' in response.text
    assert "event.preventDefault();" in response.text
    assert "send(message.value);" in response.text


def test_index_persists_chat_transcript_across_refresh(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "function restoreTranscript()" in response.text
    assert "`drainage-agent-chat-${sessionId}`" in response.text
    assert '"drainage-agent-chat-latest"' in response.text
    assert "parseStoredTranscript" in response.text
    assert "saveTranscript(transcript);" in response.text
    assert "restoreTranscript();" in response.text


def test_chat_rejects_empty_message(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 400


def test_upload_writes_expected_files_and_clears_manifest(client: TestClient, tmp_path: Path) -> None:
    manifest = tmp_path / "var" / "outputs" / "manifest.json"
    manifest.write_text('{"version": 1, "results": {"old": {}}}', encoding="utf-8")

    response = client.post(
        "/api/upload",
        files=[
            ("flow_files", ("100_W1.csv", b"timestamp,flow\n2026-01-01,1\n", "text/csv")),
            ("flow_files", ("101_W2.csv", b"timestamp,flow\n2026-01-01,2\n", "text/csv")),
            ("rainfall_file", ("rain.csv", b"timestamp,rain\n2026-01-01,0\n", "text/csv")),
            ("site_info_file", ("site.xlsx", b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("template_file", ("template.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ],
    )

    assert response.status_code == 200
    assert (tmp_path / "resources" / "data" / "flow" / "100_W1.csv").exists()
    assert (tmp_path / "resources" / "data" / "flow" / "101_W2.csv").exists()
    assert (tmp_path / "resources" / "data" / "降雨数据.csv").exists()
    assert (tmp_path / "resources" / "data" / "点位信息.xlsx").exists()
    assert (tmp_path / "resources" / "templates" / "template.docx").exists()
    assert '"results": {}' in manifest.read_text(encoding="utf-8")


def test_upload_rejects_bad_extension(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files=[("flow_files", ("bad.txt", b"bad", "text/plain"))],
    )
    assert response.status_code == 400
    assert "文件类型不支持" in response.json()["detail"]


def test_upload_rejects_path_traversal_filename(client: TestClient) -> None:
    response = client.post(
        "/api/upload",
        files=[("flow_files", ("..\\bad.csv", b"bad", "text/csv"))],
    )
    assert response.status_code == 400
    assert "非法文件名" in response.json()["detail"]


def test_results_and_file_download(client: TestClient, tmp_path: Path) -> None:
    output = tmp_path / "var" / "outputs" / "result.txt"
    output.write_text("ok", encoding="utf-8")
    workspace = tmp_path / "var" / "workspace" / "scratch.txt"
    workspace.write_text("scratch", encoding="utf-8")

    results = client.get("/api/results")
    assert results.status_code == 200
    paths = {item["path"] for group in results.json().values() for item in group}
    assert "var/outputs/result.txt" in paths
    assert "var/workspace/scratch.txt" in paths

    download = client.get("/files/var/outputs/result.txt")
    assert download.status_code == 200
    assert download.content == b"ok"

    workspace_download = client.get("/files/var/workspace/scratch.txt")
    assert workspace_download.status_code == 200
    assert workspace_download.content == b"scratch"


def test_file_download_returns_404_for_missing_allowed_file(client: TestClient) -> None:
    response = client.get("/files/var/outputs/missing.txt")

    assert response.status_code == 404


def test_file_download_rejects_data_files(client: TestClient, tmp_path: Path) -> None:
    data_file = tmp_path / "resources" / "data" / "secret.csv"
    data_file.write_text("secret", encoding="utf-8")
    response = client.get("/files/resources/data/secret.csv")
    assert response.status_code == 403


def test_file_download_supports_chinese_artifact_name(client: TestClient, tmp_path: Path) -> None:
    artifact = tmp_path / "var" / "outputs" / "综合分析结果.xlsx"
    artifact.write_bytes(b"xlsx")

    response = client.get("/files/var/outputs/综合分析结果.xlsx")

    assert response.status_code == 200
    assert response.content == b"xlsx"

