"""iOS device backend powered by the Rust ``ios4`` CLI."""

from idevice.device.ios4.device import IOSDevice4, IOSDevice4Error

__all__ = ["IOSDevice4", "IOSDevice4Error"]
