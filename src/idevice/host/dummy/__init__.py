"""No-op ``HostBase`` implementation for unsupported / unconfigured environments."""

from idevice.host.dummy.host import DummyHost

__all__ = ["DummyHost"]
