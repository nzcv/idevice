"""Unit tests for the Device factory facade."""

from __future__ import annotations

import pytest

from idevice.device.device import Device
from idevice.device.dummy.device import DummyDevice


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

