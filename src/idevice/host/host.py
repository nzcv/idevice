"""Public ``Host`` entry point for keeper-backed measurement orchestration.

Use :meth:`Host.create` / :meth:`Host.from_env` to build a host: ``macos``
yields a real :class:`~idevice.host.mac.host.MacHost`, ``windows`` yields a real
:class:`~idevice.host.win.host.WindowsHost`; every other host type yields a no-op
:class:`~idevice.host.dummy.host.DummyHost` so the controller can drive any host
type without special-casing it.
"""

from __future__ import annotations

import logging
from enum import Enum

from idevice.host import config
from idevice.host.base.host import HostBase
from idevice.host.dummy.host import DummyHost
from idevice.host.mac.host import MacHost
from idevice.host.win.host import WindowsHost

logger = logging.getLogger(__name__)

_LOG_TAG = "[Host]"


class HostType(Enum):
    """Supported host types."""

    MACOS = "macos"
    IOS = "ios"
    ANDROID = "android"
    WINDOWS = "windows"

    @classmethod
    def from_string(cls, host_type: str) -> HostType:
        """Convert a string to a HostType enum value."""
        try:
            return cls(host_type.lower())  # type: ignore
        except ValueError:
            raise ValueError(f"Invalid host type: {host_type}") from ValueError


class _HostMeta(type):
    """Metaclass exposing the last-built host via the ``Host.Instance`` property."""

    @property
    def Instance(cls) -> HostBase:
        """Return the most recently built host for quick access.

        The instance is a real host (:class:`~idevice.host.mac.host.MacHost` or
        :class:`~idevice.host.win.host.WindowsHost`) when one was built, or a
        no-op :class:`~idevice.host.dummy.host.DummyHost` when
        :meth:`Host.from_env` could not bind a host.

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
    """Build a host-type-specific :class:`HostBase` and expose it as a singleton.

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
        host_type: str,
        keeper_ip: str,
        device_udid: str,
        device_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        keeper_id: str = "",
        bundle_id: str,
    ) -> HostBase:
        """Build a host for ``host_type``: ``macos``/``windows`` -> real, else dummy.

        Args:
            host_type: Target host type (``macos`` and ``windows`` run the
                keeper-backed host; every other value resolves to a
                :class:`DummyHost`).
            keeper_ip: EndlessKeeper control-server IP.
            device_udid: Target device UDID.
            device_ip: Target device IP.
            keeper_port: Keeper control-server port.
            keeper_id: Optional keeper/controller id (informational).
            bundle_id: Target app bundle identifier.

        Returns:
            HostBase: The host-type-specific host implementation.

        Raises:
            ValueError: If ``host_type`` is unsupported, or (for ``macos`` /
                ``windows``) a required coordinate is empty.
        """
        h = HostType.from_string(host_type)
        logger.debug(f"{_LOG_TAG} create host_type={h} device_udid={device_udid}")
        if h is HostType.MACOS:
            host: HostBase = MacHost(
                host_type=host_type,
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                keeper_id=keeper_id,
                device_udid=device_udid,
                device_ip=device_ip,
                bundle_id=bundle_id,
            )
        elif h is HostType.WINDOWS:
            host = WindowsHost(
                host_type=host_type,
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                keeper_id=keeper_id,
                device_udid=device_udid,
                device_ip=device_ip,
                bundle_id=bundle_id,
            )
        else:
            host = DummyHost(
                f"unsupported host type: {host_type}",
                host_type=h.value,
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

        Reads ``GAUTO_HOST_TYPE``, ``GAUTO_HOST_IP``, ``GAUTO_HOST_PORT``,
        ``GAUTO_HOST_ID``, ``GAUTO_DEVICE_UDID``, ``GAUTO_DEVICE_IP`` and
        ``GAUTO_PACKAGE_NAME``.

        Unlike :meth:`create`, this never raises on a missing/blank environment:
        an unsupported host type (anything other than ``macos`` / ``windows``)
        or a missing required coordinate logs the reason and returns a no-op
        :class:`DummyHost`. The result (real or dummy) is bound as
        :attr:`Host.Instance`, so callers can always reach it there.

        Returns:
            HostBase: The host-type-specific host, or a no-op :class:`DummyHost`
            whose every operation reports unhealthy and returns an inert default.
        """
        host_type = config.host_type()
        keeper_ip = config.keeper_ip()
        keeper_port = config.keeper_port()
        keeper_id = config.keeper_id()
        device_udid = config.device_udid()
        device_ip = config.device_ip()
        bundle_id = config.bundle_id()

        try:
            h = HostType.from_string(host_type)
        except ValueError:
            return cls._bind_dummy(
                f"invalid host type: {host_type!r}",
                host_type, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

        if h not in (HostType.MACOS, HostType.WINDOWS):
            return cls._bind_dummy(
                f"unsupported host type: {host_type}",
                host_type, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

        missing = [
            name
            for name, value in (
                ("GAUTO_HOST_IP", keeper_ip),
                ("GAUTO_DEVICE_UDID", device_udid),
                ("GAUTO_DEVICE_IP", device_ip),
                ("GAUTO_PACKAGE_NAME", bundle_id),
            )
            if not value
        ]
        if missing:
            return cls._bind_dummy(
                f"missing/blank env var(s): {', '.join(missing)}",
                host_type, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

        try:
            return cls.create(
                host_type=host_type,
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
                host_type, keeper_ip, keeper_port, device_udid, device_ip, bundle_id, keeper_id,
            )

    @classmethod
    def _bind_dummy(
        cls,
        reason: str,
        host_type: str,
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
            host_type=host_type,
            keeper_ip=keeper_ip,
            keeper_port=keeper_port,
            device_udid=device_udid,
            device_ip=device_ip,
            bundle_id=bundle_id,
            keeper_id=keeper_id,
        )
        cls._instance = host
        return host
