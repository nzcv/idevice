"""Unit tests for device screenshot capture."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from idevice.device.android.device import AndroidDevice
from idevice.device.dummy.device import DummyDevice
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3
from idevice.device.windows.device import WindowsDevice


def _ok_runner(*, exists_target: Path) -> MagicMock:
    """Runner whose every command returns exit 0 (and touches the output file)."""
    runner = MagicMock()
    runner.run.return_value = MagicMock(returncode=0)
    exists_target.parent.mkdir(parents=True, exist_ok=True)
    exists_target.write_bytes(b"\x89PNG")
    return runner


def test_android_screenshot_screencaps_and_pulls(tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    runner = _ok_runner(exists_target=out)
    with patch("idevice.device.android.device.shutil.which", return_value="/usr/bin/adb"):
        with patch("idevice.device.android.device.SubprocessRunner", return_value=runner):
            with patch("idevice.device.android.device.adb_binary", return_value="adb"):
                device = AndroidDevice("serial-1")
                assert device.screenshot(out) is True

    remote = "/sdcard/idevice_screenshot.png"
    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "screencap", "-p", remote], check=False
    )
    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "pull", remote, str(out)], check=False
    )
    runner.run.assert_any_call(
        ["adb", "-s", "serial-1", "shell", "rm", "-f", remote], check=False
    )


def test_ios_screenshot_uses_output_flag(tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    runner = _ok_runner(exists_target=out)
    with patch("idevice.device.ios.device.shutil.which", return_value="/usr/bin/ios"):
        with patch("idevice.device.ios.device.SubprocessRunner", return_value=runner):
            device = IOSDevice("00000000-0000000000000000")
            assert device.screenshot(out) is True

    runner.run.assert_called_once_with(
        [
            "ios",
            "--udid",
            "00000000-0000000000000000",
            "screenshot",
            f"--output={out}",
        ],
        check=False,
    )


def test_ios3_screenshot_uses_dvt(tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    runner = _ok_runner(exists_target=out)
    with patch("idevice.device.ios3.device.shutil.which", return_value="/usr/bin/pmd3"):
        with patch("idevice.device.ios3.device.SubprocessRunner", return_value=runner):
            with patch("idevice.device.ios3.device.ios3_binary", return_value="pmd3"):
                device = IOSDevice3("udid-1", device_ip="")
                assert device.screenshot(out) is True

    runner.run.assert_called_once_with(
        ["pmd3", "developer", "dvt", "screenshot", str(out), "--udid", "udid-1"],
        check=False,
    )


def test_dummy_screenshot_returns_false(tmp_path: Path) -> None:
    device = DummyDevice("no device", device_id="", device_ip="", platform="dummy")
    assert device.screenshot(tmp_path / "shot.png") is False


def test_windows_screenshot_uses_imagegrab(tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    image = MagicMock()
    image.save.side_effect = lambda path: Path(path).write_bytes(b"\x89PNG")
    device = WindowsDevice(
        "host-1", company_name="Acme", package_name="Game.exe"
    )
    with patch("PIL.ImageGrab.grab", return_value=image) as grab:
        assert device.screenshot(out) is True

    grab.assert_called_once_with()
    image.save.assert_called_once_with(out)
    assert out.exists()
