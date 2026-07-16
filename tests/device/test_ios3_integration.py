"""Integration tests for ``IOSDevice3`` on a connected physical iOS device.

Run (example)::

    export IDEVICE_IOS3_UDID=00000000-0000000000000000
    uv run pytest tests/device/test_ios3_integration.py -m integration -v

Optional install round-trip::

    export IDEVICE_IOS3_TEST_IPA=/path/to/app.ipa
    export IDEVICE_IOS3_TEST_APP_ID=com.example.app
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from idevice.device.base.errors import AppNotInstalledError, CommandExecutionError
from idevice.device.device import Device
from idevice.device.ios3.device import IOSDevice3


def _skip_if_sandbox_transfer_unavailable(exc: CommandExecutionError) -> None:
    detail = f"{exc} {exc.stderr} {exc.stdout}"
    markers = (
        "InstallationLookupFailed",
        "ApplicationLookupFailed",
        "FILE_OPEN failed",
        "not found during afc",
    )
    if any(marker in detail for marker in markers):
        pytest.skip(
            "App sandbox transfer unavailable (House Arrest / AFC). "
            "Ensure the app is installed, supports file sharing, and the device "
            "trusts this host."
        )
    raise exc

pytestmark = pytest.mark.integration


def test_factory_creates_ios3_device(ios3_udid: str) -> None:
    device = Device.create("ios", device_id=ios3_udid, device_ip="")
    assert isinstance(device, IOSDevice3)
    assert device.device_id == ios3_udid


def test_is_installed_system_app(
    ios3_device: IOSDevice3,
    ios3_installed_app_id: str,
) -> None:
    assert ios3_device.is_installed(ios3_installed_app_id) is True


def test_is_installed_unknown_bundle_returns_false(ios3_device: IOSDevice3) -> None:
    assert ios3_device.is_installed("com.idevice.integration.nonexistent") is False


def test_launch_and_stop_installed_app(
    ios3_device: IOSDevice3,
    ios3_installed_app_id: str,
) -> None:
    if not ios3_device.is_installed(ios3_installed_app_id):
        pytest.skip(f"{ios3_installed_app_id} is not installed on this device")
    ios3_device.launch_app(ios3_installed_app_id)
    ios3_device.stop_app(ios3_installed_app_id)


def test_launch_not_installed_raises_app_not_installed(
    ios3_device: IOSDevice3,
) -> None:
    with pytest.raises(AppNotInstalledError, match="not installed"):
        ios3_device.launch_app("com.idevice.integration.nonexistent")


def test_stop_app_rejects_empty_when_no_bound_package(
    ios3_device: IOSDevice3,
) -> None:
    """Empty/omitted app_id raises when the device has no bound package_name."""
    assert ios3_device.package_name == ""
    with pytest.raises(ValueError, match="app_id"):
        ios3_device.stop_app("")
    with pytest.raises(ValueError, match="app_id"):
        ios3_device.stop_app()


def test_host_is_running_returns_bool(ios3_device: IOSDevice3) -> None:
    assert isinstance(ios3_device.host_is_running(), bool)


@pytest.mark.parametrize(
    ("expect_running", "env_value"),
    [
        pytest.param(True, "1", id="wda_running"),
        pytest.param(False, "0", id="wda_not_running"),
    ],
)
def test_host_is_running_expected_state(
    ios3_device: IOSDevice3,
    expect_running: bool,
    env_value: str,
) -> None:
    """Run only when ``IDEVICE_IOS3_EXPECT_WDA`` is ``1`` (running) or ``0`` (not)."""
    configured = os.environ.get("IDEVICE_IOS3_EXPECT_WDA", "").strip()
    if configured != env_value:
        pytest.skip(
            "Set IDEVICE_IOS3_EXPECT_WDA=1 or =0 to assert WDA running state"
        )
    assert ios3_device.host_is_running() is expect_running


def test_swipe_not_implemented(ios3_device: IOSDevice3) -> None:
    with pytest.raises(NotImplementedError, match="swipe"):
        ios3_device.swipe(0, 0, 100, 100)


def test_afc_push_pull_roundtrip(ios3_device: IOSDevice3, tmp_path: Path) -> None:
    remote_name = "idevice_integration_afc_test.txt"
    payload = b"idevice ios3 afc integration\n"
    local_push = tmp_path / "push.txt"
    local_push.write_bytes(payload)
    local_pull = tmp_path / "pull.txt"

    try:
        ios3_device.push(local_push, remote_name)
        ios3_device.pull(remote_name, local_pull)
        assert local_pull.read_bytes() == payload
    finally:
        if local_pull.exists():
            local_pull.unlink(missing_ok=True)
        if local_push.exists():
            local_push.unlink(missing_ok=True)


def test_apps_push_pull_roundtrip(
    ios3_device: IOSDevice3,
    ios3_sandbox_app_id: str,
    ios3_sandbox_documents: bool,
    ios3_sandbox_remote: str,
    tmp_path: Path,
) -> None:
    if not ios3_device.is_installed(ios3_sandbox_app_id):
        pytest.skip(f"{ios3_sandbox_app_id} is not installed on this device")

    payload = b"idevice ios3 sandbox integration\n"
    local_push = tmp_path / "sandbox_push.txt"
    local_push.write_bytes(payload)
    local_pull = tmp_path / "sandbox_pull.txt"

    try:
        ios3_device.push(
            local_push,
            ios3_sandbox_remote,
            app_id=ios3_sandbox_app_id,
            documents_only=ios3_sandbox_documents,
        )
        ios3_device.pull(
            ios3_sandbox_remote,
            local_pull,
            app_id=ios3_sandbox_app_id,
            documents_only=ios3_sandbox_documents,
        )
    except CommandExecutionError as exc:
        _skip_if_sandbox_transfer_unavailable(exc)

    if not local_pull.is_file():
        pytest.skip(
            "App sandbox pull did not create a local file (House Arrest / AFC may have "
            "failed while pymobiledevice3 still exited 0). Check device trust and app "
            "file-sharing entitlements."
        )
    assert local_pull.read_bytes() == payload


def test_install_uninstall_roundtrip(
    ios3_device: IOSDevice3,
    ios3_test_ipa: Path,
    ios3_test_app_id: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    cache_dir = tmp_path_factory.mktemp("ios3_app_cache")
    device = IOSDevice3(ios3_device.device_id, device_ip="", cache_dir=cache_dir)

    if device.is_installed(ios3_test_app_id):
        device.uninstall(ios3_test_app_id)

    try:
        device.install(ios3_test_ipa, app_id=ios3_test_app_id)
        assert device.is_installed(ios3_test_app_id) is True
        pkg_name = device.get_installed_pkg_name(ios3_test_app_id)
        assert pkg_name is not None
        assert pkg_name.version == ios3_test_ipa.stem
        assert pkg_name.path == ios3_test_ipa.name
    finally:
        if device.is_installed(ios3_test_app_id):
            device.uninstall(ios3_test_app_id)

    assert device.is_installed(ios3_test_app_id) is False
    assert device.get_installed_pkg_name(ios3_test_app_id) is None


def test_push_rejects_missing_local_file(
    ios3_device: IOSDevice3,
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        ios3_device.push(tmp_path / "missing.bin", "remote.bin")


def test_push_rejects_empty_remote(ios3_device: IOSDevice3, tmp_path: Path) -> None:
    local_file = tmp_path / "empty_remote_test.txt"
    local_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="remote"):
        ios3_device.push(local_file, "")
