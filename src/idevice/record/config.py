"""Environment-based configuration for the record orchestrator.

The controller (``controller/src/worker/engine.rs``) injects ``GAUTO_HOST_*`` and
``GAUTO_DEVICE_*`` variables into each subtask process. The record client reuses
the mac-host coordinates (``GAUTO_HOST_TYPE`` / ``GAUTO_HOST_IP`` /
``GAUTO_DEVICE_UDID``) since the iRecord server runs on the same mac host, and
adds an ``IRECORD_PORT`` for the iRecord control-server port.
"""

from __future__ import annotations

import os

DEFAULT_RECORD_PORT = 18300
DEFAULT_HTTP_TIMEOUT = 60.0


def record_type() -> str:
    """Return the host type (``GAUTO_HOST_TYPE``).

    The controller injects one of ``android`` | ``ios`` | ``windows`` |
    ``macos``. Only ``macos`` runs the iRecord-backed recorder; every other
    value resolves to a dummy recorder.
    """
    return os.environ.get("GAUTO_HOST_TYPE", "")


def server_ip() -> str:
    """Return the iRecord control-server IP (``GAUTO_HOST_IP``).

    The iRecord server runs on the mac host, so it reuses the keeper/mac-host IP.
    """
    return os.environ.get("GAUTO_HOST_IP", "")


def server_port() -> int:
    """Return the iRecord control-server port (``IRECORD_PORT``)."""
    raw = os.environ.get("IRECORD_PORT")
    return int(raw) if raw else DEFAULT_RECORD_PORT


def device_udid() -> str:
    """Return the target device UDID (``GAUTO_DEVICE_UDID``)."""
    return os.environ.get("GAUTO_DEVICE_UDID", "")


def http_timeout() -> float:
    """Return the per-request HTTP timeout in seconds (``IDEVICE_RECORD_TIMEOUT``)."""
    raw = os.environ.get("IDEVICE_RECORD_TIMEOUT")
    return float(raw) if raw else DEFAULT_HTTP_TIMEOUT
