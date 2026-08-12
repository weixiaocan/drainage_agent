from quality.eval.run_python_security_eval import run


def test_python_security_eval_is_fully_automatic() -> None:
    results = run()

    assert len(results) == 12
    assert all(result["passed"] for result in results), results
