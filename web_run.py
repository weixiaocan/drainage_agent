from __future__ import annotations

from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def main() -> int:
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

