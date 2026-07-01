"""macOS iRecord-backed :class:`RecordBase` implementation.

Runs on the **mac host**: :class:`MacRecord` drives screen recording of a
USB-connected device via the iRecord control server
(:class:`~idevice.record.base.client.IRecordClient`).
"""

from __future__ import annotations

import logging

from idevice.record import config
from idevice.record.base.client import IRecordClient
from idevice.record.base.record import RecordBase

logger = logging.getLogger(__name__)

_LOG_TAG = "[MacRecord]"


class MacRecord(RecordBase):
    """Drive screen recording of one device via the iRecord control server."""

    def __init__(
        self,
        record_type: str = "macos",
        *,
        server_ip: str,
        server_port: int = config.DEFAULT_RECORD_PORT,
        device_udid: str,
    ) -> None:
        """Bind the recorder to an iRecord control server and a target device.

        Raises:
            ValueError: If ``server_ip`` or ``device_udid`` is empty.
        """
        super().__init__(
            record_type,
            server_ip=server_ip,
            server_port=server_port,
            device_udid=device_udid,
        )
        self.client = IRecordClient(self.server_ip, self.server_port)

    @classmethod
    def from_env(cls) -> MacRecord:
        """Build a :class:`MacRecord` from the environment.

        Reads ``GAUTO_HOST_TYPE``, ``GAUTO_HOST_IP``, ``IRECORD_PORT`` and
        ``GAUTO_DEVICE_UDID``.

        Returns:
            MacRecord: A recorder bound to the iRecord server/device described by
            the environment.

        Raises:
            ValueError: If ``GAUTO_HOST_IP`` or ``GAUTO_DEVICE_UDID`` is empty.
        """
        return cls(
            record_type=config.record_type(),
            server_ip=config.server_ip(),
            server_port=config.server_port(),
            device_udid=config.device_udid(),
        )

    def health(self) -> bool:
        """Return ``True`` if the iRecord server is reachable.

        The iRecord server has no dedicated health route, so reachability is
        probed via the bound device's status endpoint.
        """
        try:
            self.client.status(self.device_udid)
        except Exception as exc:  # noqa: BLE001 - boolean probe
            logger.debug(f"{_LOG_TAG} iRecord health failed: {exc}")
            return False
        return True

    def start(self, *, timeout: float | str | None = None) -> dict:
        """Start recording the bound device via the iRecord server."""
        result = self.client.start(self.device_udid, timeout=timeout)
        logger.info(f"{_LOG_TAG} started recording {self.device_udid}")
        return result

    def stop(self, *, upload: bool = False, preset: str | None = None) -> dict:
        """Stop recording the bound device via the iRecord server."""
        result = self.client.stop(self.device_udid, upload=upload, preset=preset)
        logger.info(f"{_LOG_TAG} stopped recording {self.device_udid}")
        return result

    def status(self) -> dict:
        """iRecord recording status for the bound device."""
        return self.client.status(self.device_udid)
