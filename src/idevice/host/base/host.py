"""Abstract ``HostBase`` for the mac-host measurement orchestrator.

A host binds a single keeper control server (on the mac host) and a single target
device (UDID + IP), and drives a measurement run end to end: launch the xctest run
via the keeper, wait for the on-device runner to come up, start/stop measuring, and
optionally export the captured memory graphs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from idevice.host import config
from idevice.host.base.keeper import Keeper
from idevice.host.base.runner import Runner


class HostBase(ABC):
    """Orchestrate a measurement run against one device via the keeper + runner."""

    def __init__(
        self,
        *,
        keeper_ip: str,
        keeper_port: int,
        device_udid: str,
        device_ip: str,
        platform: str,
        keeper_id: str = "",
    ) -> None:
        """Bind the host to a keeper control server and a target device.

        Args:
            keeper_ip: Keeper control-server IP. Required and non-empty.
            keeper_port: Keeper control-server port.
            device_udid: Target device UDID. Required and non-empty.
            device_ip: Target device IP. Required and non-empty.
            platform: Platform identifier (only ``macos`` is supported).
            keeper_id: Optional keeper/controller id (informational).

        Raises:
            ValueError: If ``keeper_ip``, ``device_udid`` or ``device_ip`` is empty.
        """
        if not keeper_ip:
            raise ValueError("keeper_ip is required and must be a non-empty string")
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        if not device_ip:
            raise ValueError("device_ip is required and must be a non-empty string")
        self._keeper_ip = keeper_ip
        self._keeper_port = int(keeper_port)
        self._device_udid = device_udid
        self._device_ip = device_ip
        self._platform = platform
        self._keeper_id = keeper_id
        self.keeper = Keeper(keeper_ip, self._keeper_port)

    @property
    def platform(self) -> str:
        """Platform bound to this instance."""
        return self._platform

    @property
    def keeper_ip(self) -> str:
        """Keeper control-server IP bound to this instance."""
        return self._keeper_ip

    @property
    def keeper_port(self) -> int:
        """Keeper control-server port bound to this instance."""
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

    @abstractmethod
    def health(self) -> bool:
        """Return ``True`` if both the keeper and the on-device runner are reachable."""
        raise NotImplementedError

    @abstractmethod
    def launch(self, **overrides) -> dict:
        """Launch the run for the bound device via the keeper.

        Args:
            **overrides: Optional launch fields forwarded to
                :meth:`idevice.host.base.keeper.Keeper.launch`.

        Returns:
            dict: The launched run record.
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
    def export(self, presigned_url: str, content_type: str | None = None) -> dict:
        """Export the run's memory graphs to a presigned URL via the keeper."""
        raise NotImplementedError

    @abstractmethod
    def runner(self) -> Runner:
        """Return a :class:`Runner` bound to the device's current runner port.

        The port is resolved from the keeper launch/status ``server_port`` and
        falls back to :func:`idevice.host.config.runner_port`.
        """
        raise NotImplementedError

    @abstractmethod
    def wait_until_ready(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        interval: float = 2.0,
    ) -> None:
        """Poll the on-device runner until it is healthy or ``timeout`` elapses.

        Raises:
            HostTimeoutError: If the runner does not become ready in time.
        """
        raise NotImplementedError

    @abstractmethod
    def start_measuring(self, bundle_id: str) -> dict:
        """Open a measured window on ``bundle_id`` via the on-device runner."""
        raise NotImplementedError

    @abstractmethod
    def stop_measuring(self) -> dict:
        """Close the measured window via the on-device runner."""
        raise NotImplementedError

    @abstractmethod
    def measure(
        self,
        bundle_id: str,
        *,
        duration_s: float,
        export_url: str | None = None,
        content_type: str | None = None,
        **launch_overrides,
    ) -> dict:
        """Run the full measurement workflow for ``bundle_id``.

        Launch the run, wait for the runner, start measuring, sleep ``duration_s``,
        stop measuring, and optionally export to ``export_url``.

        Returns:
            dict: A summary of the run, including the export result when requested.
        """
        raise NotImplementedError
