"""Unit tests for the Windows ``DeviceBase`` implementation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.cache import InstalledAppInfo
from idevice.device.windows.device import WindowsDevice

PKG_NAME = "MyApp_v1.zip"
PKG_VERSION = "MyApp_v1"
APP_ID = "App.exe"
COMPANY_NAME = "TestCo"
PACKAGE_NAME = "App.exe"


@pytest.fixture
def windows_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return a ``WindowsDevice`` with a mocked runner and tmp app/cache dirs."""
    monkeypatch.setenv("IDEVICE_APP_DIR", str(tmp_path))
    runner = MagicMock()
    with patch(
        "idevice.device.windows.device.SubprocessRunner", return_value=runner
    ):
        device = WindowsDevice(
            "local",
            company_name=COMPANY_NAME,
            package_name=PACKAGE_NAME,
            cache_dir=tmp_path,
        )
    return device, runner, tmp_path


def _mark_installed(device: WindowsDevice, app_dir: Path) -> Path:
    """Create the on-disk exe and cache entry so the app looks installed."""
    pkg_dir = app_dir / PKG_VERSION
    pkg_dir.mkdir(parents=True, exist_ok=True)
    exe = pkg_dir / APP_ID
    exe.write_text("")
    device._app_cache.add(APP_ID, version=PKG_VERSION, path=str(exe.resolve()))
    return exe


def test_is_installed_true_when_cached_and_exe_exists(windows_device) -> None:
    device, _runner, app_dir = windows_device
    _mark_installed(device, app_dir)
    assert device.is_installed(APP_ID) is True


def test_is_installed_false_when_exe_missing(windows_device) -> None:
    device, _runner, app_dir = windows_device
    missing_exe = app_dir / PKG_VERSION / APP_ID
    device._app_cache.add(APP_ID, version=PKG_VERSION, path=str(missing_exe))
    assert device.is_installed(APP_ID) is False


def test_is_installed_false_when_not_cached(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    assert device.is_installed(APP_ID) is False


def test_get_installed_pkg_name_returns_app_info(windows_device) -> None:
    device, _runner, app_dir = windows_device
    exe = _mark_installed(device, app_dir)
    result = device.get_installed_pkg_name(APP_ID)
    assert isinstance(result, InstalledAppInfo)
    assert result.app_id == APP_ID
    assert result.version == PKG_VERSION
    assert result.path == str(exe.resolve())


def test_get_installed_pkg_name_none_when_not_installed(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    assert device.get_installed_pkg_name(APP_ID) is None


def test_uninstall_clears_cache_even_when_dir_missing(windows_device) -> None:
    device, runner, app_dir = windows_device
    missing_exe = app_dir / PKG_VERSION / APP_ID
    device._app_cache.add(APP_ID, version=PKG_VERSION, path=str(missing_exe))
    device.uninstall(APP_ID)
    assert device._app_cache.get(APP_ID) is None
    runner.run.assert_not_called()


def test_uninstall_removes_only_this_app_dir(windows_device) -> None:
    device, runner, app_dir = windows_device
    _mark_installed(device, app_dir)
    device.uninstall(APP_ID)
    assert device._app_cache.get(APP_ID) is None
    runner.run.assert_called_once()
    script = runner.run.call_args.args[0][-1]
    assert "Remove-Item" in script
    assert Path(PKG_NAME).stem in script


def test_stop_app_rejects_empty_app_id(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    with pytest.raises(ValueError, match="app_id is required"):
        device.stop_app("")


def test_quote_escapes_single_quotes() -> None:
    assert WindowsDevice._quote("plain") == "'plain'"
    assert WindowsDevice._quote("O'Brien") == "'O''Brien'"
    assert WindowsDevice._quote("C:\\a b\\c") == "'C:\\a b\\c'"


def test_rejects_empty_company_or_package_name() -> None:
    with pytest.raises(ValueError, match="company_name"):
        WindowsDevice("local", company_name="", package_name=PACKAGE_NAME)
    with pytest.raises(ValueError, match="package_name"):
        WindowsDevice("local", company_name=COMPANY_NAME, package_name="")


def test_documents_path_matches_documents_root(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    expected = (
        Path.home() / "AppData" / "Local" / COMPANY_NAME / Path(PACKAGE_NAME).stem
    )
    assert device._documents_root() == expected
    assert device._documents_path(APP_ID) == expected

