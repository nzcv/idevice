"""Unit tests for ``IOSDevice3`` package installation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.base.errors import AppNotInstalledError
from idevice.device.base.runner import CommandResult
from idevice.device.ios3.device import IOSDevice3

APP_ID = "com.example.app"
BINARY = "/opt/ios3/bin/pymobiledevice3"
IDEVICEINSTALLER = "/opt/bin/ideviceinstaller"
UDID = "target-udid"


@pytest.fixture
def ios3_device(tmp_path: Path) -> IOSDevice3:
    """Build an IOSDevice3 with a mocked CLI, runner and isolated cache."""
    with patch("idevice.device.ios3.device.shutil.which", return_value=BINARY):
        with patch(
            "idevice.device.ios3.device.ios3_binary", return_value=BINARY
        ):
            device = IOSDevice3(
                UDID,
                device_ip="",
                package_name=APP_ID,
                cache_dir=tmp_path / "cache",
            )
    device._runner = MagicMock()
    device._runner.run.return_value = CommandResult(
        returncode=0, stdout="", stderr=""
    )
    device.uninstall = MagicMock(side_effect=AppNotInstalledError(APP_ID))
    return device


def test_install_prefers_standalone_ideviceinstaller_on_darwin(
    ios3_device: IOSDevice3, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleApp.ipa"
    ipa.write_bytes(b"ipa")

    with patch("idevice.device.ios3.device.sys.platform", "darwin"):
        with patch(
            "idevice.device.ios3.device.shutil.which",
            return_value=IDEVICEINSTALLER,
        ):
            assert ios3_device.install(ipa, app_id=APP_ID) is True

    ios3_device._runner.run.assert_called_once_with(
        [IDEVICEINSTALLER, "--udid", UDID, "install", str(ipa)],
        timeout=3600,
    )
    assert ios3_device._app_cache.get(APP_ID) is not None


def test_install_falls_back_to_pymobiledevice3_when_installer_missing(
    ios3_device: IOSDevice3, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleApp.ipa"
    ipa.write_bytes(b"ipa")

    with patch("idevice.device.ios3.device.sys.platform", "darwin"):
        with patch("idevice.device.ios3.device.shutil.which", return_value=None):
            assert ios3_device.install(ipa, app_id=APP_ID) is True

    ios3_device._runner.run.assert_called_once_with(
        [BINARY, "apps", "install", str(ipa), "--udid", UDID],
        timeout=3600,
    )


def test_install_uses_pymobiledevice3_off_darwin(
    ios3_device: IOSDevice3, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleApp.ipa"
    ipa.write_bytes(b"ipa")

    with patch("idevice.device.ios3.device.sys.platform", "linux"):
        with patch(
            "idevice.device.ios3.device.shutil.which",
            return_value=IDEVICEINSTALLER,
        ):
            assert ios3_device.install(ipa, app_id=APP_ID) is True

    ios3_device._runner.run.assert_called_once_with(
        [BINARY, "apps", "install", str(ipa), "--udid", UDID],
        timeout=3600,
    )


def test_install_rejects_missing_package(
    ios3_device: IOSDevice3, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match="Package not found"):
        ios3_device.install(tmp_path / "missing.ipa", app_id=APP_ID)
