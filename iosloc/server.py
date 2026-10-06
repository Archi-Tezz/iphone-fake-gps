"""Local HTTP API and web UI host.

The server binds to the loopback interface only. It drives a phone over USB, so
exposing it on a LAN address would hand that control to anyone on the network;
`--host` is deliberately not offered.

Online helpers (address search, road snapping) are off unless the request says
`online: true`, which the UI only sets when the user ticks the box. Nothing
leaves the machine otherwise -- map tiles aside, which the browser fetches
directly.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import traceback
import webbrowser
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import errors
from .i18n import t
from .profiles import PROFILES
from .session import LocationSession

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

#: Identifies this tool to the OSM services, as their usage policies require.
USER_AGENT = "ios-loc/1.0 (local developer tool)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1"
ONLINE_TIMEOUT = 12

session = LocationSession()

#: Set only in LAN mode. None means loopback-only, where no token is needed
#: because nothing off this machine can reach the server at all.
access_token: Optional[str] = None
#: The cookie that keeps a phone authenticated after the first scan.
TOKEN_COOKIE = "iosloc_access"


# --------------------------------------------------------------------- schemas


class ConnectRequest(BaseModel):
    udid: Optional[str] = None
    enable_developer_mode: bool = False


class TeleportRequest(BaseModel):
    lat: float
    lon: float
    profile: Optional[str] = None


class Point(BaseModel):
    lat: float
    lon: float


class RouteRequest(BaseModel):
    points: list[Point] = Field(min_length=1)
    profile: Optional[str] = None
    mode: str = "once"
    speed_kmh: Optional[float] = None
    from_current: bool = True


class SteerRequest(BaseModel):
    heading: Optional[float] = None
    speed_kmh: Optional[float] = None
    snap_heading: bool = False


class ProfileRequest(BaseModel):
    profile: str
    keep_speed: bool = False


class SpeedRequest(BaseModel):
    speed_kmh: float


class StopRequest(BaseModel):
    restore: bool = False


class SearchRequest(BaseModel):
    query: str
    online: bool = False


class SnapRequest(BaseModel):
    points: list[Point] = Field(min_length=2)
    mode: str = "driving"  # driving | walking | cycling
    online: bool = False


# ------------------------------------------------------------------------ app


@asynccontextmanager
async def lifespan(application: FastAPI):
    # The tray icon runs on its own thread and needs this loop to schedule work
    # on the session, which is not thread-safe.
    application.state.loop = asyncio.get_running_loop()
    yield
    # Leaving a phone with a frozen fake location after the tool exits would be
    # a nasty surprise, so the real GPS is always handed back on shutdown.
    with suppress(Exception):
        await session.disconnect(restore=True)


app = FastAPI(title="ios-loc", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def require_token(request: Request, call_next):
    """Gate every request when the panel is exposed on the network.

    The token may arrive as ``?k=`` (what the QR code encodes) or as the cookie
    set from it, so the phone only has to scan once.
    """
    if access_token is None:
        return await call_next(request)

    supplied = request.query_params.get("k") or request.cookies.get(TOKEN_COOKIE)
    # Compared in constant time: this is reachable by anything on the network.
    if not supplied or not secrets.compare_digest(supplied, access_token):
        return JSONResponse(
            status_code=401,
            content={"detail": t("An access code is required. Open the link with the code, "
                                 "or scan the QR.")},
        )

    response = await call_next(request)
    if request.query_params.get("k"):
        response.set_cookie(
            TOKEN_COOKIE, access_token,
            max_age=12 * 3600, httponly=True, samesite="lax",
        )
    return response


@app.exception_handler(errors.IosLocError)
async def _iosloc_error_handler(_, exc: errors.IosLocError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "kind": type(exc).__name__})


def _guard(coro):
    """Turn every failure into a message the panel can show.

    A bare 500 tells the user nothing, and the panel is the only place most
    users will ever look -- so an unexpected exception is logged in full to the
    console and its type and message are passed on, rather than swallowed.
    """

    async def wrapper() -> Any:
        try:
            return await coro
        except errors.IosLocError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("unhandled failure")
            traceback.print_exc()
            raise HTTPException(
                status_code=500,
                detail=t(
                    "Unexpected error: {error}\nThe full text is in the program window "
                    "(the black console).",
                    error=f"{type(exc).__name__}: {exc}"),
            ) from exc

    return wrapper()


@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    return session.state()


@app.get("/api/profiles")
async def api_profiles() -> dict[str, Any]:
    return {"profiles": [profile.as_dict() for profile in PROFILES.values()]}


@app.get("/api/devices")
async def api_devices() -> dict[str, Any]:
    async def work():
        return {"devices": [device.as_dict() for device in await session.devices()]}

    return await _guard(work())


@app.post("/api/connect")
async def api_connect(request: ConnectRequest) -> dict[str, Any]:
    async def work():
        info = await session.connect(
            udid=request.udid, enable_developer_mode=request.enable_developer_mode
        )
        return {"device": info.as_dict(), "state": session.state()}

    return await _guard(work())


class WifiRequest(BaseModel):
    udid: Optional[str] = None
    enable: bool = True


@app.post("/api/enable-wifi")
async def api_enable_wifi(request: WifiRequest) -> dict[str, Any]:
    """Turn wireless lockdown on, so the cable can come out."""

    async def work():
        from .device import enable_wifi_connection

        return {"message": await enable_wifi_connection(request.udid, request.enable)}

    return await _guard(work())


@app.post("/api/reveal-devmode")
async def api_reveal_devmode(request: ConnectRequest) -> dict[str, Any]:
    """Unhide the Developer Mode switch in the device's Settings."""

    async def work():
        from .device import reveal_developer_mode

        return {"message": await reveal_developer_mode(request.udid)}

    return await _guard(work())


