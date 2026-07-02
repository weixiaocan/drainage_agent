from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    root = ROOT
    load_dotenv(root / ".env")
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
