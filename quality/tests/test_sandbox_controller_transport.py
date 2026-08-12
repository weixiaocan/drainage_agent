from fastapi.testclient import TestClient

from sandbox_controller.app import create_controller_app
from sandbox_controller.controller import SandboxController


TOKEN = "a" * 32


class Runtime:
    def submit(self, **kwargs): pass
    def inspect(self, name): return "running", 0
    def cancel(self, name): pass
    def remove(self, name): pass
    def logs(self, name): return "", ""
    def collect_output(self, name, output_root): pass
    def managed_containers(self): return []


def client(tmp_path):
    job = tmp_path / "jobs" / "job-1"
    (job / "code").mkdir(parents=True)
    (job / "input").mkdir()
    (job / "output").mkdir()
    (job / "code" / "main.py").write_text("print(1)", encoding="utf-8")
    controller = SandboxController(tmp_path / "jobs", tmp_path / "state.json", Runtime())
    return TestClient(create_controller_app(controller, TOKEN))


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_transport_requires_valid_bearer_token(tmp_path) -> None:
    api = client(tmp_path)
    assert api.post("/v1/jobs/submit", json={"job_id": "job-1"}).status_code == 401
    assert api.post("/v1/jobs/submit", json={"job_id": "job-1"},
                    headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_transport_exposes_only_fixed_job_operations(tmp_path) -> None:
    api = client(tmp_path)
    submitted = api.post("/v1/jobs/submit", json={"job_id": "job-1"}, headers=auth())
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "running"
    assert api.get("/v1/jobs/job-1", headers=auth()).json()["status"] == "running"
    assert api.post("/v1/jobs/cancel", json={"job_id": "job-1"}, headers=auth()).json()["status"] == "cancelled"


def test_transport_rejects_extra_docker_or_path_fields(tmp_path) -> None:
    api = client(tmp_path)
    response = api.post("/v1/jobs/submit", json={
        "job_id": "job-1", "image": "evil", "host_path": "C:/", "privileged": True,
    }, headers=auth())
    assert response.status_code == 422
    assert api.get("/v1/jobs/../escape", headers=auth()).status_code in {404, 422}


def test_compose_keeps_docker_socket_away_from_main_application() -> None:
    compose = open("docker-compose.yml", encoding="utf-8").read()
    main, controller = compose.split("  sandbox-controller:", 1)
    controller = controller.split("\nvolumes:", 1)[0]
    assert "/var/run/docker.sock" not in main
    assert "/var/run/docker.sock" in controller
    assert "ports:" not in controller
    for control in ("read_only: true", "cap_drop:", "no-new-privileges:true", "DOCKER_GID"):
        assert control in controller


def test_controller_image_contains_cli_but_no_application_or_model_dependencies() -> None:
    dockerfile = open("Dockerfile.controller", encoding="utf-8").read()
    requirements = open("requirements-controller.txt", encoding="utf-8").read().lower()
    assert "docker.io" in dockerfile
    assert "USER 10002:10002" in dockerfile
    assert "COPY . ." not in dockerfile
    for forbidden in ("openai", "pydantic-ai", "pandas", "python-dotenv"):
        assert forbidden not in requirements
