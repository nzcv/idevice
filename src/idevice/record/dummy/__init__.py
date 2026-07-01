"""No-op ``RecordBase`` implementation for unsupported / unconfigured environments."""

from idevice.record.dummy.record import DummyRecord

__all__ = ["DummyRecord"]
