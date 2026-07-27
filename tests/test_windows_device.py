"""Unit tests for the Windows ``DeviceBase`` implementation."""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.base.cleanup import cleanup_old_packages
from idevice.device.base.device import AppDataPath
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


def test_stop_app_kills_process_by_stem(windows_device) -> None:
    device, runner, _app_dir = windows_device
    device.stop_app("App.exe")
    runner.run.assert_called_once()
    script = runner.run.call_args.args[0][-1]
    assert "Stop-Process" in script
    assert "-Name 'App'" in script
    assert "-Force" in script
    assert "SilentlyContinue" in script


def test_stop_app_defaults_to_bound_package_name(windows_device) -> None:
    device, runner, _app_dir = windows_device
    device.stop_app()
    runner.run.assert_called_once()
    script = runner.run.call_args.args[0][-1]
    assert "Stop-Process" in script
    assert "-Name 'App'" in script


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
        Path.home() / "AppData" / "LocalLow" / COMPANY_NAME / Path(PACKAGE_NAME).stem
    )
    assert device._documents_root() == expected
    assert device._documents_path(".") == expected
    assert device._documents_path("/") == expected


def test_documents_path_resolves_relative_remote(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    root = device._documents_root()
    assert device._documents_path("sub/file.txt") == root / "sub" / "file.txt"
    assert device._documents_path("\\sub\\file.txt") == root / "sub" / "file.txt"


def test_documents_require_app_and_remote_rejects_empty(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    with pytest.raises(ValueError, match="app_id is required"):
        device.documents_exists("", "file.txt")
    with pytest.raises(ValueError, match="remote is required"):
        device.documents_exists(APP_ID, "")


def test_documents_roundtrip_file(windows_device, tmp_path: Path) -> None:
    device, _runner, _app_dir = windows_device
    device._doc_dir = tmp_path / "docroot"
    src = tmp_path / "src.txt"
    src.write_text("hello")

    assert device.documents_push(APP_ID, src, "data/src.txt") is True
    assert device.documents_exists(APP_ID, "data/src.txt") is True
    assert device.documents_ls(APP_ID, "data") == ["src.txt"]

    out = tmp_path / "out.txt"
    assert device.documents_pull(APP_ID, "data/src.txt", out) is True
    assert out.read_text() == "hello"

    assert device.documents_rm(APP_ID, "data/src.txt") is True
    assert device.documents_exists(APP_ID, "data/src.txt") is False


def test_documents_roundtrip_directory(windows_device, tmp_path: Path) -> None:
    device, _runner, _app_dir = windows_device
    device._doc_dir = tmp_path / "docroot"
    src_dir = tmp_path / "tree"
    (src_dir / "nested").mkdir(parents=True)
    (src_dir / "a.txt").write_text("a")
    (src_dir / "nested" / "b.txt").write_text("b")

    assert device.documents_push(APP_ID, src_dir, "tree") is True
    assert device.documents_exists(APP_ID, "tree/nested/b.txt") is True
    assert device.documents_ls(APP_ID, "tree") == ["a.txt", "nested"]

    out_dir = tmp_path / "pulled"
    assert device.documents_pull(APP_ID, "tree", out_dir) is True
    assert (out_dir / "a.txt").read_text() == "a"
    assert (out_dir / "nested" / "b.txt").read_text() == "b"

    assert device.documents_rm(APP_ID, "tree") is True
    assert device.documents_exists(APP_ID, "tree") is False


def test_documents_pull_missing_returns_false(windows_device, tmp_path: Path) -> None:
    device, _runner, _app_dir = windows_device
    device._doc_dir = tmp_path / "docroot"
    assert device.documents_pull(APP_ID, "nope.txt", tmp_path / "out.txt") is False


def test_documents_rm_missing_returns_false(windows_device, tmp_path: Path) -> None:
    device, _runner, _app_dir = windows_device
    device._doc_dir = tmp_path / "docroot"
    assert device.documents_rm(APP_ID, "nope.txt") is False


def test_documents_path_rejects_parent_segments(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    with pytest.raises(ValueError, match=r"\.\."):
        device._documents_path("../escape.txt")


def test_exe_dir_uses_cached_exe_parent(windows_device) -> None:
    device, _runner, app_dir = windows_device
    exe = _mark_installed(device, app_dir)
    assert device.exe_dir == exe.parent
    assert device.exe_dir == app_dir / PKG_VERSION


def test_exe_dir_none_when_not_installed(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    assert device.exe_dir is None


def test_local_path_is_unity_data_dir(windows_device) -> None:
    device, _runner, app_dir = windows_device
    _mark_installed(device, app_dir)
    assert device.local_path == app_dir / PKG_VERSION / "App_Data"


def test_local_path_raises_when_not_installed(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    with pytest.raises(FileNotFoundError, match="not installed"):
        _ = device.local_path


def test_persistent_path_matches_doc_dir(windows_device) -> None:
    device, _runner, _app_dir = windows_device
    assert device.persistent_path == device._doc_dir


def test_pull2_local_file(windows_device, tmp_path: Path) -> None:
    device, _runner, app_dir = windows_device
    _mark_installed(device, app_dir)
    data_file = device.local_path / "Player.log"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text("log")

    out = tmp_path / "out" / "Player.log"
    assert device.pull2(AppDataPath.Local, "Player.log", out) is True
    assert out.read_text() == "log"


def test_pull2_persistent_file(windows_device, tmp_path: Path) -> None:
    device, _runner, _app_dir = windows_device
    device._doc_dir = tmp_path / "docroot"
    src = device._doc_dir / "save.dat"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("save")

    out = tmp_path / "pulled.dat"
    assert device.pull2(AppDataPath.Persistent, "save.dat", out) is True
    assert out.read_text() == "save"


def test_pull2_missing_returns_false(windows_device, tmp_path: Path) -> None:
    device, _runner, app_dir = windows_device
    _mark_installed(device, app_dir)
    device.local_path.mkdir(parents=True, exist_ok=True)
    assert device.pull2(AppDataPath.Local, "missing.txt", tmp_path / "out.txt") is False


def test_pull2_rejects_empty_remote_and_parent_segments(windows_device) -> None:
    device, _runner, app_dir = windows_device
    _mark_installed(device, app_dir)
    out = Path("out.txt")
    with pytest.raises(ValueError, match="remote is required"):
        device.pull2(AppDataPath.Local, "", out)
    with pytest.raises(ValueError, match=r"\.\."):
        device.pull2(AppDataPath.Local, "../escape.txt", out)


def _make_package_zip(tmp_path: Path) -> Path:
    """Build a ``MyApp_v1.zip`` that extracts to ``MyApp_v1/App.exe``."""
    package = tmp_path / PKG_NAME
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(f"{PKG_VERSION}/{APP_ID}", "")
    return package


def _backdate(path: Path, days: float) -> None:
    """Set ``path`` mtime/atime to ``days`` in the past."""
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_install_prunes_stale_package_dirs(windows_device, tmp_path: Path) -> None:
    device, _runner, app_dir = windows_device
    stale = app_dir / "OldApp_v0"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("x")
    _backdate(stale, days=2)

    fresh = app_dir / "RecentApp"
    fresh.mkdir(parents=True)

    package = _make_package_zip(tmp_path)
    assert device.install(package, app_id=APP_ID) is True

    assert not stale.exists()
    assert fresh.exists()
    assert (app_dir / PKG_VERSION / APP_ID).exists()


def test_install_keeps_stale_dirs_when_retention_disabled(
    windows_device, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device, _runner, app_dir = windows_device
    monkeypatch.setenv("IDEVICE_APP_RETENTION_DAYS", "0")
    stale = app_dir / "OldApp_v0"
    stale.mkdir(parents=True)
    _backdate(stale, days=5)

    package = _make_package_zip(tmp_path)
    assert device.install(package, app_id=APP_ID) is True

    assert stale.exists()


def test_cleanup_old_packages_skips_files_and_removes_old_dirs(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    _backdate(old_dir, days=3)
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    stray_file = tmp_path / "stray.txt"
    stray_file.write_text("keep")
    _backdate(stray_file, days=10)

    assert cleanup_old_packages(tmp_path, retention_days=1) == 1
    assert not old_dir.exists()
    assert new_dir.exists()
    assert stray_file.exists()


def test_cleanup_old_packages_noop_when_disabled_or_missing(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    _backdate(old_dir, days=3)

    assert cleanup_old_packages(tmp_path, retention_days=0) == 0
    assert old_dir.exists()
    assert cleanup_old_packages(tmp_path / "missing", retention_days=1) == 0

