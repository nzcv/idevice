"""Unit tests for ``IOSDevice3`` Documents sandbox helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idevice.device.base.device import AppDataPath
from idevice.device.ios3.device import IOSDevice3, IOSDevice3Error

APP_ID = "com.example.app"


@pytest.fixture
def ios3_device() -> IOSDevice3:
    with patch("idevice.device.ios3.device.shutil.which", return_value="/opt/ios3/bin/pymobiledevice3"):
        with patch("idevice.device.ios3.device.SubprocessRunner"):
            with patch(
                "idevice.device.ios3.device.ios3_binary",
                return_value="/opt/ios3/bin/pymobiledevice3",
            ):
                yield IOSDevice3(
                    "target-udid", device_ip="", package_name=APP_ID
                )


def test_documents_afc_rejects_missing_udid_in_tunneld(ios3_device: IOSDevice3) -> None:
    other = MagicMock()
    other.udid = "other-udid"

    async def _run() -> None:
        with patch(
            "pymobiledevice3.tunneld.api.get_tunneld_devices",
            new_callable=AsyncMock,
            return_value=[other],
        ):
            with pytest.raises(IOSDevice3Error, match="target-udid.*not found in tunneld"):
                async with ios3_device._documents_afc(APP_ID):
                    pass

    import asyncio

    asyncio.run(_run())


def test_afc_relative_path_normalizes_and_rejects_parent() -> None:
    assert IOSDevice3._afc_relative_path("Library/Caches/a") == "/Library/Caches/a"
    assert IOSDevice3._afc_relative_path("\\Library\\x") == "/Library/x"
    with pytest.raises(ValueError, match=r"\.\."):
        IOSDevice3._afc_relative_path("../escape")
    with pytest.raises(ValueError, match="remote is required"):
        IOSDevice3._afc_relative_path("")


def test_pull2_persistent_uses_documents_pull(
    ios3_device: IOSDevice3, tmp_path: Path
) -> None:
    out = tmp_path / "out.txt"
    with patch.object(ios3_device, "documents_pull", return_value=True) as pull:
        assert ios3_device.pull2(AppDataPath.Persistent, "save.dat", out) is True
        pull.assert_called_once_with(APP_ID, "save.dat", out)


def test_pull2_local_uses_container_pull(
    ios3_device: IOSDevice3, tmp_path: Path
) -> None:
    out = tmp_path / "out.txt"
    with patch.object(ios3_device, "_container_pull", return_value=True) as pull:
        assert ios3_device.pull2(AppDataPath.Local, "Library/Caches/a", out) is True
        pull.assert_called_once_with(APP_ID, "Library/Caches/a", out)


def test_pull2_requires_package_name(tmp_path: Path) -> None:
    with patch("idevice.device.ios3.device.shutil.which", return_value="/opt/ios3"):
        with patch("idevice.device.ios3.device.SubprocessRunner"):
            with patch(
                "idevice.device.ios3.device.ios3_binary", return_value="/opt/ios3"
            ):
                device = IOSDevice3("udid", device_ip="")
    with pytest.raises(ValueError, match="app_id"):
        device.pull2(AppDataPath.Persistent, "x", tmp_path / "out")
