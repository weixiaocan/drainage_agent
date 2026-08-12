import logging

from agent.core import _PythonApprovalAgent
from agent.deps import AgentDeps, AgentSettings, Paths, SessionState
from agent.types import PythonApprovalRequired


class ApprovalInner:
    def __init__(self) -> None:
        self.calls = 0

    def run_sync(self, message, *, deps, message_history):
        self.calls += 1
        raise PythonApprovalRequired({
            "status": "needs_approval",
            "summary": "需要审批",
            "artifacts": [],
            "data": {"request_id": "request-1", "code_sha256": "abc123"},
        })


def deps(tmp_path):
    return AgentDeps(
        paths=Paths.from_root(tmp_path),
        settings=AgentSettings(model="test", base_url=None, api_key=None),
        logger=logging.getLogger("test.approval"),
        session=SessionState(),
    )


def test_approval_exception_terminates_turn_without_reentering_inner_agent(tmp_path) -> None:
    inner = ApprovalInner()
    agent = _PythonApprovalAgent(inner)
    result = agent.run_sync("执行 Python", deps=deps(tmp_path), message_history=[])
    assert inner.calls == 1
    assert "等待用户单次审批" in result.output
    assert "request-1" in result.output
    assert "abc123" in result.output
    assert len(result.all_messages()) == 2


def test_normal_result_passes_through_unchanged(tmp_path) -> None:
    expected = object()

    class NormalInner:
        def run_sync(self, message, *, deps, message_history):
            return expected

    assert _PythonApprovalAgent(NormalInner()).run_sync(
        "普通请求", deps=deps(tmp_path), message_history=[]
    ) is expected
