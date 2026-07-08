"""Unit tests for the Device factory facade."""

from __future__ import annotations

import pytest

from idevice.device.device import Device
from idevice.device.dummy.device import DummyDevice


def test_create_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="Invalid platform"):
        Device.create("harmonyos", device_id="abc", device_ip="")


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

