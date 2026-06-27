"""Public ``Host`` entry point for keeper-backed measurement orchestration.

Use :meth:`Host.create` / :meth:`Host.from_env` to build a host: ``macos``
yields a real :class:`~idevice.host.mac.host.MacHost`; every other platform
yields a no-op :class:`~idevice.host.dummy.host.DummyHost` so the controller can
drive any platform without special-casing it.
"""

from __future__ import annotations

import logging
from enum import Enum

from idevice.host import config
from idevice.host.base.host import HostBase
from idevice.host.dummy.host import DummyHost
from idevice.host.mac.host import MacHost

logger = logging.getLogger(__name__)

_LOG_TAG = "[Host]"


class Platform(Enum):
    """Supported host platforms."""

    MACOS = "macos"
    IOS = "ios"
    ANDROID = "android"
    WINDOWS = "windows"

    @classmethod
    def from_string(cls, platform: str) -> Platform:
        """Convert a string to a Platform enum value."""
        try:
            return cls(platform.lower())  # type: ignore
        except ValueError:
            raise ValueError(f"Invalid platform: {platform}") from ValueError


class _HostMeta(type):
    """Metaclass exposing the last-built host via the ``Host.Instance`` property."""

    @property
    def Instance(cls) -> HostBase:
        """Return the most recently built host for quick access.

        The instance is a real :class:`~idevice.host.mac.host.MacHost` when
        one was built, or a no-op :class:`~idevice.host.dummy.host.DummyHost`
        when :meth:`Host.from_env` could not bind a host.

        Raises:
            RuntimeError: If no host has been built yet (call
                :meth:`Host.create` / :meth:`Host.from_env` first).
        """
        if cls._instance is None:
            raise RuntimeError(
                f"{_LOG_TAG} no Host instance has been created yet; "
                f"call Host.create(...) or Host.from_env() first"
            )
        return cls._instance


class Host(metaclass=_HostMeta):
    """Build a platform-specific :class:`HostBase` and expose it as a singleton.

    The most recently built host is cached and reachable anywhere via
    :attr:`Host.Instance`. Use :meth:`reset` to drop the cached instance
    (mainly for tests).
    """

    _instance: HostBase | None = None

    @classmethod
    def reset(cls) -> None:
        """Drop the cached host so the next build rebinds :attr:`Host.Instance`."""
        cls._instance = None

    @classmethod
    def create(
        cls,
        *,
        platform: str,
        keeper_ip: str,
        device_udid: str,
        device_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        keeper_id: str = "",
        bundle_id: str,
    ) -> HostBase:
        """Build a host for ``platform``: ``macos`` -> real host, else dummy.

        Args:
            platform: Target platform (``macos`` runs the keeper-backed host;
                every other value resolves to a :class:`DummyHost`).
            keeper_ip: EndlessKeeper control-server IP.
            device_udid: Target device UDID.
            device_ip: Target device IP.
            keeper_port: Keeper control-server port.
            keeper_id: Optional keeper/controller id (informational).
            bundle_id: Target app bundle identifier.

        Returns:
            HostBase: The platform-specific host implementation.

        Raises:
            ValueError: If ``platform`` is unsupported, or (for ``macos``) a
                required coordinate is empty.
        """
        p = Platform.from_string(platform)
        logger.debug(f"{_LOG_TAG} create platform={p} device_udid={device_udid}")
        if p is Platform.MACOS:
            host: HostBase = MacHost(
                platform=platform,
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                keeper_id=keeper_id,
                device_udid=device_udid,
                device_ip=device_ip,
                bundle_id=bundle_id,
            )
        else:
            host = DummyHost(
                f"unsupported platform: {platform}",
                platform=p.value,
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                device_udid=device_udid,
                device_ip=device_ip,
                bundle_id=bundle_id,
                keeper_id=keeper_id,
            )
        logger.info(f"{_LOG_TAG} created {type(host).__name__} for device_udid={host.device_udid}")
        cls._instance = host
        return host

    @classmethod
    def from_env(cls) -> HostBase:
        """Build a host from the ``GAUTO_*`` environment variables.

        Reads ``GAUTO_PLATFORM``, ``GAUTO_HOST_IP``, ``GAUTO_HOST_PORT``,
        ``GAUTO_HOST_ID``, ``GAUTO_DEVICE_UDID``, ``GAUTO_DEVICE_IP`` and
        ``GAUTO_BUNDLE_ID``.

        Unlike :meth:`create`, this never raises on a missing/blank environment:
        a non-macOS platform, an unsupported platform, or a missing required
        coordinate logs the reason and returns a no-op :class:`DummyHost`. The
        result (real or dummy) is bound as :attr:`Host.Instance`, so callers can
        always reach it there.

        Returns:
            HostBase: The platform-specific host, or a no-op :class:`DummyHost`
            whose every operation reports unhealthy and returns an inert default.
        """
        platform = config.host_platform()
        keeper_ip = config.keeper_ip()
        keeper_port = config.keeper_port()
        keeper_id = config.keeper_id()
        device_udid = config.device_udid()
        device_ip = config.device_ip()
        bundle_id = config.bundle_id()

        try:
            p = Platform.from_string(platform)
        except ValueError:
            return cls._bind_dummy(
                f"invalid platform: {platform!r}",
                platform, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

        if p is not Platform.MACOS:
            return cls._bind_dummy(
                f"unsupported platform: {platform}",
                platform, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

        missing = [
            name
            for name, value in (
                ("GAUTO_HOST_IP", keeper_ip),
                ("GAUTO_DEVICE_UDID", device_udid),
                ("GAUTO_DEVICE_IP", device_ip),
                ("GAUTO_BUNDLE_ID", bundle_id),
            )
            if not value
        ]
        if missing:
            return cls._bind_dummy(
                f"missing/blank env var(s): {', '.join(missing)}",
                platform, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

        try:
            return cls.create(
                platform=platform,
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                keeper_id=keeper_id,
                device_udid=device_udid,
                device_ip=device_ip,
                bundle_id=bundle_id,
            )
        except ValueError as exc:
            return cls._bind_dummy(
                f"invalid env configuration: {exc}",
                platform, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

    @classmethod
    def _bind_dummy(
        cls,
        reason: str,
        platform: str,
        keeper_ip: str,
        keeper_port: int,
        device_udid: str,
        device_ip: str,
        bundle_id: str,
        keeper_id: str,
    ) -> HostBase:
        """Bind a no-op :class:`DummyHost` as the current instance and return it."""
        host = DummyHost(
            reason,
            platform=platform,
            keeper_ip=keeper_ip,
            keeper_port=keeper_port,
            device_udid=device_udid,
            device_ip=device_ip,
            bundle_id=bundle_id,
            keeper_id=keeper_id,
        )
        cls._instance = host
        return host
