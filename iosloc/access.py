"""Phone access: serving the panel on the local network, behind a token.

By default the server binds the loopback interface and needs no authentication,
because only this machine can reach it. Driving the panel from the phone needs
the opposite -- a reachable address -- and that changes the threat model
completely: anything on the same Wi-Fi could otherwise move the device's
location at will, including from a cafe or dorm network.

So LAN mode is never implicit. It is switched on explicitly, and when it is, a
random token is required on every request. The token is printed on the console
and encoded in a QR code, so the phone joins by scanning rather than by typing.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
from typing import Optional

__all__ = ["generate_token", "local_addresses", "primary_address", "qr_ascii", "qr_png"]

#: Short enough to retype, long enough that guessing it over a LAN is hopeless.
TOKEN_LENGTH = 10
#: Avoids characters that are easy to misread aloud or in a terminal font.
TOKEN_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def generate_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def local_addresses() -> list[str]:
    """Every IPv4 address of this machine that a phone could plausibly reach."""
    found: set[str] = set()

    # The usual trick: a UDP socket "connected" to an outside address reveals
    # which local interface would carry the traffic, without sending anything.
    for probe_target in ("8.8.8.8", "1.1.1.1"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.connect((probe_target, 80))
                found.add(probe.getsockname()[0])
            except OSError:
                continue

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass

    usable = []
    for address in found:
        try:
            parsed = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            continue
        usable.append(address)
    usable.sort(key=_address_rank)
    return usable


def _address_rank(address: str) -> tuple[int, str]:
    """Order addresses by how likely a phone on the house Wi-Fi is to reach them.

    192.168/16 is what home routers hand out, so it goes first. The machine's
    default route often belongs to a VPN or a virtual adapter instead (Hyper-V,
    WSL, Docker), and those are unreachable from the phone even though they look
    perfectly private -- which is why plain "is_private" is not enough.
    """
    parsed = ipaddress.IPv4Address(address)
    if address.startswith("192.168."):
        return (0, address)
    if parsed in ipaddress.ip_network("172.16.0.0/12"):
        return (1, address)
    if address.startswith("10."):
        return (2, address)
    return (3 if parsed.is_private else 4, address)


def primary_address() -> Optional[str]:
    addresses = local_addresses()
    return addresses[0] if addresses else None


def qr_ascii(data: str) -> Optional[str]:
    """The URL as a QR code drawn with block characters, for the console."""
    try:
        import io

        import qrcode
    except Exception:
        return None
    try:
        code = qrcode.QRCode(border=1)
        code.add_data(data)
        code.make(fit=True)
        buffer = io.StringIO()
        code.print_ascii(out=buffer, invert=True)
        return buffer.getvalue()
    except Exception:
        return None


def qr_png(data: str) -> Optional[bytes]:
    """The URL as a PNG QR code, for showing inside the panel."""
    try:
        import io

        import qrcode
    except Exception:
        return None
    try:
        image = qrcode.make(data)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None
