"""Public API for ``DeviceBase`` and platform-specific device implementations."""

from idevice.device.android.device import AndroidDevice
from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import (
    AppNotInstalledError,
    CommandExecutionError,
    DeviceError,
    DeviceNotFoundError,
)
from idevice.device.base.runner import CommandResult, SubprocessRunner
from idevice.device.device import Device, Platform
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3, IOSDevice3Error
from idevice.device.ios4.device import IOSDevice4, IOSDevice4Error
from idevice.device.ios5.device import IOSDevice5, IOSDevice5Error
from idevice.device.ios6.device import IOSDevice6, IOSDevice6Error
from idevice.device.windows.device import WindowsDevice

__all__ = [
    "AndroidDevice",
    "AppDataPath",
    "AppNotInstalledError",
    "CommandExecutionError",
    "CommandResult",
    "DeviceError",
    "DeviceNotFoundError",
    "Device",
    "DeviceBase",
    "IOSDevice",
    "IOSDevice3",
    "IOSDevice3Error",
    "IOSDevice4",
    "IOSDevice4Error",
    "IOSDevice5",
    "IOSDevice5Error",
    "IOSDevice6",
    "IOSDevice6Error",
    "Platform",
    "SubprocessRunner",
    "WindowsDevice",
]
