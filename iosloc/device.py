"""Wired connection to an iPhone, and the location-override channel on it.

Everything here goes through usbmuxd over USB -- the same transport Xcode and
Finder use. The location override itself is Apple's own developer feature
(`com.apple.instruments.server.services.LocationSimulation`, what Xcode's
"Simulate Location" drives), so no jailbreak and no patching is involved.

Three things differ by iOS version, and this module hides all three:

* **Transport.** Up to iOS 16.x developer services are reachable over plain
  lockdown. From iOS 17 they moved behind RemoteXPC, which needs a tunnel. We
  use pymobiledevice3's userspace (PyTCP) tunnel, so no admin rights and no TUN
  driver are required; `tunneld` remains available as a fallback.
* **Developer Disk Image.** Needed before any developer service will start.
  Below iOS 17 it is a downloaded DDI; from iOS 17 it is personalized per
  device. `auto_mount` picks the right one.
* **Developer Mode.** From iOS 16 it has to be switched on on the device.

The result is a `DeviceLink`: an async context manager exposing `set()` /
`clear()` regardless of which of those paths was taken.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import Any, Optional

from packaging.version import Version

from . import errors
from .i18n import t

logger = logging.getLogger(__name__)

#: From this version on, developer services live behind RemoteXPC and need a tunnel.
RSD_MIN_VERSION = Version("17.0")
#: From this version on, Developer Mode must be enabled on the device itself.
DEVELOPER_MODE_MIN_VERSION = Version("16.0")

__all__ = ["DeviceInfo", "DeviceLink", "list_devices"]


@dataclass
class DeviceInfo:
    """What we can say about a connected device before opening a session on it."""

    udid: str
    name: str
    model: str
    product_type: str
    ios_version: str
    connection_type: str
    paired: bool = True
    developer_mode: Optional[bool] = None
    problem: Optional[str] = None

    @property
    def needs_tunnel(self) -> bool:
        return Version(self.ios_version) >= RSD_MIN_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "product_type": self.product_type,
            "ios_version": self.ios_version,
            "connection_type": self.connection_type,
            "paired": self.paired,
            "developer_mode": self.developer_mode,
            "needs_tunnel": self.needs_tunnel,
            "problem": self.problem,
        }


async def list_devices(usb_only: bool = False) -> list[DeviceInfo]:
    """Enumerate reachable devices without pairing or starting any service.

    Includes devices reachable over Wi-Fi, not just the ones on the cable: once
    wireless lockdown is enabled, usbmuxd advertises them the same way.

    A device that is attached but not yet trusted still shows up, with
    ``paired=False`` and a `problem` describing what the user has to do -- that
    is more useful than an empty list.
    """
    from pymobiledevice3 import usbmux
    from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError

    try:
        mux_devices = await usbmux.list_devices()
    except ConnectionFailedToUsbmuxdError as exc:
        raise errors.UsbmuxUnavailableError(t(
            "Apple Mobile Device service (usbmuxd) is unavailable. Install Apple Devices "
            "from the Microsoft Store or iTunes from apple.com, then make sure the Apple "
            "Mobile Device Service is running."
        )) from exc

    # A device plugged in *and* reachable over Wi-Fi appears twice; prefer the
    # USB entry, which is faster and never drops mid-route.
    best: dict[str, str] = {}
    for mux_device in mux_devices:
        if usb_only and not mux_device.is_usb:
            continue
        if mux_device.is_usb or mux_device.serial not in best:
            best[mux_device.serial] = mux_device.connection_type

    return [await _describe(serial, kind) for serial, kind in best.items()]


async def _describe(serial: str, connection_type: str) -> DeviceInfo:
    """Read a device's identity over lockdown, degrading gracefully if it is untrusted."""
    from pymobiledevice3.exceptions import (
        PairingDialogResponsePendingError,
        PasscodeRequiredError,
        PyMobileDevice3Exception,
    )
    from pymobiledevice3.lockdown import create_using_usbmux

    unknown = DeviceInfo(
        udid=serial,
        name=t("(unknown)"),
        model="",
        product_type="",
        ios_version="0.0",
        connection_type=connection_type,
        paired=False,
    )
    try:
        # autopair=False: listing devices must never pop a trust dialog on the phone.
        lockdown = await create_using_usbmux(serial=serial, autopair=False, connection_type=connection_type)
    except PairingDialogResponsePendingError:
        unknown.problem = t(
            "The \"Trust This Computer?\" prompt is open on the iPhone — confirm it and "
            "press Connect again.")
        return unknown
    except PasscodeRequiredError:
        unknown.problem = t("The iPhone is locked. Unlock the screen and connect again.")
        return unknown
    except PyMobileDevice3Exception as exc:
        unknown.problem = t("Could not query the device: {error}", error=exc)
        return unknown

    try:
        values = lockdown.short_info
        info = DeviceInfo(
            udid=values.get("UniqueDeviceID") or serial,
            name=values.get("DeviceName") or "iPhone",
            model=lockdown.display_name or values.get("ProductType") or "",
            product_type=values.get("ProductType") or "",
            ios_version=lockdown.product_version,
            connection_type=connection_type,
            paired=True,
        )
        if Version(info.ios_version) >= DEVELOPER_MODE_MIN_VERSION:
            with suppress(Exception):
                info.developer_mode = await lockdown.get_developer_mode_status()
            if info.developer_mode is False:
                info.problem = t(
                    "Developer Mode is off. On the iPhone: Settings → Privacy & Security → "
                    "Developer Mode → turn on (the iPhone restarts).")
        return info
    finally:
        await lockdown.close()


