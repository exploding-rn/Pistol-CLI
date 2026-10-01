"""Windowless Explorer entry point. Errors go to the Pistol runtime log."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import sys

from .config import PistolError, data_dir
from .explorer import handle


def _log_handler() -> RotatingFileHandler:
    directory = data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "uri.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    return handler


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        handler = _log_handler()
    except OSError:
        return 2
    logger = logging.getLogger("pistol")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        if len(arguments) != 1:
            raise PistolError("Expected exactly one Pistol URI.")
        handle(arguments[0])
    except Exception as exc:
        logger.exception("Explorer URI handler failed: %s", exc)
        return 2
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
