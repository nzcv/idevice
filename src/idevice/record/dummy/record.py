"""No-op ``RecordBase`` used when no real (macOS) recorder can be bound."""

from __future__ import annotations

import logging

from idevice.record import config
from idevice.record.base.record import RecordBase

logger = logging.getLogger(__name__)

_LOG_TAG = "[DummyRecord]"


class DummyRecord(RecordBase):
    """No-op :class:`RecordBase` returned when no real recorder can be bound.

    :meth:`idevice.record.record.Record.from_env` /
    :meth:`~idevice.record.record.Record.create` build this for non-macOS host
    types or when a required environment variable is missing/blank, so the
    controller can drive any host type without special-casing an unconfigured
    environment. ``server_ip`` / ``device_udid`` / ``record_type`` expose
    whatever (possibly empty) values were read from the environment; every
    record *operation* reports itself unhealthy and returns an inert placeholder
    result instead of raising.
    """

    def __init__(
        self,
        reason: str,
        *,
        server_ip: str = "",
        server_port: int = config.DEFAULT_RECORD_PORT,
        device_udid: str = "",
        record_type: str = "dummy",
    ) -> None:
        """Bind a no-op recorder without the strict coordinate validation.

        The base initializer rejects empty coordinates; a dummy recorder must be
        constructible from a blank environment, so the backing attributes are set
        directly (the public properties keep working) instead of calling
        ``super().__init__``.
        """
        self._reason = reason
        self._record_type = record_type
        self._server_ip = server_ip
        self._server_port = int(server_port)
        self._device_udid = device_udid
        self.client = None
        logger.error(
            f"{_LOG_TAG} no recorder bound: {reason}; all record operations will be no-ops"
        )

    def _noop(self, operation: str) -> dict:
        """Log that ``operation`` was ignored because no recorder is bound."""
        logger.warning(
            f"{_LOG_TAG} `{operation}` is a no-op on record_type={self.record_type}: {self._reason}"
        )
        return {
            "status": "dummy",
            "record_type": self.record_type,
            "device_udid": self.device_udid,
            "operation": operation,
        }

    def health(self) -> bool:
        return False

    def start(self, *, timeout: float | str | None = None) -> dict:
        del timeout
        return self._noop("start")

    def stop(self, *, upload: bool = False, preset: str | None = None) -> dict:
        del upload, preset
        return self._noop("stop")

    def status(self) -> dict:
        return self._noop("status")
