"""``RecordBase`` and shared utilities for iRecord-backed record orchestration."""

from idevice.record.base.client import IRecordClient
from idevice.record.base.errors import (
    RecordError,
    RecordNotSupportedError,
    RecordServerError,
)
from idevice.record.base.record import RecordBase

__all__ = [
    "IRecordClient",
    "RecordBase",
    "RecordError",
    "RecordNotSupportedError",
    "RecordServerError",
]
