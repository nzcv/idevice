"""Unit tests for the Device factory facade."""

from __future__ import annotations

import pytest

from idevice.device.device import Device


def test_create_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="Invalid platform"):
        Device.create("harmonyos", device_id="abc", device_ip="")
