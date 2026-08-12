from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_sandbox_image_has_dedicated_non_root_fixed_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile.sandbox").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-I", "/opt/sandbox/runner.py"]' in dockerfile
    assert "COPY . ." not in dockerfile
    assert "EXPOSE" not in dockerfile
    assert "Dockerfile" not in dockerfile.replace("Dockerfile.sandbox", "")


def test_sandbox_dependencies_exclude_application_and_model_packages() -> None:
    dependencies = (ROOT / "requirements-sandbox.txt").read_text(encoding="utf-8").lower()
    for forbidden in ("openai", "pydantic-ai", "fastapi", "uvicorn", "python-dotenv",
                      "python-docx", "pytest"):
        assert forbidden not in dependencies
    for required in ("pandas", "numpy", "scipy", "matplotlib", "openpyxl", "xlsxwriter"):
        assert required in dependencies


def test_sandbox_runner_only_targets_fixed_job_paths() -> None:
    runner = (ROOT / "sandbox_runtime" / "runner.py").read_text(encoding="utf-8")
    assert '/job/code/main.py' in runner
    assert '/job/input' in runner
    assert '/job/output' in runner
    assert "subprocess" not in runner
    assert "os.environ" not in runner
