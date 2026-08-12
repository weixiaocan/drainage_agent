import pytest

from agent.python_execution_policy import PythonExecutionPolicy


@pytest.fixture
def policy() -> PythonExecutionPolicy:
    return PythonExecutionPolicy()


def decide(policy, code, **kwargs):
    return policy.evaluate(code=code, inputs=kwargs.pop("inputs", ["confirmed_flow"]),
                           outputs=kwargs.pop("outputs", ["summary.csv"]), **kwargs)


def test_safe_statistics_are_allowed(policy) -> None:
    decision = decide(policy, "import pandas as pd\nresult = pd.DataFrame({'x': [1]}).mean()")
    assert decision.action == "allow"
    assert "import:pandas" in decision.capabilities
    assert decision.affected_paths == ("summary.csv",)


def test_overwrite_and_elevated_resources_require_approval(policy) -> None:
    overwrite = decide(policy, "x = sum([1, 2])", overwrite=True)
    elevated = decide(policy, "x = 1", resource_profile="large")
    assert overwrite.action == elevated.action == "ask"
    assert "overwrite_outputs" in overwrite.capabilities
    assert "resource_profile:large" in elevated.capabilities


@pytest.mark.parametrize("code,reason", [
    ("import os", "dangerous_import:os"),
    ("from subprocess import run", "dangerous_import:subprocess"),
    ("import socket", "dangerous_import:socket"),
    ("import importlib", "dangerous_import:importlib"),
    ("eval('1 + 1')", "dangerous_call:eval"),
    ("__import__('math')", "dangerous_call:__import__"),
    ("x.__class__.__mro__", "dangerous_attribute:__class__"),
    ("open('/app/.env').read()", "absolute_path_literal"),
    ("name = '../other-project/data.csv'", "path_traversal_literal"),
    ("import made_up_package", "unapproved_import:made_up_package"),
])
def test_dangerous_code_is_denied(policy, code, reason) -> None:
    decision = decide(policy, code)
    assert decision.action == "deny"
    assert reason in decision.reasons


@pytest.mark.parametrize("inputs,outputs", [
    (["C:\\secret.csv"], ["out.csv"]),
    (["../other"], ["out.csv"]),
    (["confirmed_flow"], ["../out.csv"]),
    (["confirmed_flow"], ["script.py"]),
])
def test_path_like_or_executable_contract_names_are_denied(policy, inputs, outputs) -> None:
    assert decide(policy, "x = 1", inputs=inputs, outputs=outputs).action == "deny"


def test_non_default_but_valid_input_requires_approval(policy) -> None:
    decision = decide(policy, "x = 1", inputs=["project_attachment_1"])
    assert decision.action == "ask"
    assert "read_input:project_attachment_1" in decision.capabilities


def test_declaration_cannot_hide_dangerous_code(policy) -> None:
    decision = decide(policy, "import os\nos.environ['TOKEN']")
    assert decision.action == "deny"
    assert "dangerous_import:os" in decision.reasons
