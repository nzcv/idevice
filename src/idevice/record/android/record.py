"""Android scrcpy-backed :class:`RecordBase` implementation.

Runs on the **host** that drives the device via adb: :class:`AndroidRecord`
shells out to the ``scrcpy`` CLI to capture a video-only screen recording of a
USB- (or TCP/IP-) connected Android device directly to a local file. Unlike the
iRecord-backed macOS recorder there is no control server, so ``server_ip`` /
``server_port`` are unused; the recorder simply supervises a local ``scrcpy``
subprocess.
"""

from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from idevice.record import config
from idevice.record.base.errors import RecordError, RecordServerError
from idevice.record.base.record import RecordBase

logger = logging.getLogger(__name__)

_LOG_TAG = "[AndroidRecord]"

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hms]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1, "": 1}


def _parse_timeout_seconds(timeout: float | str | None) -> int | None:
    """Convert a duration (``2h`` / ``30m`` / ``90s`` / seconds) to whole seconds.

    Args:
        timeout: A duration string, a number of seconds, or ``None``.

    Returns:
        The duration in whole seconds, or ``None`` when ``timeout`` is ``None``.

    Raises:
        ValueError: If ``timeout`` cannot be parsed or is not positive.
    """
    if timeout is None:
        return None
    if isinstance(timeout, (int, float)):
        seconds = float(timeout)
    else:
        match = _DURATION_RE.match(timeout)
        if not match:
            raise ValueError(
                f"invalid timeout {timeout!r}; use seconds or a duration like 2h/30m/90s"
            )
        seconds = float(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
    if seconds <= 0:
        raise ValueError(f"timeout must be positive, got {timeout!r}")
    return int(seconds)


class AndroidRecord(RecordBase):
    """Drive scrcpy screen recording of one Android device on the local host."""

    def __init__(
        self,
        record_type: str = "android",
        *,
        device_udid: str,
        server_ip: str = "",
        server_port: int = config.DEFAULT_RECORD_PORT,
        output_dir: Path | None = None,
        scrcpy_binary: str | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        """Bind the recorder to an Android device and locate the scrcpy CLI.

        Args:
            record_type: Host/record type identifier (``android``).
            device_udid: adb serial of the target device. Required and non-empty.
            server_ip: Unused for scrcpy (kept for :class:`RecordBase` parity).
            server_port: Unused for scrcpy (kept for :class:`RecordBase` parity).
            output_dir: Directory recordings are written to; defaults to
                :func:`idevice.record.config.record_output_dir`.
            scrcpy_binary: scrcpy CLI path; defaults to
                :func:`idevice.record.config.scrcpy_binary`.
            extra_args: Extra scrcpy CLI args appended to every recording;
                defaults to :func:`idevice.record.config.scrcpy_extra_args`.

        Raises:
            ValueError: If ``device_udid`` is empty.
            RecordError: If the scrcpy CLI cannot be found.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        # scrcpy is local (no control server), so the base coordinate validation
        # is bypassed and the backing attributes are set directly.
        self._record_type = record_type
        self._server_ip = server_ip
        self._server_port = int(server_port)
        self._device_udid = device_udid
        self._output_dir = Path(output_dir) if output_dir else config.record_output_dir()
        self._scrcpy_binary = scrcpy_binary or config.scrcpy_binary()
        self._extra_args = list(extra_args) if extra_args is not None else config.scrcpy_extra_args()

        resolved = shutil.which(self._scrcpy_binary)
        if resolved is None and not Path(self._scrcpy_binary).is_file():
            logger.error(f"{_LOG_TAG} `{self._scrcpy_binary}` CLI not found on PATH")
            raise RecordError(
                f"`{self._scrcpy_binary}` CLI not found on PATH. "
                "Install scrcpy: https://github.com/Genymobile/scrcpy"
            )

        self._process: subprocess.Popen[bytes] | None = None
        self._output_path: Path | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        # The auto-stop timeout is enforced in-process (see start()) rather than
        # via scrcpy's --time-limit, so stop() always finalizes the recording.
        self._auto_stop_timer: threading.Timer | None = None

    @classmethod
    def from_env(cls) -> AndroidRecord:
        """Build an :class:`AndroidRecord` from the environment.

        Reads ``GAUTO_HOST_TYPE`` and ``GAUTO_DEVICE_UDID`` (the adb serial), plus
        the optional ``IDEVICE_SCRCPY_*`` / ``IDEVICE_RECORD_OUTPUT_DIR`` overrides.

        Returns:
            AndroidRecord: A recorder bound to the device described by the
            environment.

        Raises:
            ValueError: If ``GAUTO_DEVICE_UDID`` is empty.
            RecordError: If the scrcpy CLI cannot be found.
        """
        return cls(
            record_type=config.record_type(),
            device_udid=config.device_udid(),
        )

    def _is_running(self) -> bool:
        """Return ``True`` while the supervised scrcpy process is still alive."""
        return self._process is not None and self._process.poll() is None

    def health(self) -> bool:
        """Return ``True`` if scrcpy is available and the device is adb-connected."""
        from idevice.device.base.runner import SubprocessRunner
        from idevice.device.config import adb_binary

        try:
            result = SubprocessRunner().run([adb_binary(), "devices"], check=False)
        except Exception as exc:  # noqa: BLE001 - boolean probe
            logger.debug(f"{_LOG_TAG} adb health failed: {exc}")
            return False
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == self._device_udid and parts[1] == "device":
                return True
        logger.debug(f"{_LOG_TAG} device {self._device_udid} not in adb `device` state")
        return False

    def start(self, *, timeout: float | str | None = None) -> dict:
        """Start a scrcpy recording of the bound device.

        Args:
            timeout: Optional auto-stop duration; accepts seconds or a duration
                string (``2h`` / ``30m`` / ``90s``). Enforced in-process by a
                watchdog that triggers :meth:`stop`. ``None`` records until an
                explicit :meth:`stop`.

        Returns:
            dict: The recording status report (see :meth:`status`).

        Raises:
            RecordServerError: If scrcpy fails to launch.
            ValueError: If ``timeout`` cannot be parsed.
        """
        if self._is_running():
            logger.warning(f"{_LOG_TAG} recording already running for {self._device_udid}")
            return self.status()

        self._output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_udid = re.sub(r"[^A-Za-z0-9._-]", "_", self._device_udid)
        output_path = self._output_dir / f"{safe_udid}_{stamp}.mp4"
        
        is_windows = sys.platform == "win32"
        seconds = _parse_timeout_seconds(timeout)

        command = [self._scrcpy_binary, "-s", self._device_udid, "--no-window"]
        if is_windows:
            # Run headless. `--no-audio-playback` avoids requiring a host audio
            # device (the machine may have none) while still recording the
            # device's audio track. stop() finalizes the MP4 with a clean
            # Ctrl+Break; that only works without scrcpy's `--time-limit`, which
            # otherwise crashes the process during shutdown (no `moov` atom).
            command.append("--no-audio-playback")
        else:
            command.append("--no-playback")
        command += ["--record", str(output_path)]
        command.extend(self._extra_args)

        logger.info(f"{_LOG_TAG} starting recording: {' '.join(command)}")
        # A dedicated process group lets stop() deliver Ctrl+Break only to scrcpy
        # (not this process) on Windows so it can finalize the container cleanly.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if is_windows else 0
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
        except Exception as exc:
            raise RuntimeError(f"{_LOG_TAG} failed to launch scrcpy: {exc}") from exc

        self._output_path = output_path
        self._started_at = time.time()
        self._stopped_at = None
        # The auto-stop timeout is enforced in-process (rather than via scrcpy's
        # `--time-limit`) so stop() always runs the clean finalization path.
        if seconds is not None:
            self._auto_stop_timer = threading.Timer(seconds, self._auto_stop)
            self._auto_stop_timer.daemon = True
            self._auto_stop_timer.start()
        logger.info(
            f"{_LOG_TAG} started recording {self._device_udid} -> {output_path} "
            f"(pid={self._process.pid})"
        )
        return self.status()

    def _auto_stop(self) -> None:
        """Finalize the recording when the in-process auto-stop timeout elapses."""
        if self._is_running():
            logger.info(f"{_LOG_TAG} auto-stop timeout reached for {self._device_udid}")
            try:
                self.stop()
            except Exception as exc:  # noqa: BLE001 - background watchdog thread
                logger.warning(f"{_LOG_TAG} auto-stop failed: {exc}")

    def stop(self, *, upload: bool = False, preset: str | None = None) -> dict:
        """Stop the scrcpy recording and finalize the output file.

        Args:
            upload: Unused for scrcpy (kept for :class:`RecordBase` parity); the
                recording stays on the local host.
            preset: Unused for scrcpy (downscaling must be set at :meth:`start`
                time via ``extra_args``/``--max-size``); ignored with a log.

        Returns:
            dict: The recording status report including the finished file path.
        """
        if upload:
            logger.debug(f"{_LOG_TAG} `upload` is not supported for scrcpy; ignoring")
        if preset is not None:
            logger.debug(
                f"{_LOG_TAG} `preset` is not supported at stop time for scrcpy; "
                "set --max-size via extra_args at start; ignoring"
            )

        if self._auto_stop_timer is not None:
            self._auto_stop_timer.cancel()
            self._auto_stop_timer = None

        if not self._is_running():
            if self._process is not None:
                # scrcpy already exited on its own (typically via --time-limit);
                # the recording is finalized, so this is a completed no-op stop.
                self._stopped_at = self._stopped_at or time.time()
                logger.info(
                    f"{_LOG_TAG} recording for {self._device_udid} already finished "
                    f"(rc={self._process.returncode}); nothing to stop"
                )
            else:
                logger.warning(f"{_LOG_TAG} no active recording for {self._device_udid}")
            return self.status()

        process = self._process
        assert process is not None
        logger.info(f"{_LOG_TAG} stopping recording {self._device_udid} (pid={process.pid})")

        wait_for = config.stop_timeout()
        try:
            # Ask scrcpy to shut down cleanly so its muxer writes the MP4 footer
            # (the `moov` atom); a hard kill leaves an unplayable file. On Windows
            # Ctrl+Break reaches only scrcpy's own process group (see start()).
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
        except (OSError, ValueError) as exc:
            logger.warning(f"{_LOG_TAG} clean stop signal failed ({exc}); terminating")
            process.terminate()

        try:
            process.wait(timeout=wait_for)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"{_LOG_TAG} scrcpy did not finalize within {wait_for}s; terminating"
            )
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error(f"{_LOG_TAG} scrcpy unresponsive; killing (file may be corrupt)")
                process.kill()
                process.wait()

        self._stopped_at = time.time()
        logger.info(f"{_LOG_TAG} stopped recording {self._device_udid} -> {self._output_path}")
        return self.status()

    def status(self) -> dict:
        """Return the current recording status for the bound device.

        The reported ``status`` is one of:

        * ``recording`` -- scrcpy is still capturing.
        * ``completed`` -- a recording ran and has since finished (either via an
          explicit :meth:`stop` or by scrcpy's own ``--time-limit``).
        * ``idle`` -- no recording has been started on this instance.
        """
        running = self._is_running()
        started = self._process is not None
        if running:
            state = "recording"
        elif started:
            state = "completed"
        else:
            state = "idle"

        output_path = self._output_path
        size_bytes: int | None = None
        if output_path is not None and output_path.exists():
            size_bytes = output_path.stat().st_size

        if self._started_at is None:
            elapsed_seconds: float | None = None
        elif running:
            elapsed_seconds = round(time.time() - self._started_at, 3)
        elif self._stopped_at is not None:
            elapsed_seconds = round(self._stopped_at - self._started_at, 3)
        else:
            elapsed_seconds = None

        return {
            "status": state,
            "record_type": self._record_type,
            "device_udid": self._device_udid,
            "output_path": str(output_path) if output_path else None,
            "pid": self._process.pid if self._process is not None else None,
            "size_bytes": size_bytes,
            "elapsed_seconds": elapsed_seconds,
        }

    @property
    def out_path(self) -> Path | None:
        """Return the output path for the recording."""
        return Path(self._output_path) if self._output_path and Path(self._output_path).exists() else None