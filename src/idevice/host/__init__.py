"""Public API for ``HostBase`` and host-type-specific host implementations.

The host drives a measurement run on the **host machine**: it talks to the
EndlessKeeper control server (:class:`Keeper`) and the on-device
RemoteControlTest runner (:class:`Runner`). Build one with :meth:`Host.create`
/ :meth:`Host.from_env`: ``macos`` yields a real :class:`MacHost`, ``windows``
yields a real :class:`WindowsHost`; every other host type yields a no-op
:class:`~idevice.host.dummy.host.DummyHost`.
"""

from idevice.host import config
from idevice.host.base.errors import (
    HostError,
    HostNotSupportedError,
    HostTimeoutError,
    KeeperError,
    RunnerError,
)
from idevice.host.base.host import HostBase
from idevice.host.base.keeper import Keeper
from idevice.host.base.runner import Runner
from idevice.host.host import Host, HostType
from idevice.host.mac.host import MacHost
from idevice.host.win.host import WindowsHost

__all__ = [
    "Host",
    "HostBase",
    "HostError",
    "HostNotSupportedError",
    "HostTimeoutError",
    "Keeper",
    "KeeperError",
    "MacHost",
    "WindowsHost",
    "HostType",
    "Runner",
    "RunnerError",
    "config",
]
