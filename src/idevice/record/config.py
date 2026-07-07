"""Environment-based configuration for the record orchestrator.

The controller (``controller/src/worker/engine.rs``) injects ``GAUTO_HOST_*`` and
``GAUTO_DEVICE_*`` variables into each subtask process. The record client reuses
the mac-host coordinates (``GAUTO_HOST_TYPE`` / ``GAUTO_HOST_IP`` /
``GAUTO_DEVICE_UDID``) since the iRecord server runs on the same mac host, and
adds an ``IRECORD_PORT`` for the iRecord control-server port.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

DEFAULT_RECORD_PORT = 18300
DEFAULT_HTTP_TIMEOUT = 60.0
DEFAULT_SCRCPY_BINARY = "scrcpy.exe"
DEFAULT_STOP_TIMEOUT = 15.0


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


def scrcpy_binary() -> str:
    """Return the scrcpy CLI binary path (``IDEVICE_SCRCPY_BINARY``).

    The Android recorder shells out to ``scrcpy`` on the host that also runs adb;
    override this when scrcpy is not on ``PATH``.
    """
    return os.environ.get("IDEVICE_SCRCPY_BINARY", DEFAULT_SCRCPY_BINARY)


def record_output_dir() -> Path:
    """Return the directory scrcpy recordings are written to (``IDEVICE_RECORD_OUTPUT_DIR``).

    Defaults to ``~/.idevice/records``. The directory is created on demand by the
    recorder when a recording is started.
    """
    raw = os.environ.get("IDEVICE_RECORD_OUTPUT_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".idevice" / "records"


def scrcpy_extra_args() -> list[str]:
    """Return extra scrcpy CLI args (``IDEVICE_SCRCPY_EXTRA_ARGS``).

    Parsed with :func:`shlex.split` so quoting works cross-platform (e.g.
    ``--max-size 1280 --video-bit-rate 8M``). Returns an empty list when unset.
    """
    raw = os.environ.get("IDEVICE_SCRCPY_EXTRA_ARGS", "")
    return shlex.split(raw) if raw.strip() else []


def stop_timeout() -> float:
    """Return seconds to wait for scrcpy to finalize on stop (``IDEVICE_RECORD_STOP_TIMEOUT``)."""
    raw = os.environ.get("IDEVICE_RECORD_STOP_TIMEOUT")
    return float(raw) if raw else DEFAULT_STOP_TIMEOUT
