"""Unit tests for ``IOSDevice3`` Documents sandbox helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idevice.device.ios3.device import IOSDevice3, IOSDevice3Error


@pytest.fixture
def ios3_device() -> IOSDevice3:
    with patch("idevice.device.ios3.device.shutil.which", return_value="/opt/ios3/bin/pymobiledevice3"):
        with patch("idevice.device.ios3.device.SubprocessRunner"):
            with patch(
                "idevice.device.ios3.device.ios3_binary",
                return_value="/opt/ios3/bin/pymobiledevice3",
            ):
                yield IOSDevice3("target-udid", device_ip="")


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
                async with ios3_device._documents_afc("com.example.app"):
                    pass

    import asyncio

    asyncio.run(_run())
