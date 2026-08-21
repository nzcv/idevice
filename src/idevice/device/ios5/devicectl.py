"""Compatibility exports for the shared ``xcrun devicectl`` wrapper."""

from idevice.device.common.xcruncli import (
    Devicectl,
    DevicectlOutcome,
    IOSDevice5Error,
    XcrunCLI,
    XcrunCLIError,
)

__all__ = [
    "Devicectl",
    "DevicectlOutcome",
    "IOSDevice5Error",
    "XcrunCLI",
    "XcrunCLIError",
]
