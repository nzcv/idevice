"""Unit tests for swipe gestures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from idevice.device.android.device import AndroidDevice
from idevice.device.ios.device import IOSDevice
from idevice.device.windows.device import WindowsDevice


@pytest.fixture
def mock_adb_on_path() -> None:
    with patch("idevice.device.android.device.shutil.which", return_value="/usr/bin/adb"):
        yield


def test_android_device_swipe_runs_adb_input(mock_adb_on_path: None) -> None:
    runner = MagicMock()
    with patch("idevice.device.android.device.SubprocessRunner", return_value=runner):
        with patch("idevice.device.android.device.adb_binary", return_value="adb"):
            device = AndroidDevice("serial-1")
            device.swipe(10, 20, 300, 400, duration_ms=500)

    runner.run.assert_called_once_with(
        ["adb", "-s", "serial-1", "shell", "input", "swipe", "10", "20", "300", "400", "500"]
    )


def test_android_device_swipe_rejects_invalid_duration(mock_adb_on_path: None) -> None:
    with patch("idevice.device.android.device.SubprocessRunner"):
        with patch("idevice.device.android.device.adb_binary", return_value="adb"):
            device = AndroidDevice("serial-1")
            with pytest.raises(ValueError, match="duration_ms"):
                device.swipe(0, 0, 1, 1, duration_ms=0)


def test_ios_device_swipe_not_implemented() -> None:
    with patch("idevice.device.ios.device.shutil.which", return_value="/usr/bin/ios"):
        with patch("idevice.device.ios.device.SubprocessRunner"):
            device = IOSDevice("00000000-0000000000000000")
            with pytest.raises(NotImplementedError, match="swipe"):
                device.swipe(0, 0, 100, 100)


def test_windows_device_swipe_not_implemented() -> None:
    device = WindowsDevice("local", company_name="TestCo", package_name="App.exe")
    with pytest.raises(NotImplementedError, match="swipe"):
        device.swipe(0, 0, 100, 100)
