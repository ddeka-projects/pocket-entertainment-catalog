"""Console-free Windows entry point used by the scheduled task."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIRECTORY = PROJECT_ROOT / ".tmp"
LOG_PATH = LOG_DIRECTORY / "server.log"


def main() -> None:
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        os.chdir(PROJECT_ROOT)
        sys.path.insert(0, str(PROJECT_ROOT))
        print(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] Background startup")
        try:
            from pocket_entertainment_catalog.config import load_settings
            from pocket_entertainment_catalog.server import run

            run(load_settings())
        except BaseException:
            traceback.print_exc()
            raise


if __name__ == "__main__":
    main()