@app.get("/api/phone-link")
async def api_phone_link() -> dict[str, Any]:
    """Where and how to open this panel from a phone."""
    from .access import local_addresses

    if access_token is None:
        return {
            "enabled": False,
            "hint": t("Phone access is off. Start the program with --lan "
                      "(ios-loc.exe ui --lan) to open the panel on your Wi-Fi network."),
        }
    addresses = local_addresses()
    return {
        "enabled": True,
        "urls": [f"http://{address}:{server_port}/?k={access_token}" for address in addresses],
        "token": access_token,
        "port": server_port,
    }


@app.get("/api/phone-qr.png")
async def api_phone_qr():
    """QR code for the phone to scan; encodes the URL including the token."""
    from fastapi.responses import Response

    from .access import primary_address, qr_png

    if access_token is None:
        raise HTTPException(status_code=404, detail=t("Phone access is off"))
    address = primary_address()
    if not address:
        raise HTTPException(status_code=503, detail=t("Could not determine the local network address"))
    png = qr_png(f"http://{address}:{server_port}/?k={access_token}")
    if png is None:
        raise HTTPException(status_code=503, detail=t("Could not build the QR code"))
    return Response(content=png, media_type="image/png")


@app.post("/api/minimize")
async def api_minimize() -> dict[str, Any]:
    """Hide the console window; the tray icon stays as the way back."""
    from .tray import hide_console

    return {"hidden": hide_console()}


@app.post("/api/disconnect")
async def api_disconnect() -> dict[str, Any]:
    await session.disconnect(restore=True)
    return session.state()


@app.post("/api/teleport")
async def api_teleport(request: TeleportRequest) -> dict[str, Any]:
    async def work():
        await session.teleport(request.lat, request.lon, profile=request.profile)
        return session.state()

    return await _guard(work())


@app.post("/api/route")
async def api_route(request: RouteRequest) -> dict[str, Any]:
    async def work():
        summary = await session.follow(
            points=[(point.lat, point.lon) for point in request.points],
            profile=request.profile,
            mode=request.mode,
            speed_kmh=request.speed_kmh,
            from_current=request.from_current,
        )
        return {"route": summary, "state": session.state()}

    return await _guard(work())


