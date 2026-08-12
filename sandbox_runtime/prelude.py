from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

INPUT = Path("/job/input")
OUTPUT = Path("/job/output")
SAFE_NAME = re.compile(r"^[\w.-]{1,128}$", re.UNICODE)


def load_flow():
    return pd.read_csv(INPUT / "flow.csv")


def load_rain():
    return pd.read_csv(INPUT / "rainfall.csv")


def load_sites():
    return pd.read_csv(INPUT / "sites.csv")


def save_table(frame, name):
    path = _output(name, ".csv")
    frame.to_csv(path, index=False)
    return path.name


def save_chart(figure, name):
    path = _output(name, ".png")
    figure.savefig(path, format="png")
    return path.name


def save_json(value, name):
    path = _output(name, ".json")
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path.name


def _output(name, suffix):
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or Path(name).suffix.lower() != suffix:
        raise ValueError("invalid output name")
    path = (OUTPUT / name).resolve()
    if not path.is_relative_to(OUTPUT.resolve()) or path.exists():
        raise ValueError("output path is unsafe or already exists")
    return path
