"""Environment-based configuration for the host orchestrator.

The controller (``controller/src/worker/engine.rs``) injects ``GAUTO_HOST_*`` and
``GAUTO_DEVICE_*`` variables into each subtask process; these accessors read them
with sensible defaults so a host can be built directly from the environment.
"""

from __future__ import annotations

import os

DEFAULT_KEEPER_PORT = 18200
DEFAULT_RUNNER_PORT = 18100
DEFAULT_HTTP_TIMEOUT = 60.0
DEFAULT_READY_TIMEOUT = 300.0


def host_type() -> str:
    """Return the host type (``GAUTO_HOST_TYPE``).

    The controller (``controller/src/worker/engine.rs``) injects one of
    ``android`` | ``ios`` | ``windows`` | ``macos``. Only ``macos`` runs the
    keeper-backed host; every other value resolves to a dummy host.
    """
    return os.environ.get("GAUTO_HOST_TYPE", "")


def keeper_ip() -> str:
    """Return the EndlessKeeper control-server IP (``GAUTO_HOST_IP``)."""
    return os.environ.get("GAUTO_HOST_IP", "")


def keeper_id() -> str:
    """Return the keeper/controller id (``GAUTO_HOST_ID``)."""
    return os.environ.get("GAUTO_HOST_ID", "")


def keeper_port() -> int:
    """Return the keeper control-server port (``GAUTO_HOST_PORT``)."""
    raw = os.environ.get("GAUTO_HOST_PORT")
    return int(raw) if raw else DEFAULT_KEEPER_PORT


def device_udid() -> str:
    """Return the target device UDID (``GAUTO_DEVICE_UDID``)."""
    return os.environ.get("GAUTO_DEVICE_UDID", "")


def device_ip() -> str:
    """Return the target device IP (``GAUTO_DEVICE_IP``)."""
    return os.environ.get("GAUTO_DEVICE_IP", "")


def runner_port() -> int:
    """Return the on-device runner port (``GAUTO_DEVICE_SERVER_PORT``).

    Defaults to the port EndlessKeeper injects into the runner environment.
    """
    raw = os.environ.get("GAUTO_DEVICE_SERVER_PORT")
    return int(raw) if raw else DEFAULT_RUNNER_PORT


def http_timeout() -> float:
    """Return the per-request HTTP timeout in seconds (``IDEVICE_HOST_TIMEOUT``)."""
    raw = os.environ.get("IDEVICE_HOST_TIMEOUT")
    return float(raw) if raw else DEFAULT_HTTP_TIMEOUT


def ready_timeout() -> float:
    """Return the runner readiness timeout in seconds (``IDEVICE_HOST_READY_TIMEOUT``)."""
    raw = os.environ.get("IDEVICE_HOST_READY_TIMEOUT")
    return float(raw) if raw else DEFAULT_READY_TIMEOUT

def bundle_id() -> str:
    """Return the bundle id (``GAUTO_BUNDLE_ID``)."""
    return os.environ.get("GAUTO_BUNDLE_ID", "")