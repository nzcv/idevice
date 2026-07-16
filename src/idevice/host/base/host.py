"""Abstract ``HostBase`` for keeper-backed measurement orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from idevice.host import config
from idevice.host.base.runner import Runner


class HostBase(ABC):
    """Drive a measurement run on one device via a keeper + on-device runner.

    A host instance is always bound to a single keeper control server and a
    single target device (``device_udid`` / ``device_ip``). Concrete
    implementations talk to the ikeeper control server
    (:class:`~idevice.host.base.keeper.Keeper`) and the on-device
    RemoteControlTest runner (:class:`~idevice.host.base.runner.Runner`).
    """

    def __init__(
        self,
        host_type: str,
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
            host_type: Host type identifier (e.g. ``macos``).
            keeper_ip: ikeeper control-server IP. Required and non-empty.
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
        self._host_type = host_type
        self._keeper_ip = keeper_ip
        self._keeper_port = int(keeper_port)
        self._device_udid = device_udid
        self._device_ip = device_ip
        self._bundle_id = bundle_id
        self._keeper_id = keeper_id

    @property
    def host_type(self) -> str:
        """Host type bound to this instance."""
        return self._host_type

    @property
    def keeper_ip(self) -> str:
        """ikeeper control-server IP bound to this instance."""
        return self._keeper_ip

    @property
    def keeper_port(self) -> int:
        """ikeeper control-server port bound to this instance."""
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
        memgraph: bool = False,
    ) -> dict:
        """Launch the bound ``bundle_id`` and make sure it comes up running.

        Args:
            timeout: Overall budget in seconds covering runner restart,
                readiness, and the launch itself.
            memgraph: When ``True``, cold-launch with performance diagnostics
                and stack logging for later memgraph capture. Platforms that
                do not support memgraph may ignore this flag.

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
    def screenshot(self, dest_path: Path | str) -> dict:
        """Capture one screenshot of the device and write it to ``dest_path``.

        Delegates to the on-device runner's ``/api/screenshot`` endpoint via
        the keeper proxy.

        Args:
            dest_path: Local path the captured PNG is written to.

        Returns:
            dict: A result summary including the written screenshot path.

        Raises:
            HostNotSupportedError: On platforms without an on-device runner.
        """
        raise NotImplementedError

    @abstractmethod
    def tap(self, x: float, y: float) -> dict:
        """Tap the device screen at the normalized point ``(x, y)``.

        Delegates to the on-device runner's ``/api/tap`` endpoint via the keeper
        proxy. Coordinates are fractions of the screen (``(0, 0)`` top-left,
        ``(1, 1)`` bottom-right), so they are independent of the device's pixel
        resolution and point scale. To tap a feature found in a screenshot,
        divide its pixel coordinates by the screenshot's width and height.

        The offset is anchored to the foreground app's frame (via the bound
        ``bundle_id``) so it tracks the current interface orientation; this is
        what makes taps land correctly for landscape apps.

        Args:
            x: Horizontal position in ``[0, 1]`` (fraction of screen width).
            y: Vertical position in ``[0, 1]`` (fraction of screen height).

        Returns:
            dict: The runner's tap result.

        Raises:
            HostNotSupportedError: On platforms without an on-device runner.
            ValueError: If ``x`` or ``y`` is outside ``[0, 1]``.
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
        """Gracefully quit the on-device runner and wait for it to finish.

        Concrete keeper-backed hosts drive this through the keeper's dedicated
        graceful-exit endpoint (``POST /api/runs/{udid}/exit``), which asks the
        runner to quit and waits for the run to reach a terminal state so the
        ``.xcresult`` is finalized.
        """
        raise NotImplementedError
