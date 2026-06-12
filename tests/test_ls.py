"""Unit tests for remote directory listing (``ls``)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from idevice.device.android.device import AndroidDevice
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3
from idevice.device.windows.device import WindowsDevice


@pytest.fixture
def mock_adb_on_path() -> None:
    with patch("idevice.device.android.device.shutil.which", return_value="/usr/bin/adb"):
        yield


def test_ios3_ls_runs_afc_ls() -> None:
    runner = MagicMock()
    runner.run.return_value.stdout = "/Documents/foo.txt\n/Documents/bar.txt\n"
    with patch("idevice.device.ios3.device.shutil.which", return_value="/opt/ios3/bin/pymobiledevice3"):
        with patch("idevice.device.ios3.device.SubprocessRunner", return_value=runner):
            with patch(
                "idevice.device.ios3.device.ios3_binary",
                return_value="/opt/ios3/bin/pymobiledevice3",
            ):
                device = IOSDevice3("00000000-0000000000000000", device_ip="")
                entries = device.ls("/Documents")

    runner.run.assert_called_once_with(
        [
            "/opt/ios3/bin/pymobiledevice3",
            "afc",
            "ls",
            "/Documents",
            "--udid",
            "00000000-0000000000000000",
        ]
    )
    assert entries == ["/Documents/foo.txt", "/Documents/bar.txt"]


def test_ios3_ls_recursive_adds_flag() -> None:
    runner = MagicMock()
    runner.run.return_value.stdout = ""
    with patch("idevice.device.ios3.device.shutil.which", return_value="/opt/ios3/bin/pymobiledevice3"):
        with patch("idevice.device.ios3.device.SubprocessRunner", return_value=runner):
            with patch(
                "idevice.device.ios3.device.ios3_binary",
                return_value="/opt/ios3/bin/pymobiledevice3",
            ):
                device = IOSDevice3("00000000-0000000000000000", device_ip="")
                device.ls("/Documents", recursive=True)

    assert "-r" in runner.run.call_args[0][0]


def test_ios3_ls_rejects_app_id() -> None:
    with patch("idevice.device.ios3.device.shutil.which", return_value="/opt/ios3/bin/pymobiledevice3"):
        with patch("idevice.device.ios3.device.SubprocessRunner"):
            with patch(
                "idevice.device.ios3.device.ios3_binary",
                return_value="/opt/ios3/bin/pymobiledevice3",
            ):
                device = IOSDevice3("00000000-0000000000000000", device_ip="")
                with pytest.raises(NotImplementedError, match="app_id"):
                    device.ls("/Documents", app_id="com.example.app")


def test_ios_device_ls_parses_json() -> None:
    runner = MagicMock()
    runner.run.return_value.stdout = (
        '{"path":"/Documents","files":["a.txt","b.txt"],"count":2}'
    )
    with patch("idevice.device.ios.device.shutil.which", return_value="/usr/bin/ios"):
        with patch("idevice.device.ios.device.SubprocessRunner", return_value=runner):
            device = IOSDevice("00000000-0000000000000000")
            entries = device.ls("/Documents", app_id="com.example.app")

    runner.run.assert_called_once_with(
        [
            "ios",
            "--udid",
            "00000000-0000000000000000",
            "--app=com.example.app",
            "file",
            "ls",
            "--path=/Documents",
        ]
    )
    assert entries == ["a.txt", "b.txt"]


def test_android_device_ls_runs_adb_shell(mock_adb_on_path: None) -> None:
    runner = MagicMock()
    runner.run.return_value.stdout = "file1\nfile2\n"
    with patch("idevice.device.android.device.SubprocessRunner", return_value=runner):
        with patch("idevice.device.android.device.adb_binary", return_value="adb"):
            device = AndroidDevice("serial-1")
            entries = device.ls("/sdcard/Download")

    runner.run.assert_called_once_with(
        ["adb", "-s", "serial-1", "shell", "ls", "-1", "/sdcard/Download"]
    )
    assert entries == ["file1", "file2"]


def test_windows_device_ls_not_implemented() -> None:
    device = WindowsDevice("local")
    with pytest.raises(NotImplementedError, match="ls"):
        device.ls("/")
