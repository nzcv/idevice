"""Public API for ``DeviceBase`` and platform-specific device implementations."""

from idevice.device.android.device import AndroidDevice
from idevice.device.base.device import DeviceBase
from idevice.device.base.errors import (
    AppNotInstalledError,
    CommandExecutionError,
    DeviceError,
    DeviceNotFoundError,
)
from idevice.device.base.runner import CommandResult, SubprocessRunner
from idevice.device.device import Device, Platform
from idevice.device.factory import create_device
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3, IOSDevice3Error
from idevice.device.windows.device import WindowsDevice

__all__ = [
    "AndroidDevice",
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
    "Platform",
    "SubprocessRunner",
    "WindowsDevice",
    "create_device",
]
