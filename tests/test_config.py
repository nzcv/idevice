"""Unit tests for environment-based configuration."""

from __future__ import annotations

import os
from pathlib import Path

from idevice.device import config


def test_ios_binary_default() -> None:
    os.environ.pop("IDEVICE_IOS_BINARY", None)
    assert config.ios_binary() == "ios"


def test_adb_binary_default() -> None:
    os.environ.pop("IDEVICE_ADB_BINARY", None)
    assert config.adb_binary() == "adb"


def test_powershell_binary_default() -> None:
    os.environ.pop("IDEVICE_POWERSHELL_BINARY", None)
    assert config.powershell_binary() == "powershell"


def test_xcodebuild_binary_default() -> None:
    os.environ.pop("IDEVICE_XCODEBUILD_BINARY", None)
    assert config.xcodebuild_binary() == "xcodebuild"


def test_user_data_dir() -> None:
    assert config.user_data_dir() == Path.home() / ".idevice"
