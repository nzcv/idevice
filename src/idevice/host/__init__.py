"""Keeper-backed host measurement orchestrator (runs on the mac host).

The host drives a measurement run: it talks to the EndlessKeeper control server
(:class:`Keeper`) and the on-device RemoteControlTest runner (:class:`Runner`),
orchestrated by :class:`Host`. Non-macOS platforms get a no-op :class:`DummyHost`.
"""

from idevice.host import config
from idevice.host.errors import (
    HostError,
    HostNotSupportedError,
    HostTimeoutError,
    KeeperError,
    RunnerError,
)
from idevice.host.host import DummyHost, Host
from idevice.host.keeper import Keeper
from idevice.host.runner import Runner

__all__ = [
    "DummyHost",
    "Host",
    "HostError",
    "HostNotSupportedError",
    "HostTimeoutError",
    "Keeper",
    "KeeperError",
    "Runner",
    "RunnerError",
    "config",
]
