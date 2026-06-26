"""Public API for ``HostBase`` and platform-specific host implementations.

The host drives a measurement run on the **mac host**: it talks to the
EndlessKeeper control server (:class:`Keeper`) and the on-device
RemoteControlTest runner (:class:`Runner`). Build one with :meth:`Host.create`
/ :meth:`Host.from_env`: ``macos`` yields a real :class:`MacOSHost`; every other
platform yields a no-op :class:`~idevice.host.dummy.host.DummyHost`.
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
from idevice.host.host import Host, Platform
from idevice.host.macos.host import MacOSHost

__all__ = [
    "Host",
    "HostBase",
    "HostError",
    "HostNotSupportedError",
    "HostTimeoutError",
    "Keeper",
    "KeeperError",
    "MacOSHost",
    "Platform",
    "Runner",
    "RunnerError",
    "config",
]
