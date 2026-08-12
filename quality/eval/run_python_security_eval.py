from __future__ import annotations

import argparse
import gc
import json
import tempfile
from pathlib import Path

from agent.python_execution_policy import PythonExecutionPolicy
from agent.python_execution_requests import InvalidExecutionTransition, PythonExecutionRequestRepository


DEFAULT_CASES = Path(__file__).with_name("python_security_cases.json")


def run(cases_path: Path = DEFAULT_CASES) -> list[dict[str, object]]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = [_policy_result(case) for case in cases["policy_cases"]]
    with tempfile.TemporaryDirectory(prefix="python-security-eval-") as temp:
        for case in cases["approval_cases"]:
            results.append(_approval_result(case, Path(temp) / f"{case['id']}.sqlite3"))
    return results


def _policy_result(case: dict[str, object]) -> dict[str, object]:
    decision = PythonExecutionPolicy().evaluate(
        code=str(case["code"]), inputs=list(case["inputs"]), outputs=list(case["outputs"]),
        overwrite=bool(case["overwrite"]),
    )
    expected_reasons = set(case.get("expected_reasons", []))
    passed = decision.action == case["expected_action"] and expected_reasons <= set(decision.reasons)
    return {"id": case["id"], "passed": passed, "actual": decision.action,
            "reasons": list(decision.reasons)}


def _approval_result(case: dict[str, object], database: Path) -> dict[str, object]:
    repository = PythonExecutionRequestRepository(database)
    code = "result = 1"
    request = repository.create(
        project_id="project-a", batch_id="batch-a", session_id="session-a", run_id="run-a",
        purpose="security eval", code=code, policy_decision="ask",
        requested_capabilities=["overwrite_outputs"],
    )
    binding = {
        "project_id": "project-a", "batch_id": "batch-a", "session_id": "session-a",
        "code_sha256": repository.hash_code(code),
    }
    mismatch = str(case["mismatch"])
    try:
        if mismatch == "single_use":
            repository.approve(request.request_id, **binding, approved_capabilities=["overwrite_outputs"])
            repository.start(request.request_id, **binding, input_snapshot_id="snapshot-a",
                             sandbox_image_digest="sha256:image")
            repository.start(request.request_id, **binding, input_snapshot_id="snapshot-b",
                             sandbox_image_digest="sha256:image")
        else:
            changed = dict(binding)
            changed[mismatch] = "different" if mismatch != "code_sha256" else repository.hash_code("changed")
            repository.approve(request.request_id, **changed, approved_capabilities=["overwrite_outputs"])
    except InvalidExecutionTransition:
        result = {"id": case["id"], "passed": True, "actual": "rejected"}
    else:
        result = {"id": case["id"], "passed": False, "actual": "accepted"}
    del repository
    gc.collect()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic run_python security eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = run(args.cases)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}")
    failed = [item for item in results if not item["passed"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
