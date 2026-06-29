from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from agent.core.cli import run_cli
from agent.deps import build_deps
from agent.core.logging_utils import setup_logging


def main() -> int:
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    deps = build_deps(root)
    log_file = setup_logging(deps.paths.logs)
    deps.logger.info("Log file: %s", log_file)
    run_cli(deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
