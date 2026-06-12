"""Unit tests for the device factory."""

from __future__ import annotations

import pytest

from idevice.device.factory import create_device


def test_create_device_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="Unsupported platform"):
        create_device("harmonyos")  # type: ignore[arg-type]
