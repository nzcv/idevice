"""Public ``Device`` entry point for all platforms."""

from __future__ import annotations

import logging
from enum import Enum

from idevice.device import config
from idevice.device.android.device import AndroidDevice
from idevice.device.base.device import DeviceBase
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3
from idevice.device.windows.device import WindowsDevice

logger = logging.getLogger(__name__)

_LOG_TAG = "[Device]"


class Platform(Enum):
    """Supported device platforms."""

    IOS = "_ios"
    IOS3 = "ios"
    ANDROID = "android"
    WINDOWS = "windows"

    @classmethod
    def from_string(cls, platform: str) -> Platform:
        """Convert a string to a Platform enum value."""
        try:
            return cls(platform.lower())  # type: ignore
        except ValueError:
            raise ValueError(f"Invalid platform: {platform}") from ValueError


class _DeviceMeta(type):
    """Metaclass exposing the last-built device via the ``Device.Instance`` property."""

    @property
    def Instance(cls) -> DeviceBase:
        """Return the most recently built :class:`DeviceBase` for quick access.

        Raises:
            RuntimeError: If no device has been built yet (call
                :meth:`Device.create` / :meth:`Device.from_env` first).
        """
        if cls._instance is None:
            raise RuntimeError(
                f"{_LOG_TAG} no Device instance has been created yet; "
                f"call Device.create(...) or Device.from_env() first"
            )
        return cls._instance


class Device(metaclass=_DeviceMeta):
    """Build a platform-specific :class:`DeviceBase` and expose it as a singleton.

    The most recently built device is cached and reachable anywhere via
    :attr:`Device.Instance`. Use :meth:`reset` to drop the cached instance
    (mainly for tests).
    """

    _instance: DeviceBase | None = None

    @classmethod
    def reset(cls) -> None:
        """Drop the cached device so the next build rebinds :attr:`Device.Instance`."""
        cls._instance = None

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
        cls._instance = device
        return device

    @classmethod
    def from_env(cls) -> DeviceBase:
        """Build a device from the ``GAUTO_*`` environment variables.

        Reads ``GAUTO_PLATFORM``, ``GAUTO_DEVICE_UDID`` and ``GAUTO_DEVICE_IP``.

        Returns:
            DeviceBase: The platform-specific device implementation.

        Raises:
            ValueError: If the platform is unsupported or ``device_id`` is empty.
        """
        return cls.create(
            platform=config.platform(),
            device_id=config.device_id(),
            device_ip=config.device_ip(),
        )
