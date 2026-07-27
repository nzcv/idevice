"""Environment-based configuration for ``DeviceBase`` implementations."""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_APP_RETENTION_DAYS = 1


def ios_binary() -> str:
    """Return the go-ios CLI binary path."""
    return os.environ.get("IDEVICE_IOS_BINARY", "ios")


def ios3_binary() -> str:
    """Return the pymobiledevice3 CLI binary path."""
    if sys.platform == "win32":
        default = Path.home() / ".local" / "bin" / "pymobiledevice3.exe"
    else:
        default = Path.home() / ".local" / "bin" / "pymobiledevice3"
    return os.environ.get("IDEVICE_IOS3_BINARY", str(default))


def adb_binary() -> str:
    """Return the adb CLI binary path."""
    return os.environ.get("IDEVICE_ADB_BINARY", "adb")


def powershell_binary() -> str:
    """Return the PowerShell binary path."""
    return os.environ.get("IDEVICE_POWERSHELL_BINARY", "powershell")


def user_data_dir() -> Path:
    """Return the default directory for idevice user data."""
    return Path.home() / ".idevice"


def app_retention_days() -> int:
    """Return how many days extracted packages are kept (``IDEVICE_APP_RETENTION_DAYS``).

    The Windows device prunes extracted package directories older than this many
    days from its app directory at the start of every installation. Defaults to
    :data:`DEFAULT_APP_RETENTION_DAYS`; set the env var to ``0`` (or a negative
    value) to disable the cleanup entirely.

    Returns:
        The retention window in whole days; ``<= 0`` disables cleanup.
    """
    raw = os.environ.get("IDEVICE_APP_RETENTION_DAYS")
    return int(raw) if raw else DEFAULT_APP_RETENTION_DAYS


def platform() -> str:
    """Return the target device platform (``GAUTO_PLATFORM``).

    The controller (``controller/src/worker/engine.rs``) injects one of
    ``android`` | ``ios`` | ``windows`` | ``macos``.
    """
    return os.environ.get("GAUTO_PLATFORM", "")


def device_id() -> str:
    """Return the target device id / UDID (``GAUTO_DEVICE_UDID``)."""
    return os.environ.get("GAUTO_DEVICE_UDID", "")


def device_ip() -> str:
    """Return the target device IP (``GAUTO_DEVICE_IP``)."""
    return os.environ.get("GAUTO_DEVICE_IP", "")


def company_name() -> str:
    """Return the company name (``GAUTO_COMPANY_NAME``)."""
    return os.environ.get("GAUTO_COMPANY_NAME", "")


def package_name() -> str:
    """Return the package name (``GAUTO_PACKAGE_NAME``)."""
    return os.environ.get("GAUTO_PACKAGE_NAME", "")
