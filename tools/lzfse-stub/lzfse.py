"""No-op stub for the `lzfse` C extension.

`ipsw_parser` (a transitive dependency of pymobiledevice3) requires `lzfse`,
whose real implementation is a C extension with no prebuilt wheels and so needs
MSVC Build Tools. LZFSE is used exclusively for decompressing IPSW firmware
payloads during `pymobiledevice3 restore`, which ios-loc never touches.

Installing this stub satisfies the dependency. Any actual call raises, so a
code path that really needs LZFSE fails loudly instead of silently corrupting
data.
"""

__version__ = '0.4.2'

_MSG = (
    'lzfse is a stub in this environment (no MSVC toolchain). Install the real '
    'lzfse package to use IPSW/restore features.'
)


def decode_buffer(*_args, **_kwargs):
    raise NotImplementedError(_MSG)


def encode_buffer(*_args, **_kwargs):
    raise NotImplementedError(_MSG)
