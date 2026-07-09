"""Windows ffmpeg-backed :class:`RecordBase` implementation.

Runs on the **Windows host** itself: :class:`WindowsRecord` shells out to the
``ffmpeg`` CLI to capture a video-only recording of the local desktop via the
``ddagrab`` filter (the Direct3D11 Desktop Duplication API, sourced through
``lavfi``) directly to a local MP4 file. Unlike the iRecord-backed macOS
recorder there is no control server, so ``server_ip`` / ``server_port`` are
unused; the recorder simply supervises a local ``ffmpeg`` subprocess (this
mirrors the Android scrcpy recorder).

``ddagrab`` returns Direct3D11 GPU frames, so the recorder auto-selects the
cheapest encoder available (see :func:`_select_encoder_profile`): a GPU
hardware encoder (NVENC / AMF / QSV) keeps the frames in VRAM for near-zero CPU
cost, while the CPU fallback copies frames back to system memory
(``hwdownload``) and encodes with ``libx264``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from idevice.record import config
from idevice.record.base.errors import RecordError, RecordServerError
from idevice.record.base.record import RecordBase

logger = logging.getLogger(__name__)

_LOG_TAG = "[WindowsRecord]"

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hms]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1, "": 1}

# `ffmpeg -encoders` prints one encoder per line as `<6 flag chars> <name> <desc>`
# (e.g. ` V....D hevc_nvenc  NVIDIA NVENC hevc encoder`); this matches the flag
# column so the encoder id can be extracted from the second token.
_ENCODER_LINE_RE = re.compile(r"^\s*[A-Z.]{6}\s+(\S+)")


@dataclass(frozen=True)
class _EncoderProfile:
    """A candidate ffmpeg encoder configuration for ``ddagrab`` capture.

    Attributes:
        key: Short human-facing name used for selection/logging (e.g. ``nvenc``).
        encoder: The ffmpeg encoder id probed against ``ffmpeg -encoders``.
        hardware: ``True`` for GPU encoders that consume ``ddagrab`` D3D11 frames
            directly (no ``hwdownload``); ``False`` for the CPU fallback.
        init_args: Args emitted *before* ``-f lavfi`` (e.g. QSV device init).
        input_filters: Filter chain appended to the ``ddagrab`` source string.
            Empty for GPU encoders (zero-copy); the CPU profile downloads and
            converts frames here.
        codec_args: The ``-c:v`` / quality args emitted after the input.
    """

    key: str
    encoder: str
    hardware: bool
    init_args: tuple[str, ...] = ()
    input_filters: str = ""
    codec_args: tuple[str, ...] = field(default_factory=tuple)


# Hardware encoders are tried in order of decreasing prevalence for automation
# hosts (NVIDIA -> AMD -> Intel). All emit HEVC to match the previous libx265
# output. NVENC and AMF ingest ddagrab's D3D11 frames directly; QSV needs an
# explicit device init plus a hwmap into a QSV frames context.
_HW_ENCODER_PROFILES: tuple[_EncoderProfile, ...] = (
    _EncoderProfile(
        key="nvenc",
        encoder="hevc_nvenc",
        hardware=True,
        codec_args=("-c:v", "hevc_nvenc", "-preset", "p5", "-cq", "24"),
    ),
    _EncoderProfile(
        key="amf",
        encoder="hevc_amf",
        hardware=True,
        codec_args=(
            "-c:v", "hevc_amf",
            "-quality", "balanced",
            "-rc", "cqp",
            "-qp_i", "24",
            "-qp_p", "24",
        ),
    ),
    _EncoderProfile(
        key="qsv",
        encoder="hevc_qsv",
        hardware=True,
        init_args=(
            "-init_hw_device", "qsv=hw,child_device_type=dxva2",
            "-filter_hw_device", "hw",
        ),
        input_filters=",hwmap=derive_device=qsv,format=qsv",
        codec_args=("-c:v", "hevc_qsv", "-global_quality", "24"),
    ),
)

# CPU fallback: copy frames out of VRAM (`hwdownload`), declare ddagrab's 8-bit
# BGRA layout, convert to yuv420p, then encode with libx264 (lighter than
# libx265 at an equivalent preset).
_CPU_ENCODER_PROFILE = _EncoderProfile(
    key="cpu",
    encoder="libx264",
    hardware=False,
    input_filters=",hwdownload,format=bgra,format=yuv420p",
    codec_args=(
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
    ),
)

# Every profile keyed by its short name, for reverse lookup when restoring a
# persisted selection (see :func:`_load_cached_profile`).
_ALL_PROFILES: dict[str, _EncoderProfile] = {
    profile.key: profile for profile in (*_HW_ENCODER_PROFILES, _CPU_ENCODER_PROFILE)
}

# Probing `ffmpeg -encoders` is stable per binary for a process' lifetime, so
# the result is memoized per ffmpeg path to avoid repeated subprocess launches.
_ENCODER_CACHE: dict[str, frozenset[str]] = {}

# Whether a given profile's full ddagrab->encoder chain actually runs (see
# :func:`_profile_usable`), memoized per ``(ffmpeg_binary, profile key)``.
_ENCODER_USABLE_CACHE: dict[tuple[str, str], bool] = {}

# Frame rate used only for the throwaway probe capture; it does not affect
# device init, so a fixed value keeps probe results cache-stable.
_PROBE_FRAMERATE = 30

# Subprocess timeouts (seconds) for the encoder probes. A one-frame ddagrab
# capture completes in ~1-2s on a healthy host, so these are kept tight to bound
# the worst-case cold-cache `_select_encoder_profile` cost (see module notes).
_ENCODERS_LIST_TIMEOUT = 10
_PROBE_TIMEOUT = 6


def _available_encoders(ffmpeg_binary: str) -> frozenset[str]:
    """Return the set of encoder ids reported by ``ffmpeg -encoders``.

    The result is cached per ``ffmpeg_binary`` path. On any failure an empty set
    is returned (and cached), which makes callers fall back to CPU encoding.

    Args:
        ffmpeg_binary: The ffmpeg CLI path/name to probe.

    Returns:
        A frozenset of available encoder ids (e.g. ``{"libx264", "hevc_nvenc"}``).
    """
    cached = _ENCODER_CACHE.get(ffmpeg_binary)
    if cached is not None:
        return cached

    encoders: set[str] = set()
    try:
        result = subprocess.run(
            [ffmpeg_binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=_ENCODERS_LIST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"{_LOG_TAG} could not probe ffmpeg encoders: {exc}")
        frozen = frozenset()
        _ENCODER_CACHE[ffmpeg_binary] = frozen
        return frozen

    for line in result.stdout.splitlines():
        match = _ENCODER_LINE_RE.match(line)
        if match:
            encoders.add(match.group(1))

    frozen = frozenset(encoders)
    _ENCODER_CACHE[ffmpeg_binary] = frozen
    return frozen


def _profile_usable(ffmpeg_binary: str, profile: _EncoderProfile) -> bool:
    """Return ``True`` if ``profile``'s full ddagrab->encoder chain actually runs.

    ``ffmpeg -encoders`` only reports what the *build* supports, so a hardware
    encoder (e.g. ``hevc_nvenc``) is listed even when the matching GPU is
    absent. Moreover an encoder can init from CPU frames yet still fail to
    derive its device context from ``ddagrab``'s D3D11 frames (observed with
    ``hevc_amf`` on hosts without a compatible Radeon device). This therefore
    probes the *exact* pipeline -- init args, the ddagrab source with the
    profile's input filters, and the profile's codec args -- capturing a single
    frame to a null muxer. A non-zero exit means the chain is unusable on this
    host. Results are cached per profile.

    Args:
        ffmpeg_binary: The ffmpeg CLI path/name.
        profile: The encoder profile whose capture->encode chain is validated.

    Returns:
        ``True`` if the one-frame probe succeeded, ``False`` otherwise.
    """
    key = (ffmpeg_binary, profile.key)
    cached = _ENCODER_USABLE_CACHE.get(key)
    if cached is not None:
        return cached

    command = [ffmpeg_binary, "-hide_banner", "-loglevel", "error"]
    command.extend(profile.init_args)
    command += [
        "-f", "lavfi",
        "-i", f"ddagrab=output_idx=0:framerate={_PROBE_FRAMERATE}{profile.input_filters}",
        "-frames:v", "1",
    ]
    command.extend(profile.codec_args)
    command += ["-f", "null", "-"]

    usable = False
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT
        )
        usable = result.returncode == 0
        if not usable:
            logger.debug(
                f"{_LOG_TAG} encoder {profile.encoder!r} failed ddagrab probe: "
                f"{result.stderr.strip()[:300]}"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"{_LOG_TAG} probing encoder {profile.encoder!r} failed: {exc}")

    _ENCODER_USABLE_CACHE[key] = usable
    return usable


def _ffmpeg_signature(ffmpeg_binary: str) -> str:
    """Return a stable identity for ``ffmpeg_binary`` used as a disk-cache key.

    Combines the resolved path with the binary's size and mtime so that an
    ffmpeg upgrade (which may add/remove encoder support) invalidates a
    previously persisted selection.

    Args:
        ffmpeg_binary: The ffmpeg CLI path/name.

    Returns:
        A signature string; falls back to the bare path when the binary cannot
        be stat'd.
    """
    resolved = shutil.which(ffmpeg_binary) or ffmpeg_binary
    try:
        info = Path(resolved).stat()
        return f"{resolved}|{info.st_size}|{int(info.st_mtime)}"
    except OSError:
        return resolved


def _load_cached_profile(signature: str) -> _EncoderProfile | None:
    """Return the persisted encoder profile for ``signature``, or ``None``.

    Reads :func:`idevice.record.config.ffmpeg_encoder_cache_file`; any missing
    file, malformed JSON, or unknown profile key yields ``None`` so the caller
    falls back to live probing.

    Args:
        signature: The ffmpeg identity from :func:`_ffmpeg_signature`.

    Returns:
        The cached :class:`_EncoderProfile`, or ``None`` when unavailable.
    """
    path = config.ffmpeg_encoder_cache_file()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    key = data.get(signature) if isinstance(data, dict) else None
    profile = _ALL_PROFILES.get(key) if isinstance(key, str) else None
    if profile is not None:
        logger.debug(f"{_LOG_TAG} restored persisted encoder profile {key!r}")
    return profile


def _store_cached_profile(signature: str, profile: _EncoderProfile) -> None:
    """Persist ``profile`` for ``signature`` to the on-disk encoder cache.

    Best-effort: existing entries are preserved and any I/O error is logged and
    swallowed (the selection still works in-process this run).

    Args:
        signature: The ffmpeg identity from :func:`_ffmpeg_signature`.
        profile: The selected profile to persist.
    """
    path = config.ffmpeg_encoder_cache_file()
    data: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = {k: v for k, v in loaded.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        data = {}
    data[signature] = profile.key
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError as exc:
        logger.debug(f"{_LOG_TAG} could not persist encoder cache to {path}: {exc}")


def _select_encoder_profile(ffmpeg_binary: str) -> _EncoderProfile:
    """Auto-select the cheapest usable encoder profile for a recording.

    A prior selection persisted for this exact ffmpeg binary (see
    :func:`_load_cached_profile`) is reused verbatim, skipping all probing so
    the common ``start()`` path launches immediately. Otherwise hardware
    encoders are tried in :data:`_HW_ENCODER_PROFILES` order and the first whose
    full ddagrab->encoder chain actually runs (see :func:`_profile_usable`) is
    chosen; the CPU ``libx264`` profile is used when no hardware encoder is
    usable on this host. The result is then persisted for future processes.

    Args:
        ffmpeg_binary: The ffmpeg CLI path/name used to probe available encoders.

    Returns:
        The chosen :class:`_EncoderProfile`.
    """
    signature = _ffmpeg_signature(ffmpeg_binary)
    cached = _load_cached_profile(signature)
    if cached is not None:
        return cached

    available = _available_encoders(ffmpeg_binary)
    selected: _EncoderProfile | None = None
    for profile in _HW_ENCODER_PROFILES:
        if profile.encoder in available and _profile_usable(ffmpeg_binary, profile):
            selected = profile
            break
    if selected is None:
        logger.info(
            f"{_LOG_TAG} no usable hardware encoder; using CPU encode "
            f"({_CPU_ENCODER_PROFILE.encoder})"
        )
        selected = _CPU_ENCODER_PROFILE

    _store_cached_profile(signature, selected)
    return selected


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
            input: Configured capture input (app/exe name or raw target);
                defaults to :func:`idevice.record.config.ffmpeg_input`. Ignored
                while ddagrab full-desktop capture is in effect (see
                :func:`_resolve_gdigrab_target`).

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
        self._output_path = output_path

        seconds = _parse_timeout_seconds(timeout)

        # `-y` overwrites any stale file. Auto-select the cheapest encoder: a GPU
        # encoder keeps ddagrab's D3D11 frames in VRAM (near-zero CPU), otherwise
        # the CPU profile downloads/converts frames and encodes with libx264.
        ddagrab_source = f"ddagrab=output_idx=0:framerate={self._framerate}"
        profile = _select_encoder_profile(self._ffmpeg_binary)
        logger.info(
            f"{_LOG_TAG} selected encoder profile {profile.key!r} "
            f"({profile.encoder}, hardware={profile.hardware})"
        )
        command = [self._ffmpeg_binary, "-y"]
        command.extend(profile.init_args)
        command += ["-f", "lavfi", "-i", f"{ddagrab_source}{profile.input_filters}"]
        command.extend(profile.codec_args)
        command.append(str(output_path))

        logger.info(f"{_LOG_TAG} starting recording: {' '.join(command)}")
        try:
            # stdin is a pipe so stop() can send `q` to ffmpeg for a clean
            # shutdown that writes the MP4 footer (the `moov` atom). stdout and
            # stderr are discarded.
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
