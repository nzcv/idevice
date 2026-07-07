"""Public API for ``RecordBase`` and host-type-specific record implementations.

The recorder drives screen recording of a device: on the **mac host** it talks
to the iRecord control server (:class:`IRecordClient`) which records
USB-connected iOS devices directly via CoreMediaIO; on an **Android host** it
shells out to the local ``scrcpy`` CLI (:class:`AndroidRecord`). Build one with
:meth:`Record.create` / :meth:`Record.from_env`: ``macos`` yields a real
:class:`MacRecord`, ``android`` yields a real :class:`AndroidRecord`; every
other host type yields a no-op
:class:`~idevice.record.dummy.record.DummyRecord`.
"""

from idevice.record import config
from idevice.record.android.record import AndroidRecord
from idevice.record.base.client import IRecordClient
from idevice.record.base.errors import (
    RecordError,
    RecordNotSupportedError,
    RecordServerError,
)
from idevice.record.base.record import RecordBase
from idevice.record.dummy.record import DummyRecord
from idevice.record.mac.record import MacRecord
from idevice.record.record import Record, RecordType

__all__ = [
    "Record",
    "RecordBase",
    "RecordType",
    "RecordError",
    "RecordNotSupportedError",
    "RecordServerError",
    "IRecordClient",
    "MacRecord",
    "AndroidRecord",
    "DummyRecord",
    "config",
]
