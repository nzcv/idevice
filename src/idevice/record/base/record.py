"""Abstract ``RecordBase`` for iRecord-backed screen-recording orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from idevice.record import config


class RecordBase(ABC):
    """Drive screen recording of one device via the iRecord control server.

    A record instance is always bound to a single iRecord control server
    (running on the mac host) and a single target device (``device_udid``).
    Concrete implementations talk to the iRecord control server via
    :class:`~idevice.record.base.client.IRecordClient`.
    """

    def __init__(
        self,
        record_type: str,
        *,
        server_ip: str,
        server_port: int = config.DEFAULT_RECORD_PORT,
        device_udid: str,
        out_path: str | None = None,
    ) -> None:
        """Bind the recorder to an iRecord control server and a target device.

        Args:
            record_type: Host/record type identifier (e.g. ``macos``).
            server_ip: iRecord control-server IP. Required and non-empty.
            server_port: iRecord control-server port.
            device_udid: Target device UDID. Required and non-empty.

        Raises:
            ValueError: If ``server_ip`` or ``device_udid`` is empty.
        """
        if not server_ip:
            raise ValueError("server_ip is required and must be a non-empty string")
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        self._record_type = record_type
        self._server_ip = server_ip
        self._server_port = int(server_port)
        self._device_udid = device_udid
        self.out_path = out_path

    @property
    def record_type(self) -> str:
        """Record/host type bound to this instance."""
        return self._record_type

    @property
    def server_ip(self) -> str:
        """iRecord control-server IP bound to this instance."""
        return self._server_ip

    @property
    def server_port(self) -> int:
        """iRecord control-server port bound to this instance."""
        return self._server_port

    @property
    def device_udid(self) -> str:
        """Target device UDID bound to this instance."""
        return self._device_udid

    @abstractmethod
    def health(self) -> bool:
        """Return ``True`` if the iRecord server is reachable."""
        raise NotImplementedError

    @abstractmethod
    def start(self, *, timeout: float | str | None = None) -> dict:
        """Start recording the bound device.

        Args:
            timeout: Optional auto-stop duration. Accepts a duration string the
                server understands (e.g. ``2h``, ``30m``, ``90s``) or a number of
                seconds; ``None`` records until an explicit :meth:`stop`.

        Returns:
            dict: The recording status report.
        """
        raise NotImplementedError

    @abstractmethod
    def stop(self, *, upload: bool = False, preset: str | None = None) -> dict:
        """Stop recording the bound device.

        Args:
            upload: Whether the server should upload the finished file.
            preset: Optional downscale preset (``480p``, ``540p``, ``720p``,
                ``1080p``, ``2160p``).

        Returns:
            dict: The recording status report.
        """
        raise NotImplementedError

    @abstractmethod
    def status(self) -> dict:
        """Return the iRecord recording status for the bound device."""
        raise NotImplementedError

    @property
    def out_path(self) -> Path | None:
        """Return the output path for the recording."""
        return Path(self._out_path) if self._out_path and Path(self._out_path).exists() else None
