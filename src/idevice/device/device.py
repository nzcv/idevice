"""Public ``Device`` entry point for all platforms."""

from __future__ import annotations

import logging
from enum import Enum

from idevice.device import config
from idevice.device.android.device import AndroidDevice
from idevice.device.base.device import DeviceBase
from idevice.device.dummy.device import DummyDevice
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
    WINDOWS = "pc"

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
        """Return the most recently built device for quick access.

        The instance is a real :class:`DeviceBase` when one was built, or a
        no-op :class:`DummyDevice` when :meth:`Device.from_env` could not bind a
        device (every :class:`DummyDevice` operation logs an error and returns).

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
        company_name: str = "",
        package_name: str = "",
    ) -> DeviceBase:
        """Create a device instance bound to ``device_id`` for ``platform``.

        Args:
            platform: Target platform (``ios``, ``ios3``, ``android`` or
                ``windows``), as a :class:`Platform` member or its string value.
            device_id: Device id (UDID / serial). Required and non-empty.
            device_ip: Device IP address, or an empty string when not applicable.
            company_name: Windows-only publisher folder under ``%LocalAppData%``.
            package_name: Windows-only package id used to resolve the documents root.

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
            device = WindowsDevice(
                device_id,
                device_ip=device_ip,
                company_name=company_name,
                package_name=package_name,
            )
        else:
            raise ValueError(f"Unsupported platform: {platform}")
        logger.info(f"Created {type(device).__name__} for device_id={device.device_id}")
        cls._instance = device
        return device

    @classmethod
    def from_env(cls) -> DeviceBase:
        """Build a device from the ``GAUTO_*`` environment variables.

        Reads ``GAUTO_PLATFORM``, ``GAUTO_DEVICE_UDID``, ``GAUTO_DEVICE_IP``,
        and on Windows also ``GAUTO_COMPANY_NAME`` / ``GAUTO_PACKAGE_NAME``.

        Unlike :meth:`create`, this never raises on a missing/blank environment:
        when required ``GAUTO_*`` variables are empty (or the platform is
        unsupported) it logs the reason and returns a no-op
        :class:`DummyDevice`. ``GAUTO_DEVICE_IP`` may be empty because not all
        platforms use it. The result (real or dummy) is bound as
        :attr:`Device.Instance`, so callers can always reach it there.

        Returns:
            DeviceBase: The platform-specific device, or a no-op
            :class:`DummyDevice` whose every operation logs an error and returns
            an inert default.
        """
        platform = config.platform()
        device_id = config.device_id()
        device_ip = config.device_ip()
        company_name = config.company_name()
        package_name = config.package_name()
        required_env: list[tuple[str, str]] = [
            ("GAUTO_PLATFORM", platform),
            ("GAUTO_DEVICE_UDID", device_id),
        ]
        if platform.lower() in {Platform.WINDOWS.value, "windows"}:
            required_env.extend(
                [
                    ("GAUTO_COMPANY_NAME", company_name),
                    ("GAUTO_PACKAGE_NAME", package_name),
                ]
            )
        missing = [name for name, value in required_env if not value]
        if missing:
            reason = f"missing/blank env var(s): {', '.join(missing)}"
            return cls._bind_dummy(reason, platform, device_id, device_ip)

        try:
            return cls.create(
                platform=platform,
                device_id=device_id,
                device_ip=device_ip,
                company_name=company_name,
                package_name=package_name,
            )
        except ValueError as exc:
            return cls._bind_dummy(
                f"invalid env configuration: {exc}", platform, device_id, device_ip
            )

    @classmethod
    def _bind_dummy(
        cls, reason: str, platform: str, device_id: str, device_ip: str
    ) -> DeviceBase:
        """Bind a no-op :class:`DummyDevice` as the current instance and return it."""
        device = DummyDevice(
            reason, platform=platform, device_id=device_id, device_ip=device_ip
        )
        cls._instance = device
        return device
