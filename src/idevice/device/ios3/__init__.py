"""iOS ``DeviceBase`` implementation backed by pymobiledevice3."""

from idevice.device.cache import InstalledAppCache
from idevice.device.ios3.device import IOSDevice3, IOSDevice3Error

__all__ = ["IOSDevice3", "IOSDevice3Error", "InstalledAppCache"]
