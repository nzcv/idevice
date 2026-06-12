"""Public ``Device`` factory entry point for all platforms."""

from __future__ import annotations

import logging
from enum import Enum

from idevice.device.android.device import AndroidDevice
from idevice.device.base.device import DeviceBase
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3
from idevice.device.windows.device import WindowsDevice

logger = logging.getLogger(__name__)


class Platform(Enum):
    """Supported device platforms."""

    IOS = "ios"
    IOS3 = "ios3"
    ANDROID = "android"
    WINDOWS = "windows"

    @classmethod
    def from_string(cls, platform: str) -> Platform:
        """Convert a string to a Platform enum value."""
        try:
            return cls(platform.lower())  # type: ignore
        except ValueError:
            raise ValueError(f"Invalid platform: {platform}") from ValueError


class Device:
    """Factory facade that builds platform-specific :class:`DeviceBase` instances."""

    @classmethod
    def create(
        cls,
        platform: str,
        *,
        device_id: str,
        device_ip: str,
    ) -> DeviceBase:
        """Create a device instance bound to ``device_id`` for ``platform``.

        Args:
            platform: Target platform (``ios``, ``ios3``, ``android`` or
                ``windows``), as a :class:`Platform` member or its string value.
            device_id: Device id (UDID / serial). Required and non-empty.
            device_ip: Device IP address, or an empty string when not applicable.

        Returns:
            DeviceBase: The platform-specific device implementation.

        Raises:
            ValueError: If ``platform`` is unsupported or ``device_id`` is empty.
        """
        p = Platform.from_string(platform)
        logger.debug(f"Creating device for platform={p} device_id={device_id}")
        if p  is Platform.IOS:
            device: DeviceBase = IOSDevice(device_id, device_ip=device_ip)
        elif p is Platform.IOS3:
            device = IOSDevice3(device_id, device_ip=device_ip)
        elif p is Platform.ANDROID:
            device = AndroidDevice(device_id, device_ip=device_ip)
        elif p is Platform.WINDOWS:
            device = WindowsDevice(device_id, device_ip=device_ip)
        else:
            raise ValueError(f"Unsupported platform: {platform}")
        logger.info(f"Created {type(device).__name__} for device_id={device.device_id}")
        return device