async def reveal_developer_mode(udid: Optional[str] = None) -> str:
    """Make the Developer Mode switch appear in the device's Settings app.

    Apple hides that switch until a development tool has talked to the device,
    which is why a fresh iPhone shows no such entry under Privacy & Security.
    This sends AMFI the reveal action -- it only unhides the switch; turning it
    on stays the user's decision, on the device.

    :returns: a message describing what the user should do next.
    """
    from pymobiledevice3.exceptions import PyMobileDevice3Exception
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.amfi import AmfiService

    try:
        lockdown = await create_using_usbmux(serial=udid, connection_type="USB")
    except Exception as exc:
        raise errors.PairingRequiredError(t(
            "Could not connect to the iPhone: {error}\nConnect it by cable, unlock the "
            "screen and confirm \"Trust This Computer\".", error=exc)) from exc

    try:
        version = Version(lockdown.product_version)
        if version < DEVELOPER_MODE_MIN_VERSION:
            return t(
                "On iOS {version} Developer Mode is not needed — you can connect right away.",
                version=lockdown.product_version)
        if await lockdown.get_developer_mode_status():
            return t("Developer Mode is already on — you can connect.")

        await AmfiService(lockdown).reveal_developer_mode_option_in_ui()
        return t(
            "Done. On the iPhone open:\n"
            "    Settings → Privacy & Security → Developer Mode\n"
            "Turn the switch on — the iPhone will restart and ask you to confirm.")
    except PyMobileDevice3Exception as exc:
        raise errors.DeveloperModeDisabledError(t(
            "Could not reveal the Developer Mode entry: {error}\nCheck that the iPhone is "
            "unlocked and trusts this computer.", error=exc)) from exc
    finally:
        await lockdown.close()


async def enable_wifi_connection(udid: Optional[str] = None, enable: bool = True) -> str:
    """Turn wireless lockdown on, so the device stays reachable without a cable.

    Must be done over the cable: the setting is what makes the Wi-Fi transport
    exist in the first place. Afterwards usbmuxd advertises the device on the
    network whenever both are on the same Wi-Fi.
    """
    from pymobiledevice3.lockdown import create_using_usbmux

    try:
        lockdown = await create_using_usbmux(serial=udid, connection_type="USB")
    except Exception as exc:
        raise errors.NoDeviceError(
            t("A cable is required: wireless access can only be enabled over USB.\n"
              "Error: {error}", error=exc)
        ) from exc

    try:
        await lockdown.set_enable_wifi_connections(enable)
    except Exception as exc:
        raise errors.IosLocError(t(
            "Could not change the Wi-Fi connection setting: {error}", error=exc)) from exc
    finally:
        await lockdown.close()

    if not enable:
        return t("Wi-Fi access is off — cable only from now on.")
    return t(
        "Done. The iPhone is now reachable without a cable while it is on the same "
        "Wi-Fi network.\nUnplug the cable and press Refresh in the device list — it "
        "should appear marked Network.")


