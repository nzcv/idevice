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
DEFAULT_SCRCPY_MAX_SIZE = 1280
DEFAULT_SCRCPY_VIDEO_BIT_RATE = "4M"
DEFAULT_STOP_TIMEOUT = 15.0
DEFAULT_FFMPEG_BINARY = "ffmpeg.exe"
DEFAULT_FFMPEG_FRAMERATE = 30


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


def scrcpy_max_size() -> int | None:
    """Return the scrcpy ``--max-size`` cap (``IDEVICE_SCRCPY_MAX_SIZE``).

    Caps the longest video dimension so the Android recorder defaults to 720p
    (``1280``, i.e. 720x1280 portrait / 1280x720 landscape). Set the env var to
    ``0`` (or empty) to disable the cap and record at the device's native
    resolution.

    Returns:
        The max-size in pixels, or ``None`` when disabled.
    """
    raw = os.environ.get("IDEVICE_SCRCPY_MAX_SIZE")
    if raw is None:
        return DEFAULT_SCRCPY_MAX_SIZE
    raw = raw.strip()
    if not raw:
        return None
    value = int(raw)
    return value if value > 0 else None


def scrcpy_video_bit_rate() -> str | None:
    """Return the scrcpy ``--video-bit-rate`` (``IDEVICE_SCRCPY_VIDEO_BIT_RATE``).

    Sets the encoding bit rate so the Android recorder defaults to ``4M`` (scrcpy's
    own default is ``8M``), trading some quality for smaller files. Set the env var
    to ``0`` (or empty) to drop the flag and use scrcpy's default bit rate.

    Returns:
        The bit rate string (e.g. ``"4M"``), or ``None`` when disabled.
    """
    raw = os.environ.get("IDEVICE_SCRCPY_VIDEO_BIT_RATE")
    if raw is None:
        return DEFAULT_SCRCPY_VIDEO_BIT_RATE
    raw = raw.strip()
    if not raw or raw == "0":
        return None
    return raw


def stop_timeout() -> float:
    """Return seconds to wait for scrcpy to finalize on stop (``IDEVICE_RECORD_STOP_TIMEOUT``)."""
    raw = os.environ.get("IDEVICE_RECORD_STOP_TIMEOUT")
    return float(raw) if raw else DEFAULT_STOP_TIMEOUT


def ffmpeg_binary() -> str:
    """Return the ffmpeg CLI binary path (``IDEVICE_FFMPEG_BINARY``).

    The Windows recorder shells out to ``ffmpeg`` on the local host to capture the
    desktop via the ``gdigrab`` input device; override this when ffmpeg is not on
    ``PATH``.
    """
    return os.environ.get("IDEVICE_FFMPEG_BINARY", DEFAULT_FFMPEG_BINARY)


def ffmpeg_framerate() -> int:
    """Return the desktop capture frame rate (``IDEVICE_FFMPEG_FRAMERATE``)."""
    raw = os.environ.get("IDEVICE_FFMPEG_FRAMERATE")
    return int(raw) if raw else DEFAULT_FFMPEG_FRAMERATE


def ffmpeg_encoder_cache_file() -> Path:
    """Return the path of the persisted ffmpeg encoder-selection cache.

    The Windows recorder probes ffmpeg once to pick the cheapest usable encoder;
    the chosen profile is cached here so subsequent processes skip the probe.
    Defaults to ``~/.idevice/ffmpeg_encoder_cache.json``; override with
    ``IDEVICE_FFMPEG_ENCODER_CACHE``.
    """
    raw = os.environ.get("IDEVICE_FFMPEG_ENCODER_CACHE")
    if raw:
        return Path(raw)
    return Path.home() / ".idevice" / "ffmpeg_encoder_cache.json"


def ffmpeg_input() -> str:
    """Return the foreground-activation target (``GAUTO_PACKAGE_NAME``).

    The Windows recorder captures the primary monitor via ``ddagrab`` (not a
    specific window), so this value is no longer an ffmpeg capture target;
    instead it names the app whose window is brought to the foreground before
    recording starts. The controller injects the app under test as
    ``GAUTO_PACKAGE_NAME`` (e.g. ``MyApp.exe``). Defaults to ``desktop``, which
    means "record the primary monitor as-is without foregrounding any app".
    """
    return os.environ.get("GAUTO_PACKAGE_NAME", "desktop")