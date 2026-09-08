"""Unit tests for ``AndroidDevice.stop_app`` running-process guard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from idevice.device.android.device import AndroidDevice
from idevice.device.base.runner import CommandResult

APP_ID = "com.example.app"


@pytest.fixture
def device() -> AndroidDevice:
    with patch("idevice.device.android.device.shutil.which", return_value="/usr/bin/adb"):
        with patch("idevice.device.android.device.adb_binary", return_value="adb"):
            yield AndroidDevice("serial-1", package_name=APP_ID)


def _attach_runner(device: AndroidDevice) -> MagicMock:
    runner = MagicMock()
    device._runner = runner
    return runner


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


def test_stop_app_skips_force_stop_when_not_running(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.return_value = _result(returncode=1)

    device.stop_app(APP_ID)

    runner.run.assert_called_once_with(
        ["adb", "-s", "serial-1", "shell", "pidof", APP_ID],
        check=False,
    )


def test_stop_app_force_stops_when_running(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.side_effect = [_result(stdout="1234\n"), _result()]

    device.stop_app(APP_ID)

    assert runner.run.call_count == 2
    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "pidof", APP_ID],
        check=False,
    )
    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "am", "force-stop", APP_ID]
    )


def test_stop_app_defaults_to_bound_package_name(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.side_effect = [_result(stdout="5678 9012"), _result()]

    device.stop_app()

    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "pidof", APP_ID],
        check=False,
    )
    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "am", "force-stop", APP_ID]
    )


def test_stop_app_force_stops_when_pidof_unavailable(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.side_effect = [
        _result(returncode=127, stderr="pidof: not found"),
        _result(),
    ]

    device.stop_app(APP_ID)

    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "am", "force-stop", APP_ID]
    )
