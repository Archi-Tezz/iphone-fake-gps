"""Making it obvious that the program is running.

Started by double-click, the program is a console window that prints a line or
two and then sits silent for hours. That is indistinguishable from a window
that is about to close, so people close it, or start a second copy. This module
answers the only question that window has to answer -- yes, it is running, here
is what it is doing -- by printing a banner, naming itself in the title bar and
echoing activity as it happens.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Optional

__all__ = ["attach_console_log", "print_banner", "set_console_title"]

logger = logging.getLogger(__name__)


def set_console_title(text: str) -> None:
    """Name the console window, so the taskbar says what it is."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception:  # pragma: no cover - cosmetic only
        logger.debug("could not set the console title", exc_info=True)


def _supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


def print_banner(url: str, port: int, log_path: Optional[object], version: str) -> None:
    """Print the one screenful that says the program is up and where it lives."""
    line = "─" * 62 if _supports_unicode() else "-" * 62
    rows = [
        "",
        line,
        f"  ios-loc {version}   RUNNING",
        "",
        f"  Panel        {url}",
        f"  Port         {port}",
        f"  Process ID   {os.getpid()}",
        f"  Log file     {log_path if log_path else 'unavailable'}",
        "",
        "  Keep this window open. It is the program itself.",
        "  Activity appears below as it happens.",
        "  Stop with Ctrl+C -- the real location is restored automatically.",
        line,
        "",
    ]
    for row in rows:
        print(row, flush=True)


class _ConsoleFormatter(logging.Formatter):
    """Short lines: the console is for glancing at, the file keeps the detail."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if record.levelno >= logging.WARNING:
            return f"{text}   [{record.levelname}]"
        return text


def attach_console_log(verbose: bool = False) -> None:
    """Echo this program's own log to the console.

    Only `iosloc` records are echoed: the libraries underneath are noisy enough
    to bury the lines that mean something to the person watching.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(_ConsoleFormatter())
    handler.addFilter(logging.Filter("iosloc"))

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > handler.level:
        root.setLevel(handler.level)
