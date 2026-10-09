"""File logging.

Most failures here happen on someone else's machine, in front of a console
window that is closed before anything can be read. A rotating log file next to
the program turns "it stopped working" into a file that can simply be sent.

The file lives beside the executable when that directory is writable (so a
portable copy keeps its own log) and falls back to %LOCALAPPDATA% when it is
not -- Program Files, a read-only share, a flash drive mounted read-only.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

__all__ = ["LOG_NAME", "current_log_path", "setup_file_logging"]

LOG_NAME = "ios-loc.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 2

_log_path: Optional[Path] = None


def _candidate_directories() -> list[Path]:
    """Where to try putting the log, best first."""
    places: list[Path] = []
    if getattr(sys, "frozen", False):
        places.append(Path(sys.executable).parent)
    else:
        places.append(Path(__file__).resolve().parent.parent)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        places.append(Path(local) / "ios-loc")
    places.append(Path.home() / ".ios-loc")
    return places


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def current_log_path() -> Optional[Path]:
    return _log_path


def setup_file_logging(verbose: bool = False) -> Optional[Path]:
    """Attach a rotating file handler to the root logger.

    Returns the path in use, or None when no directory could be written to --
    in which case the program carries on with console logging only, because a
    missing log must never be a reason not to start.
    """
    global _log_path

    for directory in _candidate_directories():
        if not _writable(directory):
            continue
        path = directory / LOG_NAME
        try:
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
        except Exception:
            continue

        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        # The file keeps the detail even when the console is kept quiet; that
        # detail is the whole point of having it.
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)

        root = logging.getLogger()
        root.addHandler(handler)
        if root.level > handler.level:
            root.setLevel(handler.level)
        # pymobiledevice3 is where the useful detail lives when a connection fails.
        logging.getLogger("pymobiledevice3").setLevel(logging.DEBUG if verbose else logging.INFO)

        _log_path = path
        # Not logged here: the caller prints the path once, and logging it would
        # duplicate that line on the console.
        return path

    return None
