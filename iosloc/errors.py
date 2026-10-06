"""Exception types, each carrying a message meant to be shown to the user.

Every message says what to do next, because most failures here are setup
problems on the phone or on Windows rather than bugs in the code.
"""

from __future__ import annotations

__all__ = [
    "DeveloperModeDisabledError",
    "DeviceRebootingError",
    "IosLocError",
    "LocationServiceError",
    "MountError",
    "NoDeviceError",
    "NotConnectedError",
    "PairingRequiredError",
    "TunnelError",
    "UsbmuxUnavailableError",
]


class IosLocError(Exception):
    """Base class for every error ios-loc raises on purpose."""


class UsbmuxUnavailableError(IosLocError):
    """Apple Mobile Device Service / usbmuxd is not reachable on this machine."""


class NoDeviceError(IosLocError):
    """No device is attached over USB."""


class NotConnectedError(IosLocError):
    """An operation needs an open session, and there is none."""


class PairingRequiredError(IosLocError):
    """The device has not trusted this computer, or the pairing record is stale."""


class DeveloperModeDisabledError(IosLocError):
    """iOS 16+ requires Developer Mode to be enabled on the device."""


class DeviceRebootingError(IosLocError):
    """The device was told to reboot (enabling Developer Mode); retry afterwards."""


class TunnelError(IosLocError):
    """The iOS 17+ RemoteXPC tunnel could not be established."""


class MountError(IosLocError):
    """The Developer Disk Image could not be mounted."""


class LocationServiceError(IosLocError):
    """The location-simulation channel could not be opened or failed mid-use."""
