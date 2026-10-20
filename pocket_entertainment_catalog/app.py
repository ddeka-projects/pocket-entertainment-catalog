"""Application entry point for foreground and scheduled startup."""

from __future__ import annotations

import logging

from .config import load_settings
from .logging_setup import configure_logging
from .server import run


def main() -> int:
    try:
        configure_logging()
        run(load_settings())
    except Exception:
        logging.getLogger(__name__).exception(
            "The server could not start or continue. Check .env and logs/server.log."
        )
        return 1
    return 0
