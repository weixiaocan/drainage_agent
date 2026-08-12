import logging

from agent.conversations import ConversationRepository, ConversationRunner
from agent.deps import build_deps
from agent.docker_python_sandbox import DockerPythonSandbox
from agent.run_records import RunRecorder


def test_build_deps_always_configures_execution_request_repository(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DRAINAGE_SANDBOX_CONTROLLER_URL", raising=False)
    monkeypatch.delenv("DRAINAGE_SANDBOX_CONTROLLER_TOKEN", raising=False)
    monkeypatch.delenv("DRAINAGE_SANDBOX_IMAGE_DIGEST", raising=False)
    deps = build_deps(tmp_path)
    assert deps.python_execution_requests.database == tmp_path / "var" / "drainage.sqlite3"
    assert deps.python_sandbox is None
    assert deps.sandbox_jobs_root == (tmp_path / "var" / "sandbox-jobs").resolve()


def test_build_deps_requires_complete_sandbox_configuration(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("DRAINAGE_SANDBOX_CONTROLLER_URL", "http://sandbox-controller:8080")
    monkeypatch.delenv("DRAINAGE_SANDBOX_IMAGE_DIGEST", raising=False)
    with caplog.at_level(logging.WARNING):
        deps = build_deps(tmp_path)
    assert deps.python_sandbox is None
    assert "all be configured" in caplog.text


def test_build_deps_wires_controller_adapter_without_docker_authority(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DRAINAGE_SANDBOX_CONTROLLER_URL", "http://sandbox-controller:8080")
    monkeypatch.setenv("DRAINAGE_SANDBOX_CONTROLLER_TOKEN", "a" * 32)
    monkeypatch.setenv("DRAINAGE_SANDBOX_IMAGE_DIGEST", "sha256:abc")
    deps = build_deps(tmp_path)
    assert isinstance(deps.python_sandbox, DockerPythonSandbox)
    assert deps.python_sandbox.image_digest == "sha256:abc"


def test_scoped_conversation_dependencies_inherit_security_services(tmp_path) -> None:
    base = build_deps(tmp_path)
    seen = {}

    class Agent:
        def run_sync(self, message, *, deps, message_history):
            seen["requests"] = deps.python_execution_requests
            seen["sandbox"] = deps.python_sandbox

            class Result:
                output = "ok"

                @staticmethod
                def all_messages():
                    return []

            return Result()

    database = tmp_path / "var" / "drainage.sqlite3"
    runner = ConversationRunner(
        ConversationRepository(database), Agent(), base,
        tmp_path / "var" / "projects", RunRecorder(database),
    )
    runner.run(project_id="p", batch_id="b", message="test")
    assert seen["requests"] is base.python_execution_requests
    assert seen["sandbox"] is base.python_sandbox
