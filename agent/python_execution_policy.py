from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal


Action = Literal["allow", "ask", "deny"]

ALLOWED_IMPORTS = {
    "collections", "datetime", "decimal", "functools", "itertools", "json",
    "math", "matplotlib", "numpy", "openpyxl", "pandas", "scipy", "statistics",
    "xlsxwriter",
}
DENIED_IMPORTS = {
    "asyncio", "builtins", "ctypes", "http", "importlib", "inspect", "multiprocessing",
    "os", "pathlib", "pickle", "requests", "shutil", "signal", "socket", "subprocess",
    "sys", "tempfile", "threading", "urllib",
}
DENIED_CALLS = {
    "__import__", "breakpoint", "compile", "eval", "exec", "globals", "input",
    "locals", "open", "vars",
}
DENIED_ATTRIBUTES = {
    "__bases__", "__builtins__", "__class__", "__code__", "__dict__", "__globals__",
    "__mro__", "__subclasses__", "environ", "getenv", "popen", "system",
}
ALLOWED_INPUTS = {"confirmed_flow", "rainfall", "site_info", "current_results"}
ALLOWED_OUTPUT_EXTENSIONS = {".csv", ".json", ".png", ".xlsx"}
LOGICAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class PolicyDecision:
    action: Action
    reasons: tuple[str, ...]
    capabilities: tuple[str, ...]
    affected_paths: tuple[str, ...]


class PythonExecutionPolicy:
    """Classify declared and AST-observed capabilities; not a sandbox boundary."""

    def evaluate(
        self,
        *,
        code: str,
        inputs: list[str] | tuple[str, ...],
        outputs: list[str] | tuple[str, ...],
        overwrite: bool = False,
        resource_profile: str = "default",
    ) -> PolicyDecision:
        reasons: set[str] = set()
        capabilities: set[str] = set()
        affected_paths: set[str] = set()
        deny = self._validate_contract(inputs, outputs, reasons, affected_paths)

        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError) as exc:
            return PolicyDecision("deny", (f"invalid_python:{type(exc).__name__}",), (), ())

        visitor = _CapabilityVisitor()
        visitor.visit(tree)
        reasons.update(visitor.reasons)
        capabilities.update(visitor.capabilities)
        deny = deny or bool(visitor.reasons)

        if deny:
            return self._decision("deny", reasons, capabilities, affected_paths)
        if overwrite:
            reasons.add("overwrite_requires_approval")
            capabilities.add("overwrite_outputs")
        if resource_profile != "default":
            reasons.add("elevated_resources_require_approval")
            capabilities.add(f"resource_profile:{resource_profile}")
        non_default_inputs = set(inputs) - ALLOWED_INPUTS
        if non_default_inputs:
            reasons.add("non_default_input_requires_approval")
            capabilities.update(f"read_input:{name}" for name in non_default_inputs)
        if reasons:
            return self._decision("ask", reasons, capabilities, affected_paths)
        capabilities.update(f"read_input:{name}" for name in inputs)
        capabilities.update(f"create_output:{name}" for name in outputs)
        return self._decision("allow", {"safe_analysis"}, capabilities, affected_paths)

    @staticmethod
    def _validate_contract(inputs, outputs, reasons: set[str], affected_paths: set[str]) -> bool:
        denied = False
        for name in inputs:
            if not _safe_logical_name(name):
                reasons.add("invalid_or_path_like_input")
                denied = True
        for name in outputs:
            affected_paths.add(name)
            if not _safe_logical_name(name):
                reasons.add("invalid_or_path_like_output")
                denied = True
                continue
            if PurePosixPath(name).suffix.lower() not in ALLOWED_OUTPUT_EXTENSIONS:
                reasons.add("output_extension_not_allowed")
                denied = True
        return denied

    @staticmethod
    def _decision(action, reasons, capabilities, affected_paths) -> PolicyDecision:
        return PolicyDecision(action, tuple(sorted(reasons)), tuple(sorted(capabilities)),
                              tuple(sorted(affected_paths)))


def _safe_logical_name(value: object) -> bool:
    if not isinstance(value, str) or not LOGICAL_NAME.fullmatch(value):
        return False
    if value in {".", ".."} or ".." in PurePosixPath(value).parts:
        return False
    return not PurePosixPath(value).is_absolute() and not PureWindowsPath(value).is_absolute()


class _CapabilityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.reasons: set[str] = set()
        self.capabilities: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._import(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.reasons.add("relative_import_denied")
        self._import(node.module or "")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        leaf = name.rsplit(".", 1)[-1]
        if leaf in DENIED_CALLS:
            self.reasons.add(f"dangerous_call:{leaf}")
        if leaf in {"connect", "urlopen", "listen", "bind"}:
            self.reasons.add(f"network_capability:{leaf}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in DENIED_ATTRIBUTES or node.attr.startswith("__"):
            self.reasons.add(f"dangerous_attribute:{node.attr}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            value = node.value.strip()
            lowered = value.lower().replace("\\", "/")
            if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
                self.reasons.add("absolute_path_literal")
            if ".." in PurePosixPath(lowered).parts:
                self.reasons.add("path_traversal_literal")
            if ".env" in PurePosixPath(lowered).parts or "docker.sock" in lowered:
                self.reasons.add("sensitive_path_literal")

    def _import(self, name: str) -> None:
        root = name.split(".", 1)[0]
        if root in ALLOWED_IMPORTS:
            self.capabilities.add(f"import:{root}")
        elif root in DENIED_IMPORTS:
            self.reasons.add(f"dangerous_import:{root}")
        else:
            self.reasons.add(f"unapproved_import:{root or 'unknown'}")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
