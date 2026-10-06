"""Entry point for the packaged Windows build.

Double-clicking the .exe passes no arguments, which should open the panel rather
than print usage -- so a bare launch is rewritten to `ui`. Every CLI subcommand
still works: `ios-loc.exe doctor`, `ios-loc.exe devices`, and so on.
"""

from __future__ import annotations

import multiprocessing
import sys

from iosloc.cli import main

if __name__ == "__main__":
    # PyInstaller relaunches the executable for child processes; without this a
    # frozen app that ever spawns one forks an endless chain of new windows.
    multiprocessing.freeze_support()

    argv = sys.argv[1:]
    if not argv:
        argv = ["ui"]
    sys.exit(main(argv))
