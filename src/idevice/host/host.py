"""Public ``Host`` factory entry point for the keeper measurement orchestrator."""

from __future__ import annotations

import logging
from enum import Enum

from idevice.host import config
from idevice.host.base.host import HostBase
from idevice.host.dummy.host import DummyHost
from idevice.host.mac.host import MacHost

logger = logging.getLogger(__name__)


class Platform(Enum):
    """Host platforms the controller can run on.

    Only :attr:`MACOS` runs the keeper-backed :class:`~idevice.host.mac.host.MacHost`;
    every other platform resolves to a no-op
    :class:`~idevice.host.dummy.host.DummyHost`.
    """

    MACOS = "macos"
    IOS = "ios"
    ANDROID = "android"
    WINDOWS = "windows"

    @classmethod
    def is_macos(cls, platform: str) -> bool:
        """Return ``True`` when ``platform`` is the keeper-backed macOS host."""
        return platform.lower() == cls.MACOS.value


class Host:
    """Factory facade that builds platform-specific :class:`HostBase` instances."""

    @classmethod
    def create(
        cls,
        platform: str,
        *,
        keeper_ip: str,
        device_udid: str,
        device_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        keeper_id: str = "",
    ) -> HostBase:
        """Create a host instance bound to a keeper and a device.

        The keeper-backed workflow only runs on macOS, so ``macos`` builds a
        :class:`~idevice.host.mac.host.MacHost`. Every other platform builds a
        no-op :class:`~idevice.host.dummy.host.DummyHost` instead of raising.

        Args:
            platform: Target platform (``macos`` runs the keeper; anything else
                resolves to a dummy host).
            keeper_ip: Keeper control-server IP.
            device_udid: Target device UDID.
            device_ip: Target device IP.
            keeper_port: Keeper control-server port.
            keeper_id: Optional keeper/controller id (informational).

        Returns:
            HostBase: The platform-specific host implementation.
        """
        logger.debug(f"Creating host for platform={platform} device_udid={device_udid}")
        if Platform.is_macos(platform):
            host: HostBase = MacHost(
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                device_udid=device_udid,
                device_ip=device_ip,
                keeper_id=keeper_id,
            )
        else:
            host = DummyHost(
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                device_udid=device_udid,
                device_ip=device_ip,
                platform=platform.lower(),
                keeper_id=keeper_id,
            )
        logger.info(f"Created {type(host).__name__} for device_udid={host.device_udid}")
        return host

    @classmethod
    def from_env(cls) -> HostBase:
        """Build a host from the ``GAUTO_*`` environment variables.

        Reads the platform (``GAUTO_PLATFORM``) plus keeper and device
        configuration injected by the controller (see
        ``controller/src/worker/engine.rs``) via :mod:`idevice.host.config`. A
        ``macos`` platform yields a :class:`~idevice.host.mac.host.MacHost`;
        every other platform yields a :class:`~idevice.host.dummy.host.DummyHost`.

        Returns:
            HostBase: A host bound to the environment-provided keeper and device.
        """
        return cls.create(
            config.host_platform(),
            keeper_ip=config.keeper_ip(),
            keeper_port=config.keeper_port(),
            device_udid=config.device_udid(),
            device_ip=config.device_ip(),
            keeper_id=config.keeper_id(),
        )
