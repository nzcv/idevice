"""Unit tests for the shared iwda2 mixin on ``IOSDevice3``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.common.iwda2 import IWDA2Mixin
from idevice.device.ios3.device import IOSDevice3

APP_ID = "com.example.app"
BINARY = "/opt/ios3/bin/pymobiledevice3"
UDID = "target-udid"
DEVICE_IP = "192.0.2.10"


@pytest.fixture
def ios3_device(tmp_path: Path) -> IOSDevice3:
    """Build an IOSDevice3 with a mocked CLI and isolated cache."""
    with patch("idevice.device.ios3.device.shutil.which", return_value=BINARY):
        with patch(
            "idevice.device.ios3.device.ios3_binary", return_value=BINARY
        ):
            return IOSDevice3(
                UDID,
                device_ip=DEVICE_IP,
                package_name=APP_ID,
                cache_dir=tmp_path / "cache",
            )


def test_ios3_reuses_shared_iwda2_methods() -> None:
    assert IOSDevice3.tap is IWDA2Mixin.tap
    assert IOSDevice3.start_moniter is IWDA2Mixin.start_moniter
    assert IOSDevice3.stop_moniter is IWDA2Mixin.stop_moniter


def test_tap_calls_iwda2_with_normalized_coordinates_and_bundle_id(
    ios3_device: IOSDevice3,
) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        ios3_device.tap(0.25, 0.75, app_id="com.example.foreground")

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/tap",
        params={
            "x": 0.25,
            "y": 0.75,
            "bundleId": "com.example.foreground",
        },
        timeout=30.0,
    )


def test_start_moniter_calls_iwda2_with_duration(
    ios3_device: IOSDevice3,
) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        assert ios3_device.start_moniter(duration=90) is True

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/monitor/start",
        params={"duration": "90"},
        timeout=30.0,
    )


def test_stop_moniter_calls_iwda2(ios3_device: IOSDevice3) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        assert ios3_device.stop_moniter() is True

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/monitor/stop",
        params=None,
        timeout=30.0,
    )
