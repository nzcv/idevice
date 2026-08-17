"""iOS device backend powered by Apple's ``xcrun devicectl`` CLI."""

from idevice.device.ios5.device import IOSDevice5, IOSDevice5Error

__all__ = ["IOSDevice5", "IOSDevice5Error"]
