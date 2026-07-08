"""Windows ffmpeg-backed :class:`RecordBase` implementation.

Runs on the **Windows host** itself: :class:`WindowsRecord` shells out to the
``ffmpeg`` CLI to capture a video-only recording of the primary monitor via the
``ddagrab`` (DXGI Desktop Duplication) source filter directly to a local MP4
file. Unlike ``gdigrab``, ``ddagrab`` captures the *composited* desktop image,
so it can record GPU/hardware-accelerated content (games, DirectX/OpenGL apps)
that ``gdigrab``'s GDI ``BitBlt`` path renders as a black frame.

``ddagrab`` captures a whole monitor (not a specific window), so the recorder
first brings the target app's window to the foreground (best-effort, driven by
``GAUTO_PACKAGE_NAME``) before starting ffmpeg. Unlike the iRecord-backed macOS
recorder there is no control server, so ``server_ip`` / ``server_port`` are
unused; the recorder simply supervises a local ``ffmpeg`` subprocess (this
mirrors the Android scrcpy recorder).

Requirements/caveats:

* Needs FFmpeg 5.0+ (the ``ddagrab`` filter).
* Must run in an active, unlocked interactive session; a locked workstation, a
  disconnected RDP session, or a Session 0 service all yield a black capture
  regardless of the capture method.
* Exclusive-fullscreen games can be unstable with Desktop Duplication;
  borderless-windowed mode is the most reliable.
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

# ddagrab captures a whole monitor by index; 0 is the primary display.
_OUTPUT_IDX = 0
# Seconds to wait after foregrounding the target window so its frame is stable
# before ffmpeg starts capturing.
_FOREGROUND_SETTLE_SECONDS = 0.5


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


# PowerShell that finds a running process' main window by exe/process name and
# forces it to the foreground. A bare `SetForegroundWindow` from a background
# process is silently blocked by Windows' foreground lock, so this uses the
# well-known robust sequence: an ALT keystroke to release the lock,
# `AttachThreadInput` to share input state with the current foreground thread,
# `BringWindowToTop` + `SetForegroundWindow`, and a HWND_TOPMOST/NOTOPMOST
# z-order kick, then verifies via `GetForegroundWindow`. `__NAME__` is replaced
# with a single-quoted PowerShell string literal (avoids brace escaping).
_FOREGROUND_PS_TEMPLATE = r"""
$name = __NAME__
$p = Get-Process -Name $name -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Error 'no window'; exit 1 }
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class IDeviceWin {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();

    public static bool Force(IntPtr hWnd) {
        if (IsIconic(hWnd)) { ShowWindow(hWnd, 9); } // SW_RESTORE
        uint pid;
        uint fgThread = GetWindowThreadProcessId(GetForegroundWindow(), out pid);
        uint appThread = GetCurrentThreadId();
        // A stray ALT keystroke releases the SetForegroundWindow foreground lock.
        keybd_event(0x12, 0, 0, UIntPtr.Zero);
        keybd_event(0x12, 0, 2, UIntPtr.Zero);
        bool attached = fgThread != appThread && AttachThreadInput(fgThread, appThread, true);
        BringWindowToTop(hWnd);
        ShowWindow(hWnd, 5); // SW_SHOW
        SetForegroundWindow(hWnd);
        if (attached) { AttachThreadInput(fgThread, appThread, false); }
        uint flags = 0x0001 | 0x0002; // SWP_NOMOVE | SWP_NOSIZE
        SetWindowPos(hWnd, new IntPtr(-1), 0, 0, 0, 0, flags); // HWND_TOPMOST
        SetWindowPos(hWnd, new IntPtr(-2), 0, 0, 0, 0, flags); // HWND_NOTOPMOST
        return GetForegroundWindow() == hWnd;
    }
}
'@
$h = $p.MainWindowHandle
$ok = [IDeviceWin]::Force($h)
Start-Sleep -Milliseconds 200
if (-not $ok -and ([IDeviceWin]::GetForegroundWindow() -ne $h)) {
    Write-Error 'window did not reach the foreground'; exit 2
}
"""


def _bring_window_to_foreground(app: str) -> bool:
    """Best-effort: force a running app's main window to the foreground.

    ``ddagrab`` captures a whole monitor rather than a specific window, so the
    target app must be visible/foreground on the primary display before capture
    starts. The automation framework identifies apps by exe/process name (e.g.
    ``MyApp.exe``), so this resolves the running process and forces its main
    window to the foreground. A plain ``SetForegroundWindow`` from a background
    process is silently blocked by Windows' foreground lock, so the robust
    sequence (ALT keystroke + ``AttachThreadInput`` + ``BringWindowToTop`` +
    ``SetForegroundWindow`` + a topmost z-order kick) is used and the result is
    verified with ``GetForegroundWindow``. A minimized window is restored first
    via ``ShowWindow(SW_RESTORE)`` so it is visible again without otherwise
    resizing it.

    Args:
        app: An exe or process name (``MyApp.exe`` / ``MyApp``).

    Returns:
        ``True`` if the window reached the foreground, ``False`` if the
        process/window could not be found or activation could not be confirmed.
        Failures are logged and never raised, so a recording still starts
        (capturing whatever is on the primary display).
    """
    from idevice.device.config import powershell_binary

    process_name = app[:-4] if app.lower().endswith(".exe") else app
    script = _FOREGROUND_PS_TEMPLATE.replace("__NAME__", _ps_single_quote(process_name))
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
        logger.warning(f"{_LOG_TAG} foregrounding {app!r} failed: {exc}")
        return False
    if result.returncode != 0:
        detail = result.stderr.strip() or "process/window not found"
        logger.warning(
            f"{_LOG_TAG} could not bring {app!r} to the foreground: {detail}"
        )
        return False
    logger.info(f"{_LOG_TAG} brought {app!r} to the foreground")
    return True


class WindowsRecord(RecordBase):
    """Drive ffmpeg ``ddagrab`` recording of the local Windows primary monitor."""

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
            framerate: Monitor capture frame rate; defaults to
                :func:`idevice.record.config.ffmpeg_framerate`.
            extra_args: Extra ffmpeg CLI args inserted before the output file;
                defaults to :func:`idevice.record.config.ffmpeg_extra_args`.
            input: Foreground-activation target (app/exe name); defaults to
                :func:`idevice.record.config.ffmpeg_input` (``GAUTO_PACKAGE_NAME``).

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
        """Start an ffmpeg ``ddagrab`` recording of the primary monitor.

        Brings the configured target app (``self._input``, from
        ``GAUTO_PACKAGE_NAME``) to the foreground first (best-effort) so it is
        visible on the primary display, then captures the composited monitor via
        ``ddagrab``.

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
        self._output_path = output_path

        seconds = _parse_timeout_seconds(timeout)

        # ddagrab captures a whole monitor, so the app under test must be visible
        # on the primary display first. A bare app name (e.g. `MyApp.exe`, from
        # `GAUTO_PACKAGE_NAME`) is brought to the foreground best-effort; an empty
        # / `desktop` input means "just capture the desktop as-is".
        target = (self._input or "").strip()
        if target and target.lower() != "desktop":
            if _bring_window_to_foreground(target):
                # Give the window a moment to finish repainting after activation.
                time.sleep(_FOREGROUND_SETTLE_SECONDS)

        # `ddagrab` (DXGI Desktop Duplication) captures the composited primary
        # monitor, including GPU/hardware-accelerated content that `gdigrab`
        # cannot. Its frames live on the GPU, so `hwdownload,format=bgra` copies
        # them back to system memory before the CPU `libx264` encoder. `-y`
        # overwrites any stale file; a yuv420p H.264 stream keeps the output
        # broadly playable. `extra_args` are inserted before the output so callers
        # can override the default filter/encoder (e.g. `-c:v h264_nvenc`).
        ddagrab_src = f"ddagrab=output_idx={_OUTPUT_IDX}:framerate={self._framerate}"
        command = [
            self._ffmpeg_binary,
            "-y",
            "-f", "lavfi",
            "-i", ddagrab_src,
        ]
        command.extend(self._extra_args)
        if not any(a in ("-vf", "-filter:v", "-filter_complex") for a in self._extra_args):
            command += ["-vf", "hwdownload,format=bgra"]
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

    @property
    def out_path(self) -> Path | None:
        """Return the output path for the recording."""
        return Path(self._output_path) if self._output_path and Path(self._output_path).exists() else None
