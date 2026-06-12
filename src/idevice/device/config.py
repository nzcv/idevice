"""Environment-based configuration for ``DeviceBase`` implementations."""

from __future__ import annotations

import os
import sys
from pathlib import Path


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
