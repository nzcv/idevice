"""Unit tests for the Device factory facade."""

from __future__ import annotations

import pytest

from idevice.device.device import Device
from idevice.device.dummy.device import DummyDevice
from idevice.device.ios4.device import IOSDevice4


def test_create_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="Invalid platform"):
        Device.create("harmonyos", device_id="abc", device_ip="")


def test_create_binds_package_name(monkeypatch: pytest.MonkeyPatch) -> None:
    Device.reset()
    monkeypatch.setattr(
        "idevice.device.android.device.shutil.which", lambda _name: "adb"
    )
    device = Device.create(
        "android",
        device_id="emulator-5554",
        device_ip="",
        package_name="com.example.app",
    )
    assert device.package_name == "com.example.app"
    Device.reset()


def test_create_ios4_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    Device.reset()
    monkeypatch.setattr(
        "idevice.device.ios4.device.shutil.which",
        lambda _name: "/usr/local/bin/ios4",
    )

    device = Device.create(
        "ios4",
        device_id="00000000-0000000000000000",
        device_ip="",
        package_name="com.example.game",
    )

    assert isinstance(device, IOSDevice4)
    assert device.platform == "ios4"
    assert device.package_name == "com.example.game"
    Device.reset()


def test_from_env_windows_requires_company_and_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Device.reset()
    monkeypatch.setenv("GAUTO_PLATFORM", "pc")
    monkeypatch.setenv("GAUTO_DEVICE_UDID", "local")
    monkeypatch.delenv("GAUTO_COMPANY_NAME", raising=False)
    monkeypatch.delenv("GAUTO_PACKAGE_NAME", raising=False)

    device = Device.from_env()

    assert isinstance(device, DummyDevice)
    Device.reset()


def test_from_env_requires_package_name_for_android(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Device.reset()
    monkeypatch.setenv("GAUTO_PLATFORM", "android")
    monkeypatch.setenv("GAUTO_DEVICE_UDID", "emulator-5554")
    monkeypatch.delenv("GAUTO_PACKAGE_NAME", raising=False)

    device = Device.from_env()

    assert isinstance(device, DummyDevice)
    Device.reset()


def test_dummy_pull2_is_noop(tmp_path) -> None:
    from idevice.device.base.device import AppDataPath

    device = DummyDevice("unconfigured")
    assert device.pull2(AppDataPath.Persistent, "x", tmp_path / "out") is False


def test_ping_empty_ip_returns_false() -> None:
    device = DummyDevice("unconfigured", device_ip="")
    assert device.ping() is False


def test_ping_uses_subprocess_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    from idevice.device.base import device as device_mod

    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):  # noqa: ANN001
        calls.append(list(command))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(device_mod.subprocess, "run", fake_run)
    device = DummyDevice("unconfigured", device_ip="10.0.0.1")
    assert device.ping() is True
    assert calls and calls[0][-1] == "10.0.0.1"
    assert device.ping("192.168.1.1") is True
    assert calls[-1][-1] == "192.168.1.1"


def test_optional_device_interfaces_report_unsupported_platform() -> None:
    device = DummyDevice("configured for test", platform="dummy")

    startup_thread = device.run_iwda2()
    startup_thread.join(timeout=1)
    assert startup_thread.is_alive() is False
    assert device.stop_iwda2() is None
    assert device.xmemory_shot("snapshot.memgraph").name == "snapshot.memgraph"
