"""iOS ``DeviceBase`` implementation."""

from idevice.device.cache import InstalledAppCache
from idevice.device.ios.device import IOSDevice, IOSDeviceError

__all__ = ["IOSDevice", "IOSDeviceError", "InstalledAppCache"]
