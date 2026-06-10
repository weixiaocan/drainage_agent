from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


def setup_logging(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"agent-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    return log_file


class TraceLogger:
    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / f"trace-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

    def write(self, event: dict[str, Any]) -> None:
        event = {"ts": datetime.now().isoformat(timespec="seconds"), **event}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