@app.post("/api/steer")
async def api_steer(request: SteerRequest) -> dict[str, Any]:
    async def work():
        await session.steer(
            heading=request.heading,
            speed_kmh=request.speed_kmh,
            snap_heading=request.snap_heading,
        )
        return session.state()

    return await _guard(work())


@app.post("/api/profile")
async def api_profile(request: ProfileRequest) -> dict[str, Any]:
    async def work():
        await session.set_profile(request.profile, keep_speed=request.keep_speed)
        return session.state()

    return await _guard(work())


@app.post("/api/speed")
async def api_speed(request: SpeedRequest) -> dict[str, Any]:
    async def work():
        await session.set_speed(request.speed_kmh)
        return session.state()

    return await _guard(work())


@app.post("/api/pause")
async def api_pause() -> dict[str, Any]:
    await session.pause()
    return session.state()


@app.post("/api/resume")
async def api_resume() -> dict[str, Any]:
    async def work():
        await session.resume()
        return session.state()

    return await _guard(work())


@app.post("/api/stop")
async def api_stop(request: StopRequest) -> dict[str, Any]:
    async def work():
        await session.stop(restore=request.restore)
        return session.state()

    return await _guard(work())


# ------------------------------------------------------------- online helpers


@app.post("/api/search")
async def api_search(request: SearchRequest) -> dict[str, Any]:
    """Geocode a place name through OSM Nominatim. Requires `online`."""
    if not request.online:
        raise HTTPException(
            status_code=403,
            detail=t("Address search sends the query to OpenStreetMap. Enable "
                     "\"Online services\" in settings if that is acceptable."),
        )
    import requests

    def fetch() -> list[dict[str, Any]]:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": request.query, "format": "jsonv2", "limit": 8},
            headers={"User-Agent": USER_AGENT},
            timeout=ONLINE_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    try:
        results = await asyncio.to_thread(fetch)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=t("Search is unavailable: {error}", error=exc)) from exc

    return {
        "results": [
            {
                "name": item.get("display_name", ""),
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "kind": item.get("type", ""),
            }
            for item in results
            if item.get("lat") and item.get("lon")
        ]
    }


@app.post("/api/snap")
async def api_snap(request: SnapRequest) -> dict[str, Any]:
    """Turn waypoints into a road-following path through the public OSRM. Requires `online`."""
    if not request.online:
        raise HTTPException(
            status_code=403,
            detail=t("Road routing runs on the public OSRM server. Enable "
                     "\"Online services\" in settings if that is acceptable."),
        )
    if request.mode not in {"driving", "walking", "cycling"}:
        raise HTTPException(status_code=400, detail=t("mode must be driving, walking or cycling"))

    import requests

    coordinates = ";".join(f"{point.lon},{point.lat}" for point in request.points)

    def fetch() -> dict[str, Any]:
        response = requests.get(
            f"{OSRM_URL}/{request.mode}/{coordinates}",
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            headers={"User-Agent": USER_AGENT},
            timeout=ONLINE_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    try:
        payload = await asyncio.to_thread(fetch)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=t("The router is unavailable: {error}", error=exc)) from exc

    routes = payload.get("routes") or []
    if not routes:
        raise HTTPException(status_code=404, detail=t("No road route found"))
    geometry = routes[0]["geometry"]["coordinates"]
    return {
        "points": [{"lat": lat, "lon": lon} for lon, lat in geometry],
        "distance_m": round(routes[0].get("distance", 0.0), 1),
        "duration_s": round(routes[0].get("duration", 0.0)),
    }


# ----------------------------------------------------------------------- html


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


DEFAULT_PORT = 8723
#: How many ports past the default to try before giving up.
PORT_SEARCH_RANGE = 12


def _port_is_free(port: int) -> bool:
    """Whether 127.0.0.1:<port> can be bound right now.

    Deliberately no SO_REUSEADDR: on Windows it lets a bind succeed on a port
    another process is already listening on, which would turn this check into a
    lie and move the failure to uvicorn.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _panel_already_running(port: int) -> bool:
    """Whether the thing occupying this port is another copy of ios-loc."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as response:
            return "connected" in json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def _open_later(url: str, delay: float = 1.0) -> None:
    """Open the browser once uvicorn has had a moment to bind."""
    import threading

    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


