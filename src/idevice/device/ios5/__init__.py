"""iOS device backend powered by Apple's ``xcrun devicectl`` CLI."""

from idevice.device.ios5.device import IOSDevice5, IOSDevice5Error
from idevice.device.ios5.devicectl import Devicectl

__all__ = ["Devicectl", "IOSDevice5", "IOSDevice5Error"]
