"""Public ``Host`` factory entry point for the keeper measurement orchestrator."""

from __future__ import annotations

import logging
from enum import Enum

from idevice.host import config
from idevice.host.base.errors import HostNotSupportedError
from idevice.host.base.host import HostBase
from idevice.host.mac.host import MacHost

logger = logging.getLogger(__name__)


class Platform(Enum):
    """Supported host platforms (keeper runs on macOS only)."""

    MACOS = "macos"

    @classmethod
    def from_string(cls, platform: str) -> Platform:
        """Convert a string to a :class:`Platform`.

        Raises:
            HostNotSupportedError: If ``platform`` is not a supported host platform.
        """
        try:
            return cls(platform.lower())
        except ValueError as exc:
            raise HostNotSupportedError(
                f"Host feature is only supported on macOS, not: {platform}"
            ) from exc


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

        Args:
            platform: Target platform; only ``macos`` is supported.
            keeper_ip: Keeper control-server IP.
            device_udid: Target device UDID.
            device_ip: Target device IP.
            keeper_port: Keeper control-server port.
            keeper_id: Optional keeper/controller id (informational).

        Returns:
            HostBase: The platform-specific host implementation.

        Raises:
            HostNotSupportedError: If ``platform`` is unsupported.
        """
        p = Platform.from_string(platform)
        logger.debug(f"Creating host for platform={p} device_udid={device_udid}")
        if p is Platform.MACOS:
            host: HostBase = MacHost(
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                device_udid=device_udid,
                device_ip=device_ip,
                keeper_id=keeper_id,
            )
        else:  # pragma: no cover - Platform.from_string already guards this
            raise HostNotSupportedError(f"Unsupported host platform: {platform}")
        logger.info(f"Created {type(host).__name__} for device_udid={host.device_udid}")
        return host

    @classmethod
    def from_env(cls) -> HostBase:
        """Build a :class:`MacHost` from the ``GAUTO_*`` environment variables.

        Reads keeper and device configuration injected by the controller (see
        ``controller/src/worker/engine.rs``) via :mod:`idevice.host.config`.

        Returns:
            HostBase: A host bound to the environment-provided keeper and device.
        """
        return cls.create(
            Platform.MACOS.value,
            keeper_ip=config.keeper_ip(),
            keeper_port=config.keeper_port(),
            device_udid=config.device_udid(),
            device_ip=config.device_ip(),
            keeper_id=config.keeper_id(),
        )