#: Port the running server bound to, for building phone links.
server_port = DEFAULT_PORT


def serve(
    port: Optional[int] = None,
    open_browser: bool = True,
    log_level: str = "warning",
    tray: bool = True,
    lan: bool = False,
) -> int:
    """Run the local UI. Loopback only -- see the module docstring.

    `port` of None means "the default, or the next free one after it": a busy
    port is the single most common reason the window closes instantly when the
    app is started by double-click, and silently dying is the worst answer to it.
    An explicit port is treated as a real choice and is never substituted.
    """
    import uvicorn

    chosen = port if port is not None else DEFAULT_PORT

    if not _port_is_free(chosen):
        if _panel_already_running(chosen):
            url = f"http://127.0.0.1:{chosen}/"
            print(f"ios-loc is already running at {url} — opening it.", flush=True)
            if open_browser:
                webbrowser.open(url)
            return 0
        if port is not None:
            print(f"Port {chosen} is used by another program.", file=sys.stderr)
            print("Pick a free one, for example:  ios-loc.exe ui --port 8800", file=sys.stderr)
            return 1
        for candidate in range(DEFAULT_PORT + 1, DEFAULT_PORT + PORT_SEARCH_RANGE):
            if _port_is_free(candidate):
                print(f"Port {DEFAULT_PORT} is busy, moving to {candidate}.", flush=True)
                chosen = candidate
                break
        else:
            print(
                f"Ports {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SEARCH_RANGE - 1} are all busy.",
                file=sys.stderr,
            )
            print("Free one of them or pick your own:  ios-loc.exe ui --port 8800",
                  file=sys.stderr)
            return 1

    global access_token, server_port
    server_port = chosen
    url = f"http://127.0.0.1:{chosen}/"
    host = "127.0.0.1"

    if lan:
        # Reachable from the network means reachable by everyone on it, so the
        # token is not optional here.
        from .access import generate_token, primary_address, qr_ascii

        access_token = generate_token()
        host = "0.0.0.0"
        url = f"http://127.0.0.1:{chosen}/?k={access_token}"
        address = primary_address()
        print(f"ios-loc: control panel → {url}", flush=True)
        if address:
            phone_url = f"http://{address}:{chosen}/?k={access_token}"
            print("\nOpen on your phone (same Wi-Fi):", flush=True)
            print(f"    {phone_url}", flush=True)
            code = qr_ascii(phone_url)
            if code:
                print(code, flush=True)
            print(f"Access code: {access_token}", flush=True)
        else:
            print("Could not determine the local network address — check your connection.",
                  flush=True)
        print("The panel is now open to your local network. Nothing gets in without the\n"
              "access code, but do not use this mode on a public or untrusted network.\n",
              flush=True)
    else:
        print(f"ios-loc: control panel → {url}", flush=True)

    print("Stop with Ctrl+C — the real location is restored automatically", flush=True)
    if open_browser:
        _open_later(url)

    # uvicorn.Server rather than uvicorn.run(): the tray thread needs a handle
    # to ask for a clean shutdown.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=chosen, log_level=log_level))
    icon = _start_tray(url, server) if tray else None
    try:
        server.run()
    finally:
        if icon is not None:
            with suppress(Exception):
                icon.stop()
    return 0


def _start_tray(url: str, server: Any) -> Optional[Any]:
    """Attach a tray icon to a running server, if the platform offers one."""
    from .tray import show_console, start_tray

    def on_restore() -> None:
        # The session belongs to the server's event loop; the tray is elsewhere.
        loop = getattr(app.state, "loop", None)
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(session.stop(restore=True), loop)
        future.result(timeout=20)

    def on_quit() -> None:
        show_console()
        server.should_exit = True

    try:
        return start_tray(url, on_restore=on_restore, on_quit=on_quit)
    except Exception:
        logger.exception("tray icon could not be started")
        return None
