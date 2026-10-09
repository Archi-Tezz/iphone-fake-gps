"""Command line interface.

`iosloc ui` is the usual entry point -- it starts the local panel. The other
subcommands exist for scripting and for diagnosing a connection without a
browser in the way.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import logging
import sys
import traceback
from typing import Optional, Sequence

from . import __version__, errors
from .profiles import PROFILES, get_profile, ms_to_kmh
from .route import parse_gpx
from .session import LocationSession

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iosloc",
        description="Override an iPhone's location over USB using Apple's own developer services.",
    )
    parser.add_argument("--version", action="version", version=f"ios-loc {__version__}")
    parser.add_argument("--udid", help="device UDID (defaults to the first one found)")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument(
        "--enable-developer-mode",
        action="store_true",
        help="try to enable Developer Mode on the iPhone (it will restart)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    ui = sub.add_parser("ui", help="open the control panel in a browser")
    ui.add_argument("--port", type=int, default=None,
                    help="panel port (default 8723; the next free one if taken)")
    ui.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    ui.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    ui.add_argument("--lan", action="store_true",
                    help="serve the panel on your Wi-Fi network so a phone can drive it "
                         "(protected by a one-time access code)")

    sub.add_parser("devices", help="list connected devices")
    sub.add_parser("devmode", help="reveal the Developer Mode entry in iPhone settings")
    wifi = sub.add_parser("wifi", help="allow working with the device over Wi-Fi (no cable)")
    wifi.add_argument("--off", action="store_true", help="turn it back off")
    sub.add_parser("doctor", help="check that everything needed is in place")
    log_cmd = sub.add_parser("log", help="show the log file, newest last")
    log_cmd.add_argument("-f", "--follow", action="store_true",
                         help="keep watching for new lines (Ctrl+C to stop)")
    log_cmd.add_argument("-n", "--lines", type=int, default=200,
                         help="how many lines to show (default 200)")

    set_cmd = sub.add_parser("set", help="set a position and hold it")
    set_cmd.add_argument("latitude", type=float)
    set_cmd.add_argument("longitude", type=float)
    set_cmd.add_argument("--hold", action="store_true", help="hold until Ctrl+C (otherwise exit immediately)")

    sub.add_parser("clear", help="restore the real location")

    walk = sub.add_parser("walk", help="travel a route given as points or a GPX file")
    walk.add_argument("points", nargs="*", help="lat,lon pairs separated by spaces")
    walk.add_argument("--gpx", help="GPX file instead of a point list")
    walk.add_argument("--profile", default="walk", choices=sorted(PROFILES))
    walk.add_argument("--speed", type=float, help="speed in km/h (overrides the profile)")
    walk.add_argument("--mode", default="once", choices=["once", "loop", "pingpong"])

    return parser


def _parse_points(raw: Sequence[str]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in raw:
        parts = item.replace(";", ",").split(",")
        if len(parts) != 2:
            raise ValueError(f"does not look like coordinates: {item!r} (expected lat,lon)")
        points.append((float(parts[0]), float(parts[1])))
    return points


async def _devices() -> int:
    session = LocationSession()
    devices = await session.devices()
    if not devices:
        print("No devices found.")
        print("Connect an iPhone by cable, unlock it and confirm \"Trust This Computer\".")
        return 1
    for device in devices:
        print(f"{device.name}  [{device.udid}]")
        print(f"  model: {device.model or device.product_type}, iOS {device.ios_version}")
        print(f"  link: {device.connection_type}, tunnel required: {'yes' if device.needs_tunnel else 'no'}")
        if device.developer_mode is not None:
            print(f"  Developer Mode: {'on' if device.developer_mode else 'OFF'}")
        if device.problem:
            print(f"  ! {device.problem}")
    return 0


#: Modules the connection path needs, with what breaks when one is missing.
REQUIRED_MODULES = [
    ("pymobiledevice3.lockdown", "device link over USB"),
    ("pymobiledevice3.services.mobile_image_mounter", "mounting the developer image"),
    ("pymobiledevice3.services.dvt.instruments.location_simulation", "location override"),
    ("pymobiledevice3.services.amfi", "revealing the Developer Mode entry"),
    ("pytun_pmd3", "tunnel network driver (wintun)"),
    ("pymobiledevice3.remote.userspace_tunnel", "tunnel for iOS 17 and newer"),
    ("pystray", "system tray icon"),
    ("qrcode", "QR code for phone access"),
]


def _check_imports() -> bool:
    """Import every module the connection path needs.

    In a frozen build a missing binary (wintun.dll, a compiled extension) only
    shows up when the user presses Connect, as a stack trace. Importing the set
    up front turns that into a plain checklist that runs without a device.
    """
    import importlib

    print("\nModules required to connect:")
    ok = True
    for name, purpose in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            ok = False
            print(f"  [FAIL] {purpose}")
            print(f"           {type(exc).__name__}: {exc}")
        else:
            print(f"  [ok] {purpose}")
    if not ok:
        print("  -> The build is incomplete. Rebuild with tools/make_release.py")
    return ok


async def _devmode(args: argparse.Namespace) -> int:
    """Unhide the Developer Mode switch, which iOS keeps hidden until asked."""
    from .device import reveal_developer_mode

    print(await reveal_developer_mode(args.udid))
    return 0


async def _wifi(args: argparse.Namespace) -> int:
    """Enable (or disable) wireless lockdown, which needs the cable to set up."""
    from .device import enable_wifi_connection

    print(await enable_wifi_connection(args.udid, enable=not args.off))
    return 0


async def _doctor() -> int:
    """Check the host side first, then the device side, and say what is missing."""
    ok = True
    print(f"ios-loc {__version__}")
    print(f"Python: {sys.version.split()[0]}")

    try:
        from importlib.metadata import version

        print(f"pymobiledevice3: {version('pymobiledevice3')}")
    except Exception as exc:
        print(f"pymobiledevice3: NOT READY ({exc})")
        return 1

    from .logsetup import current_log_path

    path = current_log_path()
    if path:
        print(f"Log file: {path}")

    if not _check_imports():
        ok = False

    from pymobiledevice3 import usbmux

    print()
    try:
        mux_devices = await usbmux.list_devices()
        print(f"usbmuxd (Apple Mobile Device service): available, devices: {len(mux_devices)}")
    except Exception as exc:
        ok = False
        print(f"usbmuxd: UNAVAILABLE ({exc})")
        print("  -> Install Apple Devices (Microsoft Store) or iTunes from apple.com,")
        print("     then check the Apple Mobile Device Service in services.msc")
        return 1

    if not mux_devices:
        print("No devices connected — remaining checks skipped.")
        return 0 if ok else 1

    session = LocationSession()
    for device in await session.devices():
        print(f"\n{device.name} (iOS {device.ios_version})")
        print(f"  paired: {'yes' if device.paired else 'NO'}")
        if device.developer_mode is not None:
            print(f"  Developer Mode: {'on' if device.developer_mode else 'OFF'}")
        print(f"  path: {'RemoteXPC tunnel' if device.needs_tunnel else 'lockdown directly'}")
        if device.problem:
            ok = False
            print(f"  ! {device.problem}")
    return 0 if ok else 1


async def _set(args: argparse.Namespace) -> int:
    session = LocationSession()
    await session.connect(udid=args.udid, enable_developer_mode=args.enable_developer_mode)
    device = session.device
    assert device is not None
    print(f"Connected: {device.name}, iOS {device.ios_version}")
    await session.teleport(args.latitude, args.longitude)
    print(f"Position: {args.latitude}, {args.longitude}")
    if not args.hold:
        # The override lives in the device's developer session, which ends with
        # the connection -- so without --hold the position lasts only moments.
        print("Note: without --hold the override is dropped as soon as this exits.")
        await session.disconnect(restore=True)
        return 0
    print("Holding the position. Ctrl+C restores the real location.")
    await _hold(session)
    return 0


async def _clear(args: argparse.Namespace) -> int:
    session = LocationSession()
    await session.connect(udid=args.udid, enable_developer_mode=args.enable_developer_mode)
    await session.force_clear()
    print("Real location restored.")
    await session.disconnect(restore=False)
    return 0


async def _walk(args: argparse.Namespace) -> int:
    if args.gpx:
        track = parse_gpx(args.gpx)
        points = track.points
        print(f"GPX: {len(points)} points, {track.length / 1000:.2f} km")
    else:
        points = _parse_points(args.points)
        if not points:
            print("Give points (lat,lon ...) or a --gpx file", file=sys.stderr)
            return 2

    profile = get_profile(args.profile)
    session = LocationSession()
    await session.connect(udid=args.udid, enable_developer_mode=args.enable_developer_mode)
    device = session.device
    assert device is not None
    print(f"Connected: {device.name}, iOS {device.ios_version} ({session.state()['transport']})")

    summary = await session.follow(
        points=points,
        profile=args.profile,
        mode=args.mode,
        speed_kmh=args.speed,
        from_current=False,
    )
    speed = args.speed if args.speed is not None else round(ms_to_kmh(profile.speed), 1)
    print(f"Route: {summary['length_m'] / 1000:.2f} km, {speed} km/h, mode {args.mode}")
    if summary["eta_s"]:
        print(f"Estimated time: {summary['eta_s'] // 60} min {summary['eta_s'] % 60} s")
    print("Ctrl+C stops and restores the real location.")
    await _hold(session, report=True)
    return 0


async def _hold(session: LocationSession, report: bool = False) -> None:
    """Idle until interrupted, then always restore the real location."""
    announced_finish = False
    try:
        while True:
            await asyncio.sleep(2.0)
            snapshot = session.state()
            if snapshot["error"]:
                print(f"\nError: {snapshot['error']}", file=sys.stderr)
                break
            if report and snapshot["position"]:
                position = snapshot["position"]
                route = snapshot["route"] or {}
                progress = route.get("progress")
                tail = f" {progress * 100:5.1f}%" if progress is not None else ""
                print(
                    f"\r{position['lat']:.5f}, {position['lon']:.5f}  "
                    f"{position['speed_kmh']:6.1f} km/h{tail}   ",
                    end="",
                    flush=True,
                )
            if _route_finished(snapshot) and not announced_finish:
                announced_finish = True
                report = False
                print("\nRoute finished. Holding the final point (Ctrl+C to exit).")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\nRestoring the real location...")
        with contextlib.suppress(Exception):
            await session.disconnect(restore=True)


def _route_finished(snapshot: dict) -> bool:
    route = snapshot.get("route")
    return bool(route and route.get("finished"))


def _force_utf8_output() -> None:
    """Keep non-ASCII output readable in cmd.exe, whose code page is not UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _console_closes_on_exit() -> bool:
    """Whether this process owns its console window, i.e. it was double-clicked.

    When Explorer launches an app, Windows creates a console just for it and
    destroys it the moment the process ends -- so any error message flashes by
    unread. `GetConsoleProcessList` reporting a single attached process is the
    reliable way to tell that case from being run inside an existing terminal.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        buffer = (ctypes.c_uint * 8)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(buffer, 8)
        return count <= 1
    except Exception:
        return False


def _pause_if_window_would_vanish() -> None:
    """Hold the window open so the user can actually read what went wrong."""
    if not _console_closes_on_exit():
        return
    print("\nPress Enter to close this window...", end="", flush=True)
    with contextlib.suppress(Exception):
        input()


def _log(args) -> int:
    """Print the log file, optionally following it.

    The window being closed is the normal case, not the exception -- this is how
    you read what happened after the fact, without hunting for the file.
    """
    import time

    from .logsetup import LOG_NAME, _candidate_directories

    path = None
    for directory in _candidate_directories():
        candidate = directory / LOG_NAME
        if candidate.exists():
            path = candidate
            break

    if path is None:
        print("No log file yet. Start the program once, then look again.")
        return 1

    print(f"Log file: {path}", flush=True)
    print("-" * 62, flush=True)

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        tail = collections.deque(handle, maxlen=max(10, args.lines))
        for line in tail:
            print(line.rstrip(), flush=True)
        if not args.follow:
            return 0

        print("-" * 62, flush=True)
        print("Watching for new lines. Ctrl+C to stop.", flush=True)
        try:
            while True:
                line = handle.readline()
                if line:
                    print(line.rstrip(), flush=True)
                else:
                    time.sleep(0.4)
        except KeyboardInterrupt:
            return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_output()
    try:
        code = _run(argv)
    except SystemExit as exit_request:  # argparse errors (bad flags, --help)
        code = int(exit_request.code or 0)
        if code != 0:
            _pause_if_window_would_vanish()
        return code
    except Exception:
        # An unexpected crash is exactly when the window must not disappear.
        traceback.print_exc()
        _pause_if_window_would_vanish()
        return 1
    if code != 0:
        _pause_if_window_would_vanish()
    return code


def _run(argv: Optional[Sequence[str]]) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format=LOG_FORMAT,
    )
    # pymobiledevice3 is chatty at INFO even when our own level is higher.
    logging.getLogger("pymobiledevice3").setLevel(logging.DEBUG if args.verbose else logging.ERROR)

    # The file keeps what the console throws away, which is what makes a report
    # from someone else's machine usable.
    from .logsetup import setup_file_logging

    log_path = setup_file_logging(verbose=args.verbose)
    # `ui` names the file in its own banner; printing it here as well would
    # just push the banner down the window.
    if log_path and args.command == "doctor":
        print(f"Log file: {log_path}", flush=True)

    if args.command == "ui":
        from .server import serve

        try:
            return serve(
                port=args.port,
                open_browser=not args.no_browser,
                log_level="info" if args.verbose else "warning",
                tray=not args.no_tray,
                lan=args.lan,
            )
        except KeyboardInterrupt:
            return 0

    if args.command == "log":
        return _log(args)

    handlers = {
        "devices": lambda: _devices(),
        "devmode": lambda: _devmode(args),
        "wifi": lambda: _wifi(args),
        "doctor": lambda: _doctor(),
        "set": lambda: _set(args),
        "clear": lambda: _clear(args),
        "walk": lambda: _walk(args),
    }
    try:
        return asyncio.run(handlers[args.command]())
    except KeyboardInterrupt:
        return 130
    except errors.IosLocError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