@dataclass
class DeviceLink:
    """An open location-override channel to one device.

    Use as an async context manager::

        async with DeviceLink(udid) as link:
            await link.set(55.751244, 37.618423)
            await link.clear()

    `udid` of ``None`` picks the first reachable device. USB is preferred when
    the same device is also reachable over Wi-Fi: it is faster and does not drop
    halfway through a route.
    """

    udid: Optional[str] = None
    allow_tunneld: bool = True
    enable_developer_mode: bool = False

    info: Optional[DeviceInfo] = field(default=None, init=False)
    transport: str = field(default="", init=False)
    #: "USB" or "Network" -- whichever the device actually answered on.
    connection_type: str = field(default="USB", init=False)
    _stack: Optional[AsyncExitStack] = field(default=None, init=False, repr=False)
    _backend: Any = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def __aenter__(self) -> "DeviceLink":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    @property
    def is_open(self) -> bool:
        return self._backend is not None

    async def open(self) -> "DeviceLink":
        """Connect, mount the DDI if needed, and open the location channel."""
        if self.is_open:
            return self
        stack = AsyncExitStack()
        try:
            self._backend = await self._build(stack)
        except BaseException:
            await stack.aclose()
            self._backend = None
            raise
        self._stack = stack
        return self

    async def close(self) -> None:
        """Tear the channel down. Does not clear an active override -- callers decide that."""
        self._backend = None
        stack, self._stack = self._stack, None
        if stack is not None:
            with suppress(Exception):
                await stack.aclose()

    async def set(self, latitude: float, longitude: float) -> None:
        """Report the device as being at these coordinates."""
        if self._backend is None:
            raise errors.NotConnectedError(t("No active connection to the device."))
        async with self._lock:
            await self._backend.set(latitude, longitude)

    async def clear(self) -> None:
        """Stop overriding and let the device report its real location again."""
        if self._backend is None:
            raise errors.NotConnectedError(t("No active connection to the device."))
        async with self._lock:
            await self._backend.clear()

    # ---------------------------------------------------------------- internals

    async def _connect_lockdown(self, create_using_usbmux) -> Any:
        """Open lockdown over USB, falling back to Wi-Fi.

        Both are the same protocol through usbmuxd; only the transport differs,
        so a wireless device behaves identically once connected -- just slower
        and easier to lose when the phone sleeps.
        """
        from pymobiledevice3.exceptions import (
            ConnectionFailedError,
            DeviceNotFoundError,
            NoDeviceConnectedError,
        )

        try:
            lockdown = await create_using_usbmux(serial=self.udid, connection_type="USB")
            self.connection_type = "USB"
            return lockdown
        except (NoDeviceConnectedError, DeviceNotFoundError, ConnectionFailedError):
            logger.info("no device on USB; trying Wi-Fi")

        lockdown = await create_using_usbmux(serial=self.udid, connection_type="Network")
        self.connection_type = "Network"
        logger.info("connected over Wi-Fi")
        return lockdown

    async def _build(self, stack: AsyncExitStack) -> Any:
        from pymobiledevice3.exceptions import (
            ConnectionFailedToUsbmuxdError,
            DeviceNotFoundError,
            FatalPairingError,
            InvalidHostIDError,
            NoDeviceConnectedError,
            NotPairedError,
            NotTrustedError,
            PairingDialogResponsePendingError,
            PasscodeRequiredError,
            UserDeniedPairingError,
        )
        from pymobiledevice3.lockdown import create_using_usbmux

        try:
            lockdown = await self._connect_lockdown(create_using_usbmux)
        except ConnectionFailedToUsbmuxdError as exc:
            raise errors.UsbmuxUnavailableError(t(
                "Apple Mobile Device service (usbmuxd) is unavailable. Install Apple Devices "
                "from the Microsoft Store or iTunes from apple.com, then make sure the Apple "
                "Mobile Device Service is running.")) from exc
        except (NoDeviceConnectedError, DeviceNotFoundError) as exc:
            raise errors.NoDeviceError(t(
                "No iPhone found. Connect it with a cable, unlock the screen and confirm "
                "\"Trust This Computer\".")) from exc
        except PairingDialogResponsePendingError as exc:
            raise errors.PairingRequiredError(t(
                "The \"Trust This Computer?\" prompt is open on the iPhone — confirm it and "
                "press Connect again.")) from exc
        except UserDeniedPairingError as exc:
            raise errors.PairingRequiredError(t(
                "You declined to trust this computer on the iPhone.\nUnplug the cable, "
                "plug it back in and choose Trust.")) from exc
        except PasscodeRequiredError as exc:
            raise errors.PairingRequiredError(t(
                "The iPhone is locked. Unlock the screen and connect again.")) from exc
        except (NotTrustedError, NotPairedError) as exc:
            raise errors.PairingRequiredError(t(
                "The iPhone does not trust this computer.\nUnplug the cable, plug it back "
                "in, unlock the screen and tap Trust.")) from exc
        except (InvalidHostIDError, FatalPairingError) as exc:
            raise errors.PairingRequiredError(t(
                "The stored pairing record no longer fits.\nOn the iPhone: Settings → "
                "General → Transfer or Reset iPhone → Reset → Reset Location & Privacy, "
                "then connect again.") + f"\n({exc})") from exc
        stack.push_async_callback(lockdown.close)

        version = Version(lockdown.product_version)
        values = lockdown.short_info
        self.info = DeviceInfo(
            udid=values.get("UniqueDeviceID") or lockdown.udid or "",
            name=values.get("DeviceName") or "iPhone",
            model=lockdown.display_name or values.get("ProductType") or "",
            product_type=values.get("ProductType") or "",
            ios_version=lockdown.product_version,
            connection_type=self.connection_type,
        )

        await self._ensure_developer_mode(lockdown, version)

        provider: Any = lockdown
        if version >= RSD_MIN_VERSION:
            provider = await self._open_tunnel(stack, lockdown)
        else:
            self.transport = "lockdown"

        await self._mount_developer_image(provider)
        return await self._open_location_channel(stack, provider)

    async def _ensure_developer_mode(self, lockdown: Any, version: Version) -> None:
        from pymobiledevice3.exceptions import DeviceHasPasscodeSetError

        if version < DEVELOPER_MODE_MIN_VERSION:
            return
        try:
            enabled = await lockdown.get_developer_mode_status()
        except Exception as exc:  # the query itself is best-effort on some builds
            logger.debug("developer mode query failed: %s", exc)
            return
        if self.info is not None:
            self.info.developer_mode = enabled
        if enabled:
            return
        if not self.enable_developer_mode:
            raise errors.DeveloperModeDisabledError(t(
                "Developer Mode is off on the iPhone; Apple will not start developer "
                "services without it.\nEnable it: Settings → Privacy & Security → "
                "Developer Mode. The iPhone will restart.\nIf that entry is missing, press "
                "\"I don't see Developer Mode\" in the device list to reveal it."))

        from pymobiledevice3.services.amfi import AmfiService

        logger.info("enabling developer mode on the device")
        try:
            await AmfiService(lockdown).enable_developer_mode()
        except DeviceHasPasscodeSetError as exc:
            raise errors.DeveloperModeDisabledError(t(
                "To enable Developer Mode automatically the iPhone must have no passcode "
                "set, or you can enable it by hand in Settings.")) from exc
        raise errors.DeviceRebootingError(t(
            "Developer Mode is being enabled — the iPhone is restarting. Confirm it on the "
            "device after it boots, then connect again."))

    async def _open_tunnel(self, stack: AsyncExitStack, lockdown: Any) -> Any:
        """Establish a RemoteXPC tunnel for iOS 17+, preferring the root-free path."""
        from pymobiledevice3.exceptions import PyMobileDevice3Exception
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

        udid = lockdown.udid
        tunnel = UserspaceRsdTunnel(serial=udid)
        try:
            rsd = await tunnel.aopen()
        except PyMobileDevice3Exception as exc:
            logger.info("userspace tunnel unavailable (%s); trying tunneld", exc)
            rsd = await self._connect_tunneld(udid)
            if rsd is None:
                raise errors.TunnelError(t(
                    "Could not establish the tunnel to the device (iOS {version}): {error}\n"
                    "If the iOS version is newer than the library, update it first:\n"
                    "    pip install -U pymobiledevice3\n"
                    "Workaround: run `pymobiledevice3 remote tunneld` in a separate window "
                    "as administrator and try again.",
                    version=lockdown.product_version, error=exc)) from exc
            stack.push_async_callback(rsd.close)
            self.transport = "tunneld"
            return rsd
        stack.push_async_callback(tunnel.aclose)
        self.transport = "userspace-tunnel"
        return rsd

    async def _connect_tunneld(self, udid: Optional[str]) -> Any:
        if not self.allow_tunneld or udid is None:
            return None
        from pymobiledevice3.tunneld.api import get_tunneld_device_by_udid

        try:
            return await get_tunneld_device_by_udid(udid)
        except Exception as exc:
            logger.debug("tunneld not reachable: %s", exc)
            return None

    async def _mount_developer_image(self, provider: Any) -> None:
        """Mount the Developer Disk Image, which developer services require."""
        from pymobiledevice3.exceptions import (
            AlreadyMountedError,
            DeveloperModeIsNotEnabledError,
            NotEnoughDiskSpaceError,
        )
        from pymobiledevice3.services.mobile_image_mounter import auto_mount

        try:
            await auto_mount(provider)
            logger.info("developer disk image mounted")
        except AlreadyMountedError:
            logger.debug("developer disk image already mounted")
        except DeveloperModeIsNotEnabledError as exc:
            raise errors.DeveloperModeDisabledError(t(
                "Developer Mode is off, so the device refused to mount the developer "
                "image.")) from exc
        except NotEnoughDiskSpaceError as exc:
            raise errors.MountError(t(
                "The iPhone does not have enough free space for the developer image."
            )) from exc
        except Exception as exc:
            raise errors.MountError(t(
                "Could not mount the Developer Disk Image: {error}\nUsually fixed by "
                "unlocking the iPhone, keeping the cable in, and checking your internet "
                "connection (the image is downloaded from Apple).\nIf the iOS version is "
                "very new, update the library: pip install -U pymobiledevice3",
                error=exc)) from exc

    async def _open_location_channel(self, stack: AsyncExitStack, provider: Any) -> Any:
        """Open the DVT location channel, falling back to the legacy lockdown service.

        DVT is preferred because the channel stays open: a route update is one
        message. `com.apple.dt.simulatelocation` opens a fresh service connection
        per coordinate, which is fine for a one-shot teleport but too costly to
        drive a moving route at 1 Hz.
        """
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        try:
            dvt = await stack.enter_async_context(DvtProvider(provider))
            backend = await stack.enter_async_context(LocationSimulation(dvt))
            logger.info("location channel open over DVT (%s)", self.transport or "lockdown")
            return backend
        except Exception as exc:
            logger.info("DVT location channel failed (%s); falling back to simulatelocation", exc)

        from pymobiledevice3.services.simulate_location import DtSimulateLocation

        try:
            backend = DtSimulateLocation(provider)
            await backend.clear()  # proves the service actually starts
            self.transport = f"{self.transport or 'lockdown'}+simulatelocation"
            return backend
        except Exception as exc:
            raise errors.LocationServiceError(t(
                "Could not open the location channel: {error}\nCheck that the iPhone is "
                "unlocked, Developer Mode is on and the developer image is mounted.",
                error=exc)) from exc
