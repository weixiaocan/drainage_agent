from __future__ import annotations

import runpy
from pathlib import Path


CODE = Path("/job/code/main.py")
INPUT = Path("/job/input")
OUTPUT = Path("/job/output")


def main() -> None:
    if not CODE.is_file() or CODE.is_symlink():
        raise RuntimeError("sandbox code file is unavailable")
    if not INPUT.is_dir() or not OUTPUT.is_dir():
        raise RuntimeError("sandbox job directories are unavailable")
    runpy.run_path(str(CODE), run_name="__main__")


if __name__ == "__main__":
    main()
