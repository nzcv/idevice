"""Public ``Record`` entry point for screen-recording control.

Use :meth:`Record.create` / :meth:`Record.from_env` to build a recorder:
``macos`` yields a real :class:`~idevice.record.mac.record.MacRecord` (iRecord
control server), ``android`` yields a real
:class:`~idevice.record.android.record.AndroidRecord` (local scrcpy); every
other host type yields a no-op :class:`~idevice.record.dummy.record.DummyRecord`
so the controller can drive any host type without special-casing it.
"""

from __future__ import annotations

import logging
from enum import Enum

from idevice.record import config
from idevice.record.android.record import AndroidRecord
from idevice.record.base.errors import RecordError
from idevice.record.base.record import RecordBase
from idevice.record.dummy.record import DummyRecord
from idevice.record.mac.record import MacRecord

logger = logging.getLogger(__name__)

_LOG_TAG = "[Record]"


class RecordType(Enum):
    """Supported record/host types."""

    MACOS = "macos"
    IOS = "ios"
    ANDROID = "android"
    WINDOWS = "windows"

    @classmethod
    def from_string(cls, record_type: str) -> RecordType:
        """Convert a string to a RecordType enum value."""
        try:
            return cls(record_type.lower())  # type: ignore
        except ValueError:
            raise ValueError(f"Invalid record type: {record_type}") from ValueError


class _RecordMeta(type):
    """Metaclass exposing the last-built recorder via ``Record.Instance``."""

    @property
    def Instance(cls) -> RecordBase:
        """Return the most recently built recorder for quick access.

        The instance is a real :class:`~idevice.record.mac.record.MacRecord` when
        one was built, or a no-op :class:`~idevice.record.dummy.record.DummyRecord`
        when :meth:`Record.from_env` could not bind a recorder.

        Raises:
            RuntimeError: If no recorder has been built yet (call
                :meth:`Record.create` / :meth:`Record.from_env` first).
        """
        if cls._instance is None:
            raise RuntimeError(
                f"{_LOG_TAG} no Record instance has been created yet; "
                f"call Record.create(...) or Record.from_env() first"
            )
        return cls._instance


class Record(metaclass=_RecordMeta):
    """Build a host-type-specific :class:`RecordBase` and expose it as a singleton.

    The most recently built recorder is cached and reachable anywhere via
    :attr:`Record.Instance`. Use :meth:`reset` to drop the cached instance
    (mainly for tests).
    """

    _instance: RecordBase | None = None

    @classmethod
    def reset(cls) -> None:
        """Drop the cached recorder so the next build rebinds :attr:`Record.Instance`."""
        cls._instance = None

    @classmethod
    def create(
        cls,
        *,
        record_type: str,
        server_ip: str,
        device_udid: str,
        server_port: int = config.DEFAULT_RECORD_PORT,
    ) -> RecordBase:
        """Build a recorder for ``record_type``: ``macos`` -> real, else dummy.

        Args:
            record_type: Target host type (``macos`` runs the iRecord-backed
                recorder, ``android`` runs the local scrcpy recorder; every other
                value resolves to a :class:`DummyRecord`).
            server_ip: iRecord control-server IP (the mac host); unused for
                ``android``.
            device_udid: Target device UDID (adb serial for ``android``).
            server_port: iRecord control-server port; unused for ``android``.

        Returns:
            RecordBase: The host-type-specific recorder implementation
            (``macos`` -> :class:`MacRecord`, ``android`` -> :class:`AndroidRecord`,
            else :class:`DummyRecord`).

        Raises:
            ValueError: If ``record_type`` is unsupported, or a required
                coordinate is empty (``macos`` needs ``server_ip``; both need
                ``device_udid``).
            RecordError: If ``android`` is requested but the scrcpy CLI is missing.
        """
        r = RecordType.from_string(record_type)
        logger.debug(f"{_LOG_TAG} create record_type={r} device_udid={device_udid}")
        if r is RecordType.MACOS:
            recorder: RecordBase = MacRecord(
                record_type=record_type,
                server_ip=server_ip,
                server_port=server_port,
                device_udid=device_udid,
            )
        elif r is RecordType.ANDROID:
            recorder = AndroidRecord(
                record_type=record_type,
                server_ip=server_ip,
                server_port=server_port,
                device_udid=device_udid,
            )
        else:
            recorder = DummyRecord(
                f"unsupported record type: {record_type}",
                record_type=r.value,
                server_ip=server_ip,
                server_port=server_port,
                device_udid=device_udid,
            )
        logger.info(
            f"{_LOG_TAG} created {type(recorder).__name__} for device_udid={recorder.device_udid}"
        )
        cls._instance = recorder
        return recorder

    @classmethod
    def from_env(cls) -> RecordBase:
        """Build a recorder from the environment.

        Reads ``GAUTO_HOST_TYPE``, ``GAUTO_HOST_IP``, ``IRECORD_PORT`` and
        ``GAUTO_DEVICE_UDID`` (plus the optional ``IDEVICE_SCRCPY_*`` overrides
        for Android).

        Unlike :meth:`create`, this never raises on a missing/blank environment:
        an unsupported host type, a missing required coordinate, or a missing
        scrcpy CLI (Android) logs the reason and returns a no-op
        :class:`DummyRecord`. The result (real or dummy) is bound as
        :attr:`Record.Instance`, so callers can always reach it there.

        Returns:
            RecordBase: The host-type-specific recorder (``macos`` ->
            :class:`MacRecord`, ``android`` -> :class:`AndroidRecord`), or a
            no-op :class:`DummyRecord` whose every operation reports unhealthy
            and returns an inert default.
        """
        record_type = config.record_type()
        server_ip = config.server_ip()
        server_port = config.server_port()
        device_udid = config.device_udid()

        try:
            r = RecordType.from_string(record_type)
        except ValueError:
            return cls._bind_dummy(
                f"invalid record type: {record_type!r}",
                record_type, server_ip, server_port, device_udid,
            )

        if r is RecordType.MACOS:
            required = (("GAUTO_HOST_IP", server_ip), ("GAUTO_DEVICE_UDID", device_udid))
        elif r is RecordType.ANDROID:
            required = (("GAUTO_DEVICE_UDID", device_udid),)
        else:
            return cls._bind_dummy(
                f"unsupported record type: {record_type}",
                record_type, server_ip, server_port, device_udid,
            )

        missing = [name for name, value in required if not value]
        if missing:
            return cls._bind_dummy(
                f"missing/blank env var(s): {', '.join(missing)}",
                record_type, server_ip, server_port, device_udid,
            )

        try:
            return cls.create(
                record_type=record_type,
                server_ip=server_ip,
                server_port=server_port,
                device_udid=device_udid,
            )
        except (ValueError, RecordError) as exc:
            return cls._bind_dummy(
                f"invalid env configuration: {exc}",
                record_type, server_ip, server_port, device_udid,
            )

    @classmethod
    def _bind_dummy(
        cls,
        reason: str,
        record_type: str,
        server_ip: str,
        server_port: int,
        device_udid: str,
    ) -> RecordBase:
        """Bind a no-op :class:`DummyRecord` as the current instance and return it."""
        recorder = DummyRecord(
            reason,
            record_type=record_type,
            server_ip=server_ip,
            server_port=server_port,
            device_udid=device_udid,
        )
        cls._instance = recorder
        return recorder
