from __future__ import annotations

import runpy
import sys
import time
import traceback
from pathlib import Path


CODE = Path("/job/code/main.py")
INPUT = Path("/job/input")
OUTPUT = Path("/job/output")
COMPLETION = Path("/tmp/sandbox-complete")


def main() -> None:
    if not CODE.is_file() or CODE.is_symlink():
        raise RuntimeError("sandbox code file is unavailable")
    if not INPUT.is_dir() or not OUTPUT.is_dir():
        raise RuntimeError("sandbox job directories are unavailable")
    exit_code = 0
    try:
        runpy.run_path(str(CODE), run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    COMPLETION.write_text(str(exit_code), encoding="ascii")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
