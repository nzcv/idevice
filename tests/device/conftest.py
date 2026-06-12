"""Shared fixtures for physical-device integration tests.

Environment variables
---------------------
IDEVICE_IOS3_UDID:
    Device UDID (required for all ios3 integration tests).
IDEVICE_IOS3_INSTALLED_APP_ID:
    Bundle id of an app already on the device for launch/stop checks
    (default: ``com.apple.Preferences``).
IDEVICE_IOS3_TEST_IPA:
    Path to a test ``.ipa`` for install/uninstall round-trip tests.
IDEVICE_IOS3_TEST_APP_ID:
    Bundle id inside the test IPA (required when ``IDEVICE_IOS3_TEST_IPA`` is set).
IDEVICE_IOS3_SANDBOX_APP_ID:
    Optional bundle id for app-sandbox push/pull tests.
IDEVICE_IOS3_SANDBOX_DOCUMENTS:
    Use ``apps push/pull --documents`` (default: ``1`` when sandbox app id is set).
IDEVICE_IOS3_SANDBOX_REMOTE:
    Remote file name under the app Documents directory (default:
    ``idevice_integration_sandbox_test.txt``).
IDEVICE_IOS3_BINARY:
    pymobiledevice3 CLI path (see ``idevice.device.config.ios3_binary``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from idevice.device.ios3.device import IOSDevice3


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@pytest.fixture(scope="session")
def ios3_udid() -> str:
    """Connected device UDID from ``IDEVICE_IOS3_UDID``."""
    udid = _env("IDEVICE_IOS3_UDID")
    if not udid:
        pytest.skip("Set IDEVICE_IOS3_UDID to the connected device UDID")
    return udid


@pytest.fixture(scope="session")
def ios3_device(ios3_udid: str) -> IOSDevice3:
    """``IOSDevice3`` bound to the session UDID."""
    return IOSDevice3(ios3_udid, device_ip="")


@pytest.fixture(scope="session")
def ios3_installed_app_id() -> str:
    """Bundle id used for launch/stop integration tests."""
    return _env("IDEVICE_IOS3_INSTALLED_APP_ID") or "com.apple.Preferences"


@pytest.fixture(scope="session")
def ios3_test_ipa() -> Path:
    """Path to a test IPA (``IDEVICE_IOS3_TEST_IPA``)."""
    raw = _env("IDEVICE_IOS3_TEST_IPA")
    if not raw:
        pytest.skip("Set IDEVICE_IOS3_TEST_IPA to run install/uninstall integration tests")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        pytest.skip(f"IDEVICE_IOS3_TEST_IPA not found: {path}")
    return path


@pytest.fixture(scope="session")
def ios3_test_app_id(ios3_test_ipa: Path) -> str:
    """Bundle id of the test IPA (``IDEVICE_IOS3_TEST_APP_ID``)."""
    app_id = _env("IDEVICE_IOS3_TEST_APP_ID")
    if not app_id:
        pytest.skip(
            "Set IDEVICE_IOS3_TEST_APP_ID (bundle id matching IDEVICE_IOS3_TEST_IPA)"
        )
    return app_id


@pytest.fixture(scope="session")
def ios3_sandbox_app_id() -> str:
    """Bundle id for app-container push/pull tests."""
    app_id = _env("IDEVICE_IOS3_SANDBOX_APP_ID")
    if not app_id:
        pytest.skip("Set IDEVICE_IOS3_SANDBOX_APP_ID for sandbox push/pull tests")
    return app_id


@pytest.fixture(scope="session")
def ios3_sandbox_documents() -> bool:
    """Whether sandbox transfers target the app Documents directory only."""
    raw = _env("IDEVICE_IOS3_SANDBOX_DOCUMENTS")
    if raw is None:
        return True
    return raw.lower() in ("1", "true", "yes")


@pytest.fixture(scope="session")
def ios3_sandbox_remote() -> str:
    """Remote path for sandbox push/pull (relative to app container root)."""
    return _env("IDEVICE_IOS3_SANDBOX_REMOTE") or "idevice_integration_sandbox_test.txt"

