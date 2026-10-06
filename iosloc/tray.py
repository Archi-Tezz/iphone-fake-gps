"""System tray icon for the running panel.

The app has to stay running for the location override to hold, so it occupies a
taskbar slot for as long as it is used. The tray icon gives it somewhere to live
instead: open the panel, hand the real location back, or quit -- without keeping
a console window in the way.

Everything here is best-effort. A missing tray, a locked-down session or no
pystray at all must never stop the panel from serving, so every entry point
degrades to a no-op rather than raising.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ICON_PATH = Path(__file__).parent / "static" / "brand" / "icon-256.png"

__all__ = ["console_window_available", "hide_console", "show_console", "start_tray"]


# --------------------------------------------------------------- console window


def _console_window() -> int:
    """Handle of this process's console window, or 0 when there is none."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return 0


def console_window_available() -> bool:
    return _console_window() != 0


def _show_window(visible: bool) -> bool:
    handle = _console_window()
    if not handle:
        return False
    try:
        import ctypes

        # SW_HIDE = 0, SW_SHOW = 5
        ctypes.windll.user32.ShowWindow(handle, 5 if visible else 0)
        return True
    except Exception:
        return False


def hide_console() -> bool:
    """Hide the console window. Returns False when there is nothing to hide."""
    return _show_window(False)


def show_console() -> bool:
    return _show_window(True)


# -------------------------------------------------------------------- the icon


def start_tray(
    url: str,
    on_restore: Callable[[], None],
    on_quit: Callable[[], None],
) -> Optional[object]:
    """Run a tray icon on its own thread and return it (None if unavailable).

    :param url: the panel address, opened by the default action.
    :param on_restore: hand the device its real location back.
    :param on_quit: stop the server.
    """
    try:
        import pystray
        from PIL import Image
    except Exception as exc:
        logger.debug("tray unavailable: %s", exc)
        return None

    try:
        image = Image.open(ICON_PATH)
    except Exception as exc:
        logger.debug("tray icon image missing: %s", exc)
        return None

    def open_panel(_icon=None, _item=None) -> None:
        webbrowser.open(url)

    def restore(_icon=None, _item=None) -> None:
        try:
            on_restore()
        except Exception:
            logger.exception("restore from tray failed")

    def quit_app(icon, _item=None) -> None:
        icon.visible = False
        icon.stop()
        show_console()
        try:
            on_quit()
        except Exception:
            logger.exception("quit from tray failed")

    menu_items = [
        pystray.MenuItem("Открыть панель", open_panel, default=True),
        pystray.MenuItem("Вернуть реальную геопозицию", restore),
    ]
    if console_window_available():
        menu_items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Показать окно", lambda *_: show_console()),
            pystray.MenuItem("Скрыть окно", lambda *_: hide_console()),
        ]
    menu_items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Выход", quit_app)]

    icon = pystray.Icon("ios-loc", image, "ios-loc — подмена геопозиции", pystray.Menu(*menu_items))

    # run_detached is not available on every backend, so the icon gets a thread
    # of its own. Daemon, so a stuck tray cannot keep the process alive.
    thread = threading.Thread(target=icon.run, name="iosloc-tray", daemon=True)
    thread.start()
    logger.info("tray icon started")
    return icon
