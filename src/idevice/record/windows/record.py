"""Windows ffmpeg-backed :class:`RecordBase` implementation.

Runs on the **Windows host** itself: :class:`WindowsRecord` shells out to the
``ffmpeg`` CLI to capture a video-only recording of the local desktop via the
``gdigrab`` input device directly to a local MP4 file. Unlike the iRecord-backed
macOS recorder there is no control server, so ``server_ip`` / ``server_port``
are unused; the recorder simply supervises a local ``ffmpeg`` subprocess (this
mirrors the Android scrcpy recorder).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from idevice.record import config
from idevice.record.base.errors import RecordError, RecordServerError
from idevice.record.base.record import RecordBase

logger = logging.getLogger(__name__)

_LOG_TAG = "[WindowsRecord]"

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


def _ps_single_quote(value: str) -> str:
    """Quote a value as a PowerShell single-quoted string literal."""
    return "'" + value.replace("'", "''") + "'"


def _find_window_title(app: str) -> str | None:
    """Return the main window title of a running process by exe/process name.

    ``gdigrab`` can only target a window by its *title* (``title=<name>``), but
    the automation framework identifies apps by exe/package name (e.g.
    ``Endfield.exe``). This resolves the currently-running process' main window
    title so a bare app name can be turned into a valid gdigrab target.

    Args:
        app: An exe or process name (``Endfield.exe`` / ``Endfield``).

    Returns:
        The process' non-empty main window title, or ``None`` when the process
        is not running, has no visible window, or the lookup fails.
    """
    from idevice.device.config import powershell_binary

    process_name = app[:-4] if app.lower().endswith(".exe") else app
    script = (
        f"$p = Get-Process -Name {_ps_single_quote(process_name)} "
        "-ErrorAction SilentlyContinue "
        "| Where-Object { $_.MainWindowTitle } | Select-Object -First 1; "
        "if ($p) { [Console]::Out.Write($p.MainWindowTitle) }"
    )
    try:
        result = subprocess.run(
            [
                powershell_binary(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"{_LOG_TAG} window-title lookup for {app!r} failed: {exc}")
        return None
    title = result.stdout.strip()
    return title or None


def _resolve_gdigrab_target(raw_input: str) -> str:
    """Normalize a configured input into a valid ``gdigrab`` target.

    ``gdigrab`` only accepts ``desktop``, ``title=<window title>`` or
    ``hwnd=<hwnd>``. This maps the configured input accordingly:

    * empty / ``desktop`` -> ``desktop`` (whole-screen capture).
    * an explicit ``title=`` / ``hwnd=`` / ``desktop`` value -> passed through.
    * any other value (treated as an app/exe name) -> resolved to that app's
      main window title (``title=<...>``); if no visible window is found it
      falls back to ``desktop`` so a recording is always produced (never a
      0-byte file).
    """
    value = (raw_input or "").strip()
    if not value or value.lower() == "desktop":
        return "desktop"
    if value.lower().startswith(("title=", "hwnd=")):
        return value

    title = _find_window_title(value)
    if title:
        logger.info(f"{_LOG_TAG} resolved input {value!r} -> window title {title!r}")
        return f"title={title}"
    logger.warning(
        f"{_LOG_TAG} no visible window found for {value!r}; capturing full desktop instead"
    )
    return "desktop"


class WindowsRecord(RecordBase):
    """Drive ffmpeg screen recording of the local Windows desktop."""

    def __init__(
        self,
        record_type: str = "windows",
        *,
        device_udid: str,
        server_ip: str = "",
        server_port: int = config.DEFAULT_RECORD_PORT,
        output_dir: Path | None = None,
        ffmpeg_binary: str | None = None,
        framerate: int | None = None,
        extra_args: list[str] | None = None,
        input: str | None = None,
    ) -> None:
        """Bind the recorder to the local desktop and locate the ffmpeg CLI.

        Args:
            record_type: Host/record type identifier (``windows``).
            device_udid: Identifier of the target host (typically the host name);
                used only to label the output file. Required and non-empty.
            server_ip: Unused for ffmpeg (kept for :class:`RecordBase` parity).
            server_port: Unused for ffmpeg (kept for :class:`RecordBase` parity).
            output_dir: Directory recordings are written to; defaults to
                :func:`idevice.record.config.record_output_dir`.
            ffmpeg_binary: ffmpeg CLI path; defaults to
                :func:`idevice.record.config.ffmpeg_binary`.
            framerate: Desktop capture frame rate; defaults to
                :func:`idevice.record.config.ffmpeg_framerate`.
            extra_args: Extra ffmpeg CLI args inserted before the output file;
                defaults to :func:`idevice.record.config.ffmpeg_extra_args`.

        Raises:
            ValueError: If ``device_udid`` is empty.
            RecordError: If the ffmpeg CLI cannot be found.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        # ffmpeg is local (no control server), so the base coordinate validation
        # is bypassed and the backing attributes are set directly.
        self._record_type = record_type
        self._server_ip = server_ip
        self._server_port = int(server_port)
        self._device_udid = device_udid
        self._output_dir = Path(output_dir) if output_dir else config.record_output_dir()
        self._ffmpeg_binary = ffmpeg_binary or config.ffmpeg_binary()
        self._framerate = int(framerate) if framerate is not None else config.ffmpeg_framerate()
        self._extra_args = list(extra_args) if extra_args is not None else config.ffmpeg_extra_args()
        self._input = input or config.ffmpeg_input()

        resolved = shutil.which(self._ffmpeg_binary)
        if resolved is None and not Path(self._ffmpeg_binary).is_file():
            logger.error(f"{_LOG_TAG} `{self._ffmpeg_binary}` CLI not found on PATH")
            raise RecordError(
                f"`{self._ffmpeg_binary}` CLI not found on PATH. "
                "Install ffmpeg: https://ffmpeg.org/download.html"
            )

        self._process: subprocess.Popen[bytes] | None = None
        self._output_path: Path | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        # The auto-stop timeout is enforced in-process (see start()) rather than
        # via ffmpeg's `-t` flag, so stop() always finalizes the recording.
        self._auto_stop_timer: threading.Timer | None = None

    @classmethod
    def from_env(cls) -> WindowsRecord:
        """Build a :class:`WindowsRecord` from the environment.

        Reads ``GAUTO_HOST_TYPE`` and ``GAUTO_DEVICE_UDID`` (the host name), plus
        the optional ``IDEVICE_FFMPEG_*`` / ``IDEVICE_RECORD_OUTPUT_DIR`` overrides.

        Returns:
            WindowsRecord: A recorder bound to the host described by the
            environment.

        Raises:
            ValueError: If ``GAUTO_DEVICE_UDID`` is empty.
            RecordError: If the ffmpeg CLI cannot be found.
        """
        return cls(
            record_type=config.record_type(),
            device_udid=config.device_udid(),
        )

    def _is_running(self) -> bool:
        """Return ``True`` while the supervised ffmpeg process is still alive."""
        return self._process is not None and self._process.poll() is None

    def health(self) -> bool:
        """Return ``True`` if the ffmpeg CLI is available on this host."""
        resolved = shutil.which(self._ffmpeg_binary)
        if resolved is None and not Path(self._ffmpeg_binary).is_file():
            logger.debug(f"{_LOG_TAG} `{self._ffmpeg_binary}` CLI not available")
            return False
        return True

    def start(self, *, timeout: float | str | None = None) -> dict:
        """Start an ffmpeg recording of the local desktop.

        Args:
            timeout: Optional auto-stop duration; accepts seconds or a duration
                string (``2h`` / ``30m`` / ``90s``). Enforced in-process by a
                watchdog that triggers :meth:`stop`. ``None`` records until an
                explicit :meth:`stop`.

        Returns:
            dict: The recording status report (see :meth:`status`).

        Raises:
            RecordServerError: If ffmpeg fails to launch.
            ValueError: If ``timeout`` cannot be parsed.
        """
        if self._is_running():
            logger.warning(f"{_LOG_TAG} recording already running for {self._device_udid}")
            return self.status()

        self._output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_udid = re.sub(r"[^A-Za-z0-9._-]", "_", self._device_udid)
        output_path = self._output_dir / f"{safe_udid}_{stamp}.mp4"

        seconds = _parse_timeout_seconds(timeout)

        # gdigrab needs `desktop` / `title=<...>` / `hwnd=<...>`; a bare app name
        # (e.g. `Endfield.exe`) is resolved to its window title, falling back to
        # whole-desktop capture so ffmpeg never fails to open its input.
        target = _resolve_gdigrab_target(self._input)

        # `-y` overwrites any stale file. A yuv420p H.264 stream keeps the output
        # broadly playable. `extra_args` are inserted before the output so callers
        # can override the defaults.
        command = [
            self._ffmpeg_binary,
            "-y",
            "-f", "gdigrab",
            "-framerate", str(self._framerate),
            "-i", target,
        ]
        command.extend(self._extra_args)
        if "-c:v" not in self._extra_args and "-vcodec" not in self._extra_args:
            command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        command.append(str(output_path))

        logger.info(f"{_LOG_TAG} starting recording: {' '.join(command)}")
        try:
            # stdin is a pipe so stop() can send `q` to ffmpeg for a clean
            # shutdown that writes the MP4 footer (the `moov` atom).
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RecordServerError(f"{_LOG_TAG} failed to launch ffmpeg: {exc}") from exc

        self._output_path = output_path
        self._started_at = time.time()
        self._stopped_at = None
        # The auto-stop timeout is enforced in-process (rather than via ffmpeg's
        # `-t` flag) so stop() always runs the clean finalization path.
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
        """Stop the ffmpeg recording and finalize the output file.

        Args:
            upload: Unused for ffmpeg (kept for :class:`RecordBase` parity); the
                recording stays on the local host.
            preset: Unused for ffmpeg (downscaling must be set at :meth:`start`
                time via ``extra_args``/``-vf scale``); ignored with a log.

        Returns:
            dict: The recording status report including the finished file path.
        """
        if upload:
            logger.debug(f"{_LOG_TAG} `upload` is not supported for ffmpeg; ignoring")
        if preset is not None:
            logger.debug(
                f"{_LOG_TAG} `preset` is not supported at stop time for ffmpeg; "
                "set -vf scale via extra_args at start; ignoring"
            )

        if self._auto_stop_timer is not None:
            self._auto_stop_timer.cancel()
            self._auto_stop_timer = None

        if not self._is_running():
            if self._process is not None:
                # ffmpeg already exited on its own; the recording is finalized,
                # so this is a completed no-op stop.
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
            # Ask ffmpeg to shut down cleanly by writing `q` to its stdin so its
            # muxer writes the MP4 footer (the `moov` atom); a hard kill leaves
            # an unplayable file.
            if process.stdin is not None:
                process.stdin.write(b"q")
                process.stdin.flush()
                process.stdin.close()
        except (OSError, ValueError) as exc:
            logger.warning(f"{_LOG_TAG} clean stop via stdin failed ({exc}); terminating")
            process.terminate()

        try:
            process.wait(timeout=wait_for)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"{_LOG_TAG} ffmpeg did not finalize within {wait_for}s; terminating"
            )
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error(f"{_LOG_TAG} ffmpeg unresponsive; killing (file may be corrupt)")
                process.kill()
                process.wait()

        self._stopped_at = time.time()
        logger.info(f"{_LOG_TAG} stopped recording {self._device_udid} -> {self._output_path}")
        return self.status()

    def status(self) -> dict:
        """Return the current recording status for the local desktop.

        The reported ``status`` is one of:

        * ``recording`` -- ffmpeg is still capturing.
        * ``completed`` -- a recording ran and has since finished (either via an
          explicit :meth:`stop` or because ffmpeg exited on its own).
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
