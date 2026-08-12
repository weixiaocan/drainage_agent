from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_main_image_runs_as_unprivileged_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "USER 10001:10002" in dockerfile
    assert "chmod 2770 /var/lib/sandbox-jobs" in dockerfile


def test_compose_applies_main_container_security_defaults() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    main, controller = compose.split("  sandbox-controller:", maxsplit=1)

    assert '"127.0.0.1:8000:8000"' in main
    assert "read_only: true" in main
    assert "no-new-privileges:true" in main
    assert "cap_drop:\n      - ALL" in main
    assert "mem_limit: 2g" in main
    assert "cpus: 2.0" in main
    assert "pids_limit: 256" in main
    assert "/var/run/docker.sock" not in main

    assert "ports:" not in controller
    assert "network_mode: host" not in controller
    assert "mem_limit: 256m" in controller
    assert "cpus: 0.5" in controller
    assert "pids_limit: 64" in controller
