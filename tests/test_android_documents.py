"""Unit tests for ``AndroidDevice`` Documents (external files) helpers.

The Android "Documents" sandbox maps to the app's external files directory
``/sdcard/Android/data/<app_id>/files`` and is driven entirely via ``adb``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from idevice.device.android.device import AndroidDevice

APP_ID = "com.android.chrome"
ROOT = f"/sdcard/Android/data/{APP_ID}/files"


@pytest.fixture
def device() -> AndroidDevice:
    with patch("idevice.device.android.device.shutil.which", return_value="/usr/bin/adb"):
        with patch("idevice.device.android.device.adb_binary", return_value="adb"):
            yield AndroidDevice("serial-1")


def _attach_runner(device: AndroidDevice) -> MagicMock:
    runner = MagicMock()
    device._runner = runner
    return runner


def test_documents_root_builds_external_files_path() -> None:
    assert AndroidDevice.documents_root(APP_ID) == ROOT


def test_documents_root_rejects_empty_app_id() -> None:
    with pytest.raises(ValueError, match="app_id"):
        AndroidDevice.documents_root("")


def test_documents_path_joins_relative_remote() -> None:
    assert AndroidDevice._documents_path(APP_ID, "logs/run.log") == f"{ROOT}/logs/run.log"
    assert AndroidDevice._documents_path(APP_ID, "/logs/run.log") == f"{ROOT}/logs/run.log"
    assert AndroidDevice._documents_path(APP_ID, ".") == ROOT


def test_documents_exists_true_on_zero_exit(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.returncode = 0

    assert device.documents_exists(APP_ID, "config.json") is True
    runner.run.assert_called_once_with(
        ["adb", "-s", "serial-1", "shell", "test", "-e", f"'{ROOT}/config.json'"],
        check=False,
    )


def test_documents_exists_false_on_nonzero_exit(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.returncode = 1

    assert device.documents_exists(APP_ID, "missing.txt") is False


def test_documents_ls_returns_entries(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.stdout = "a.json\nb.log\n\n"

    entries = device.documents_ls(APP_ID, ".")

    runner.run.assert_called_once_with(
        ["adb", "-s", "serial-1", "shell", "ls", "-1", f"'{ROOT}'"]
    )
    assert entries == ["a.json", "b.log"]


def test_documents_pull_returns_false_when_missing(device: AndroidDevice, tmp_path) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.returncode = 1  # test -e fails

    ok = device.documents_pull(APP_ID, "missing.txt", tmp_path / "out.txt")

    assert ok is False
    runner.run.assert_called_once()  # only the existence check ran


def test_documents_pull_runs_adb_pull_when_present(device: AndroidDevice, tmp_path) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.returncode = 0

    local = tmp_path / "nested" / "out.txt"
    ok = device.documents_pull(APP_ID, "logs/run.log", local)

    assert ok is True
    assert local.parent.exists()
    pull_call = runner.run.call_args_list[-1]
    assert pull_call.args[0] == [
        "adb",
        "-s",
        "serial-1",
        "pull",
        f"{ROOT}/logs/run.log",
        str(local),
    ]


def test_documents_push_returns_false_when_local_missing(device: AndroidDevice, tmp_path) -> None:
    runner = _attach_runner(device)

    ok = device.documents_push(APP_ID, tmp_path / "nope.txt", "dest.txt")

    assert ok is False
    runner.run.assert_not_called()


def test_documents_push_makes_parent_and_pushes(device: AndroidDevice, tmp_path) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.returncode = 0
    local = tmp_path / "in.txt"
    local.write_text("payload")

    ok = device.documents_push(APP_ID, local, "logs/in.txt")

    assert ok is True
    mkdir_call, push_call = runner.run.call_args_list
    assert mkdir_call.args[0] == [
        "adb",
        "-s",
        "serial-1",
        "shell",
        "mkdir",
        "-p",
        f"'{ROOT}/logs'",
    ]
    assert push_call.args[0] == [
        "adb",
        "-s",
        "serial-1",
        "push",
        str(local),
        f"{ROOT}/logs/in.txt",
    ]


def test_documents_rm_runs_rm_rf(device: AndroidDevice) -> None:
    runner = _attach_runner(device)
    runner.run.return_value.returncode = 0

    ok = device.documents_rm(APP_ID, "logs")

    assert ok is True
    runner.run.assert_called_once_with(
        ["adb", "-s", "serial-1", "shell", "rm", "-rf", f"'{ROOT}/logs'"],
        check=False,
    )


def test_documents_methods_validate_args(device: AndroidDevice) -> None:
    with pytest.raises(ValueError, match="app_id"):
        device.documents_exists("", "x")
    with pytest.raises(ValueError, match="remote"):
        device.documents_ls(APP_ID, "")
