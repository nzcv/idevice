"""Abstract ``HostBase`` for keeper-backed measurement orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod

from idevice.host import config
from idevice.host.base.runner import Runner


class HostBase(ABC):
    """Drive a measurement run on one device via a keeper + on-device runner.

    A host instance is always bound to a single keeper control server and a
    single target device (``device_udid`` / ``device_ip``). Concrete
    implementations talk to the EndlessKeeper control server
    (:class:`~idevice.host.base.keeper.Keeper`) and the on-device
    RemoteControlTest runner (:class:`~idevice.host.base.runner.Runner`).
    """

    def __init__(
        self,
        platform: str,
        *,
        keeper_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        device_udid: str,
        device_ip: str,
        bundle_id: str,
        keeper_id: str = "",
    ) -> None:
        """Bind the host to a keeper control server and a target device.

        Args:
            platform: Platform identifier (e.g. ``macos``).
            keeper_ip: EndlessKeeper control-server IP. Required and non-empty.
            keeper_port: Keeper control-server port.
            device_udid: Target device UDID. Required and non-empty.
            device_ip: Target device IP. Required and non-empty.
            bundle_id: Target app bundle identifier. Required and non-empty.
            keeper_id: Optional keeper/controller id (informational).

        Raises:
            ValueError: If ``keeper_ip``, ``device_udid``, ``device_ip`` or
                ``bundle_id`` is empty.
        """
        if not keeper_ip:
            raise ValueError("keeper_ip is required and must be a non-empty string")
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        if not device_ip:
            raise ValueError("device_ip is required and must be a non-empty string")
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        self._platform = platform
        self._keeper_ip = keeper_ip
        self._keeper_port = int(keeper_port)
        self._device_udid = device_udid
        self._device_ip = device_ip
        self._bundle_id = bundle_id
        self._keeper_id = keeper_id

    @property
    def platform(self) -> str:
        """Platform bound to this instance."""
        return self._platform

    @property
    def keeper_ip(self) -> str:
        """EndlessKeeper control-server IP bound to this instance."""
        return self._keeper_ip

    @property
    def keeper_port(self) -> int:
        """EndlessKeeper control-server port bound to this instance."""
        return self._keeper_port

    @property
    def keeper_id(self) -> str:
        """Keeper/controller id bound to this instance."""
        return self._keeper_id

    @property
    def device_udid(self) -> str:
        """Target device UDID bound to this instance."""
        return self._device_udid

    @property
    def device_ip(self) -> str:
        """Target device IP bound to this instance."""
        return self._device_ip

    @property
    def bundle_id(self) -> str:
        """Target app bundle identifier bound to this instance."""
        return self._bundle_id

    @abstractmethod
    def health(self) -> bool:
        """Return ``True`` if the keeper is reachable."""
        raise NotImplementedError

    @abstractmethod
    def runner(self) -> Runner:
        """Return a :class:`Runner` bound to the device's current runner port.

        Raises:
            HostNotSupportedError: On platforms without an on-device runner.
        """
        raise NotImplementedError

    @abstractmethod
    def launch_app(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
    ) -> dict:
        """Launch the bound ``bundle_id`` and make sure it comes up running.

        Args:
            timeout: Overall budget in seconds covering runner restart,
                readiness, and the launch itself.

        Returns:
            dict: The runner's launch result.

        Raises:
            HostTimeoutError: If the runner does not restart/become ready or
                the app is not launched within ``timeout``.
        """
        raise NotImplementedError

    @abstractmethod
    def capture_memgraph(self, timeout: float = 60.0) -> dict:
        """Open a measured window that auto-closes after a fixed interval.

        Args:
            timeout: Seconds to wait for the memgraph capture to finish.

        Returns:
            dict: The runner's measuring result.

        Raises:
            HostTimeoutError: If the memgraph is not captured within ``timeout``.
        """
        raise NotImplementedError

    @abstractmethod
    def export(self) -> dict:
        """Export the run's memgraphs via the keeper.

        The keeper presigns the upload destination, uploads the archive, and
        signs it with its own content type, so callers supply nothing.

        Returns:
            dict: The export summary, including the uploaded archive's
            ``download_url``.
        """
        raise NotImplementedError

    @abstractmethod
    def status(self) -> dict:
        """Return the keeper run status for the bound device."""
        raise NotImplementedError

    @abstractmethod
    def kill(self) -> dict:
        """Kill the keeper run for the bound device."""
        raise NotImplementedError

    @abstractmethod
    def exit(self) -> dict:
        """Quit the on-device runner."""
        raise NotImplementedError
