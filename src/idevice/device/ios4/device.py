"""iOS game installation and launch via the Rust ``ios4`` CLI."""

from __future__ import annotations

import logging
import posixpath
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import NoReturn

import wda

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import AppNotInstalledError
from idevice.device.base.runner import CommandResult, SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.common.ios4cli import IOS4CLI
from idevice.device.config import device_id as env_device_id
from idevice.device.config import device_ip as env_device_ip
from idevice.device.config import ideviceinstaller_binary, ios4_binary
from idevice.device.config import package_name as env_package_name

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice4]"
_INSTALL_SUCCESS_MARKERS = (
    "install success",
    "install: complete",
    "install - complete",
)
_PID_PATTERN = re.compile(r"(?m)^PID:\s*(\d+)\s*$")
_DOCUMENTS_ROOT = "/Documents"
_DOCUMENTS_DIR_IFMT = "S_IFDIR"
_DOCUMENTS_FILE_IFMT = "S_IFREG"
_DOCUMENTS_IFMT_PATTERN = re.compile(r'st_ifmt:\s*"(\w+)"')
_DOCUMENTS_LIST_ENTRY_PATTERN = re.compile(r'^\s*"((?:[^"\\]|\\.)*)",?\s*$')
_WDA_PROCESS_MARKERS = ("webdriveragent", "xctrunner")
_WDA_PORT = 8100
_WDA_READY_TIMEOUT = 60.0
_POPUP_BUTTON_LABELS = (
    "无线局域网与蜂窝网络",
    "继续",
    "使用App时允许",
    "Allow",
    "Allow Once",
    "Allow While Using App",
    "Allow Access",
    "Allow Access to Local Network",
    "Allow Access to Network",
    "允许访问",
    "允许访问本地网络",
    "允许",
    "同意",
    "确定",
    "重试",
    "不允许",
    "OK",
    "Cancel",
    "取消",
    "稍后",
)


class IOSDevice4Error(RuntimeError):
    """Raised when an ``ios4`` device operation fails."""


class IOSDevice4(DeviceBase):
    """Install and launch iOS games through ``ios4``.

    IPA installation prefers the standalone libimobiledevice
    ``ideviceinstaller`` CLI and falls back to ``ios4 ideviceinstaller install``
    when that binary is not on the host. Every other operation uses these
    ``ios4`` subcommands:

    * ``application_listing`` for exact bundle-id checks.
    * ``process_control`` for direct native launches.
    * WebDriverAgent for launches, with ``process_control`` as the fallback
      that also reports the launch PID.
    * ``screenshot`` for screen capture.
    * ``memgraph`` for Xcode-compatible process memory snapshots.
    * WebDriverAgent, with ``pkill --bundle`` as the stop fallback.
    * ``app_service uninstall`` for lifecycle cleanup.
    * ``afc --documents`` for the app Documents sandbox (``documents_*``).

    Generic file transfer (``push`` / ``pull`` / ``ls``) outside the Documents
    sandbox is intentionally not implemented because the ios4 backend
    currently focuses on the game installation and process-launch workflow.
    """

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        package_name: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(
            device_id, device_ip, platform="ios4", package_name=package_name
        )
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        self._ios4cli = IOS4CLI(
            device_id,
            binary=ios4_binary(),
            runner=SubprocessRunner(),
        )
        self._last_launch_pid: int | None = None
        self._last_launch_app_id = ""
        if self._resolve_binary(self._binary) is None:
            logger.error(f"{_LOG_TAG} `{self._binary}` CLI not found")
            raise IOSDevice4Error(
                f"`{self._binary}` CLI not found. Build or install the "
                "ios4 binary, or set IDEVICE_IOS4_BINARY."
            )

    @property
    def last_launch_pid(self) -> int | None:
        """Return the PID from the most recent successful launch."""
        return self._last_launch_pid

    @property
    def _binary(self) -> str:
        """Compatibility alias for the composed CLI binary."""
        return self._ios4cli.binary

    @_binary.setter
    def _binary(self, value: str) -> None:
        self._ios4cli.binary = value

    @property
    def _runner(self) -> SubprocessRunner:
        """Compatibility alias for the composed CLI runner."""
        return self._ios4cli.runner

    @_runner.setter
    def _runner(self, value: SubprocessRunner) -> None:
        self._ios4cli.runner = value

    @classmethod
    def from_env(cls) -> IOSDevice4:
        """Build an :class:`IOSDevice4` from the ``GAUTO_*`` environment."""
        return cls(
            env_device_id(),
            device_ip=env_device_ip(),
            package_name=env_package_name(),
        )

    @classmethod
    def default_udid(cls) -> str:
        """Return the first connected device UDID reported by ios4."""
        return IOS4CLI.default_udid(
            binary=ios4_binary(), runner=SubprocessRunner()
        )

    def _command(self, *arguments: str) -> list[str]:
        """Build an ios4 command for the bound device."""
        return self._ios4cli.command(*arguments)

    @staticmethod
    def _resolve_binary(binary: str) -> str | None:
        """Return the usable path for ``binary``, or ``None`` when missing."""
        resolved = shutil.which(binary)
        if resolved is not None:
            return resolved
        return binary if Path(binary).is_file() else None

    def _install_command(self, package_path: Path) -> list[str]:
        """Build the install command, preferring standalone ``ideviceinstaller``."""
        standalone = self._resolve_binary(ideviceinstaller_binary())
        if standalone is not None:
            return [
                standalone,
                "--udid",
                self.device_id,
                "install",
                str(package_path),
            ]
        logger.debug(
            f"{_LOG_TAG} standalone ideviceinstaller not found; using the "
            "ios4 ideviceinstaller subcommand"
        )
        return self._command("ideviceinstaller", "install", str(package_path))

    @staticmethod
    def _bundle_id_in_application_listing(output: str, app_id: str) -> bool:
        """Return whether an application-listing row starts with ``app_id``."""
        for line in output.splitlines():
            columns = line.strip().split()
            if columns and columns[0] == app_id:
                return True
        return False

    @staticmethod
    def _encode_environment(environment: dict[str, str]) -> str:
        """Encode environment variables for process_control ``--env``."""
        entries: list[str] = []
        for key, value in environment.items():
            if not key or "=" in key or "," in key:
                raise ValueError(
                    "environment names must be non-empty and contain neither '=' nor ','"
                )
            if not isinstance(value, str):
                raise TypeError(f"environment value for {key!r} must be a string")
            if "," in value:
                raise ValueError(
                    f"environment value for {key!r} cannot contain ','"
                )
            entries.append(f"{key}={value}")
        return ",".join(entries)

    @staticmethod
    def _encode_launch_arguments(arguments: list[str]) -> str:
        """Encode ordered arguments for process_control ``--args``."""
        encoded: list[str] = []
        for index, argument in enumerate(arguments):
            if not isinstance(argument, str):
                raise TypeError(f"launch argument at index {index} must be a string")
            if not argument:
                raise ValueError(f"launch argument at index {index} cannot be empty")
            encoded.append(argument.replace("\\", "\\\\").replace(",", "\\,"))
        return ",".join(encoded)

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        """Install an IPA or app directory with ``ideviceinstaller install``.

        The standalone libimobiledevice ``ideviceinstaller`` CLI is used when it
        is available on the host (or pointed at by
        ``IDEVICE_IDEVICEINSTALLER_BINARY``); otherwise the installation falls
        back to the ``ios4 ideviceinstaller`` subcommand.

        Args:
            package_path: IPA file or app directory to install.
            app_id: Bundle identifier cached after a successful install.

        Returns:
            bool: Whether the installation reported success.

        Raises:
            FileNotFoundError: If ``package_path`` does not exist.
        """
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")

        command = self._install_command(package_path)
        logger.info(
            f"{_LOG_TAG} Installing package on {self.device_id} via "
            f"{command[0]}: {package_path}"
        )
        result = self._runner.run(command, check=False, timeout=3600)
        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        succeeded = result.returncode == 0 and any(
            marker in combined_output for marker in _INSTALL_SUCCESS_MARKERS
        )
        if not succeeded:
            logger.error(
                f"{_LOG_TAG} Installation failed on {self.device_id}: "
                f"returncode={result.returncode}, stdout={result.stdout!r}, "
                f"stderr={result.stderr!r}"
            )
            return False

        if app_id:
            self._app_cache.add(app_id, version=package_path.stem, path=None)
        return True

    def uninstall(self, app_id: str) -> None:
        """Uninstall an application through CoreDevice app service."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        logger.info(f"{_LOG_TAG} Uninstalling {app_id} on {self.device_id}")
        self._ios4cli.run("app_service", "uninstall", app_id)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        """Check an exact bundle id using ``application_listing``."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        result = self._ios4cli.run("application_listing", check=False)
        if result.returncode != 0:
            logger.warning(
                f"{_LOG_TAG} application listing failed on {self.device_id}: "
                f"returncode={result.returncode}, stderr={result.stderr!r}"
            )
            return False
        return self._bundle_id_in_application_listing(result.stdout, app_id)

    def launch_app(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        """Launch an installed game with optional environment and ``argv``.

        The launch is attempted through WebDriverAgent first and falls back to
        ``ios4 process_control``. Arguments and environment values are encoded
        for ``process_control`` before either transport runs, so both paths
        reject the same inputs.

        WDA does not report a PID, so :attr:`last_launch_pid` is resolved
        best-effort from the WDA app list and stays ``None`` when that list
        does not contain the launched bundle. The ``process_control`` fallback
        always reports a PID.

        Args:
            app_id: Bundle identifier to launch. When omitted or empty, uses
                the bound :attr:`package_name`.
            args: Ordered command-line arguments passed to the game process.
            environment: Environment variables injected before process start.

        Raises:
            ValueError: If both ``app_id`` and :attr:`package_name` are empty.
            AppNotInstalledError: If the resolved bundle id is not installed.
            IOSDevice4Error: If WDA is unavailable and process control does not
                return a PID.
        """
        target = self._resolve_app_id(app_id)
        if not self.is_installed(target):
            raise AppNotInstalledError(f"App not installed: {target}")

        encoded_environment = (
            self._encode_environment(environment) if environment else ""
        )
        encoded_arguments = (
            self._encode_launch_arguments(args) if args else ""
        )

        logger.info(f"{_LOG_TAG} Launching {target} on {self.device_id}")
        if self._launch_app_via_wda(target, args or [], environment or {}):
            return

        logger.info(
            f"{_LOG_TAG} Falling back to ios4 process_control for {target} "
            f"on {self.device_id}"
        )
        command = self._command("process_control")
        if encoded_environment:
            command.extend(["--env", encoded_environment])
        if encoded_arguments:
            command.extend(["--args", encoded_arguments])
        command.append(target)

        result = self._ios4cli.runner.run(command)
        match = _PID_PATTERN.search(f"{result.stdout}\n{result.stderr}")
        if match is None:
            raise IOSDevice4Error(
                f"{_LOG_TAG} process_control did not return a PID for {target}: "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        self._last_launch_pid = int(match.group(1))
        self._last_launch_app_id = target

    def launch_wda(self, bundle_id: str) -> None:
        """Start WebDriverAgent on the device, unless it is already ready.

        A ``status`` probe decides readiness; when WDA already answers it,
        the existing instance is left alone (killing it would also kill
        whatever xctest runner started it, which this call may not own).
        Otherwise the xctest runner is (re)started and this call blocks
        until WDA answers ``status`` or ``timeout`` elapses.

        Args:
            bundle_id: WDA xctest runner bundle id, e.g.
                ``com.example.WebDriverAgentRunner.xctrunner``.

        Raises:
            ValueError: If ``bundle_id`` is empty.
            IOSDevice4Error: If WDA does not become ready in time.
        """
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")

        if wda.Client(self.wda_url()).is_ready():
            logger.info(f"{_LOG_TAG} WDA already ready on {self.device_id}")
        else:
            logger.info(
                f"{_LOG_TAG} WDA not ready on {self.device_id}; starting {bundle_id}"
            )
            self.launch(bundle_id)

            if not wda.Client(self.wda_url()).wait_ready(timeout=_WDA_READY_TIMEOUT):
                raise IOSDevice4Error(
                    f"{_LOG_TAG} WDA did not become ready on {self.device_id} "
                    f"within {_WDA_READY_TIMEOUT:g}s after starting {bundle_id}"
                )
            logger.info(f"{_LOG_TAG} WDA ready on {self.device_id}")

        self.dismiss_message_popup(duration=60*10)

    def wda_url(self) -> str | None:
        """Return the bound WDA base URL, or ``None`` for the client default."""
        return f"http://{self.device_ip}:{_WDA_PORT}" if self.device_ip else None

    def _wda_session(self) -> wda.Client:
        """Return a client bound to WebDriverAgent's *existing* session.

        Operations on the app under test must never open or close a session
        of their own. ``POST /session`` displaces the app the previous
        session launched and ``DELETE /session/{id}`` terminates it, so a
        helper wrapping its work in ``with client.session() as s:`` kills the
        app :meth:`launch_app` just started -- which is what the popup watch
        did to every game it was supposed to be babysitting.

        A bare client resolves ``session_id`` lazily from ``GET /status``,
        attaching to whichever session is already live and creating one only
        when the device has none.
        """
        return wda.Client(self.wda_url())

    def launch(self, app_id: str | None = None) -> None:
        """Launch an app directly through the ios4 ``process_control`` command.

        Unlike :meth:`launch_app`, this lower-level operation does not use
        WebDriverAgent and accepts no launch arguments.

        It also does not read back the launch PID, so :attr:`last_launch_pid`
        keeps whatever value an earlier :meth:`launch_app` recorded. Pass an
        explicit ``pid`` to :meth:`capture_memgraph` after this call; the
        retained PID may belong to a different app.

        Args:
            app_id: Bundle identifier to launch. When omitted or empty, uses
                the bound :attr:`package_name`.

        Raises:
            ValueError: If both ``app_id`` and :attr:`package_name` are empty.
            CommandExecutionError: If the ios4 command fails.
        """
        target = self._resolve_app_id(app_id)
        logger.info(
            f"{_LOG_TAG} Launching {target} through process_control on "
            f"{self.device_id}"
        )
        self._ios4cli.run("process_control", target)

    def capture_memgraph(
        self,
        output: Path | str,
        *,
        pid: int | None = None,
    ) -> Path:
        """Capture an Xcode-compatible memory graph for a process.

        Args:
            output: Destination ``.memgraph`` file. An existing destination is
                replaced only after a complete non-empty snapshot is received.
            pid: Process id to capture. Defaults to :attr:`last_launch_pid`.

        Returns:
            Path: The resolved destination path.

        Raises:
            IOSDevice4Error: If there is no PID or the snapshot is empty.
            ValueError: If an explicit PID is not a positive integer.
        """
        target_pid = self._last_launch_pid if pid is None else pid
        if target_pid is None:
            raise IOSDevice4Error(
                f"{_LOG_TAG} No PID available; launch the game or pass pid explicitly"
            )
        if isinstance(target_pid, bool) or not isinstance(target_pid, int):
            raise ValueError("pid must be a positive integer")
        if target_pid <= 0:
            raise ValueError("pid must be a positive integer")

        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}-",
            suffix=".memgraph",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        logger.info(
            f"{_LOG_TAG} Capturing PID {target_pid} on {self.device_id} "
            f"to {output_path}"
        )
        try:
            result = self._ios4cli.run(
                "memgraph",
                str(target_pid),
                str(temporary_path),
                check=False,
                timeout=600,
            )
            if (
                result.returncode != 0
                or not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise IOSDevice4Error(
                    f"{_LOG_TAG} Memory graph capture failed for PID {target_pid}: "
                    f"returncode={result.returncode}, stdout={result.stdout!r}, "
                    f"stderr={result.stderr!r}"
                )
            temporary_path.replace(output_path)
            return output_path
        finally:
            temporary_path.unlink(missing_ok=True)

    def stop_app(self, app_id: str | None = None) -> None:
        """Stop the app through WDA, falling back to ``ios4 pkill``.

        A stopped app is not an error. WDA is addressed through
        :attr:`device_ip` on its standard port when an IP is bound; otherwise
        the ``wda`` client's ``DEVICE_URL``/localhost default is used.

        Raises:
            IOSDevice4Error: If WDA is unavailable and ``ios4 pkill`` fails,
                which includes the app not being installed.
        """
        target = self._resolve_app_id(app_id)
        logger.info(f"{_LOG_TAG} Stopping app on iOS device {self.device_id}: {target}")
        if self._stop_app_via_wda(target):
            self._clear_last_launch(target)
            return

        logger.info(
            f"{_LOG_TAG} Falling back to ios4 pkill for {target} on {self.device_id}"
        )
        result = self._ios4cli.run("pkill", "--bundle", target, check=False)
        if result.returncode != 0:
            raise IOSDevice4Error(
                f"{_LOG_TAG} Failed to stop {target} on {self.device_id}: "
                f"returncode={result.returncode}, stdout={result.stdout!r}, "
                f"stderr={result.stderr!r}"
            )
        self._clear_last_launch(target)

    def _launch_app_via_wda(
        self, app_id: str, args: list[str], environment: dict[str, str]
    ) -> bool:
        """Return whether WDA launched ``app_id``.

        The WDA session is deliberately left open: deleting it terminates the
        application under test on some WebDriverAgent builds.
        """
        try:
            session = wda.Client(self.wda_url()).session(
                app_id,
                arguments=args or None,
                environment=environment or None,
            )
        except Exception as exc:
            logger.warning(
                f"{_LOG_TAG} WDA failed to launch {app_id} on "
                f"{self.device_id}: {exc}"
            )
            return False

        self._last_launch_pid = self._wda_pid(session, app_id)
        self._last_launch_app_id = app_id
        if self._last_launch_pid is None:
            logger.warning(
                f"{_LOG_TAG} WDA launched {app_id} on {self.device_id} without "
                "a PID; pass pid explicitly to capture_memgraph"
            )
        return True

    def _wda_pid(self, session: wda.Client, app_id: str) -> int | None:
        """Return the PID WDA reports for ``app_id``, when it reports one."""
        try:
            entries = session.app_list()
        except Exception as exc:
            logger.debug(
                f"{_LOG_TAG} WDA app list unavailable on {self.device_id}: {exc}"
            )
            return None
        for entry in entries or []:
            if entry.get("bundleId") != app_id:
                continue
            pid = entry.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                return pid
        return None

    def _stop_app_via_wda(self, app_id: str) -> bool:
        """Return whether WDA accepted an app-termination request."""
        try:
            self._wda_session().app_terminate(app_id)
        except Exception as exc:
            logger.warning(
                f"{_LOG_TAG} WDA failed to stop {app_id} on {self.device_id}: {exc}"
            )
            return False
        return True

    def _clear_last_launch(self, app_id: str) -> None:
        """Clear launch tracking when it belongs to ``app_id``."""
        if self._last_launch_app_id == app_id:
            self._last_launch_pid = None
            self._last_launch_app_id = ""

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        """Return cached package information when the app is still installed."""
        if not self.is_installed(app_id):
            return None
        return self._app_cache.get(app_id)

    def host_is_running(self) -> bool:
        """Return whether a WebDriverAgent-style process is running."""
        result = self._ios4cli.run("device_info", "processes", check=False)
        if result.returncode != 0:
            return False
        output = result.stdout.lower()
        return any(marker in output for marker in _WDA_PROCESS_MARKERS)

    def screenshot(self, local: Path | str) -> bool:
        """Capture a screenshot with the ios4 screenshot service."""
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._ios4cli.run("screenshot", str(local_path), check=False)
        return result.returncode == 0 and local_path.exists()

    def tap(self, x: float, y: float, *, app_id: str | None = None) -> None:
        """Tap a normalized screen point through WebDriverAgent.

        Args:
            x: Horizontal position in ``[0, 1]``.
            y: Vertical position in ``[0, 1]``.
            app_id: Optional foreground bundle id used for log context. WDA
                taps are screen-relative and do not require the bundle id.

        Raises:
            ValueError: If ``x`` or ``y`` is not a number in ``[0, 1]``.
            IOSDevice4Error: If WDA does not accept the tap.
        """
        self._validate_normalized_coordinate(x, "x")
        self._validate_normalized_coordinate(y, "y")
        app_context = f" for {app_id}" if app_id else ""
        logger.info(
            f"{_LOG_TAG} Tapping ({x}, {y}) on {self.device_id}{app_context}"
        )
        try:
            self._wda_session().click(float(x), float(y))
        except Exception as exc:
            raise IOSDevice4Error(
                f"{_LOG_TAG} WDA failed to tap ({x}, {y}) on "
                f"{self.device_id}{app_context}: {exc}"
            ) from exc

    def dismiss_message_popup(
        self,
        *,
        button_labels: tuple[str, ...] | list[str] | None = None,
        timeout: float = 1.0,
        duration: float | None = None,
        interval: float = 1.0,
    ) -> bool:
        """Dismiss message popups via WDA alert controls.

        By default this checks once and returns. Pass ``duration`` to keep
        watching for that many seconds, dismissing every popup that appears --
        useful across a launch or login flow that raises several permission
        prompts. The watch runs on a background daemon thread with its own
        WDA session, so this call returns immediately instead of blocking the
        caller for the whole ``duration``.

        Args:
            button_labels: Labels tried in order. Defaults to a built-in set
                that covers common iOS and Chinese popups.
            timeout: Per-element check timeout for alert/button presence.
            duration: Seconds to keep watching. ``None`` checks exactly once.
            interval: Seconds between scans while watching. Ignored when
                ``duration`` is ``None``.

        Returns:
            bool: When ``duration`` is ``None``, whether a popup was
            dismissed. When ``duration`` is set, ``True`` once the background
            watch has started.

        Raises:
            ValueError: If ``timeout``, ``interval``, or ``duration`` is not
                a positive number.
            IOSDevice4Error: If ``duration`` is ``None`` and the WDA session
                cannot be opened.
        """
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")
        if interval <= 0:
            raise ValueError("interval must be a positive number")
        if duration is not None and duration <= 0:
            raise ValueError("duration must be a positive number")

        labels = self._normalize_message_popup_labels(button_labels)
        logger.info(f'{labels}')
        if duration is not None:
            threading.Thread(
                target=self._watch_message_popups,
                args=(labels, timeout, duration, interval),
                daemon=True,
            ).start()
            return True

        try:
            return self._dismiss_message_popup_with_session(
                self._wda_session(), labels, timeout
            )
        except Exception as exc:
            raise IOSDevice4Error(
                f"{_LOG_TAG} WDA failed to handle message popup on "
                f"{self.device_id}: {exc}"
            ) from exc

    def _watch_message_popups(
        self,
        labels: tuple[str, ...],
        timeout: float,
        duration: float,
        interval: float,
    ) -> None:
        """Dismiss popups for ``duration`` seconds on a background thread.

        Runs off the caller's thread. Each scan attaches to WDA's live
        session via :meth:`_wda_session` instead of opening its own: a scan
        that created and deleted a session would terminate the app under
        test, so the watch used to kill the very app whose popups it was
        dismissing. Any failure -- a popup appearing and vanishing between
        the alert check and the click, or a transient WDA error -- is logged
        and the watch continues rather than cutting the duration short.
        """
        logger.info(
            f"{_LOG_TAG} Watching for message popups on {self.device_id} "
            f"for {duration:g}s"
        )
        deadline = time.monotonic() + duration
        dismissed = 0
        while True:
            try:
                if self._dismiss_message_popup_with_session(
                    self._wda_session(), labels, timeout
                ):
                    dismissed += 1
            except Exception as exc:
                logger.debug(
                    f"{_LOG_TAG} popup watch ignored transient WDA error "
                    f"on {self.device_id}: {exc}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))

        logger.info(
            f"{_LOG_TAG} Dismissed {dismissed} message popup(s) on "
            f"{self.device_id} over {duration:g}s"
        )

    @staticmethod
    def _dismiss_message_popup_with_session(
        session: wda.Client, labels: tuple[str, ...], timeout: float
    ) -> bool:
        """Click the first known button on an active alert, if any.

        Returns whether a button was clicked.
        """
        if not session.alert.exists:
            return False
        for label in labels:
            logger.info(f'{label}')
            button = session(text=label, timeout=timeout)
            if button.exists:
                button.click()
                return True
        return False

    @staticmethod
    def _normalize_message_popup_labels(
        button_labels: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        """Return validated popup button labels for handler/manual dismiss."""
        labels = button_labels if button_labels is not None else _POPUP_BUTTON_LABELS
        normalized = []
        seen = set[str]()
        for label in labels:
            if not label or label in seen:
                continue
            normalized.append(label)
            seen.add(label)
        normalized = tuple(normalized)
        if not normalized:
            raise ValueError("at least one button label is required")
        return normalized

    @staticmethod
    def _validate_normalized_coordinate(value: float, name: str) -> None:
        """Validate one normalized tap coordinate."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a normalized coordinate in [0, 1]")
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be a normalized coordinate in [0, 1]")

    def _unsupported(self, operation: str) -> NoReturn:
        raise NotImplementedError(
            f"{_LOG_TAG} {operation} is not supported by the ios4 lifecycle backend"
        )

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        del local, remote, app_id, documents_only
        self._unsupported("push")

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        del remote, local, app_id, documents_only
        self._unsupported("pull")

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        del remote, app_id, recursive
        self._unsupported("ls")

    @staticmethod
    def _require_app_and_remote(app_id: str, remote: str) -> None:
        """Validate the arguments shared by the Documents helpers."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")

    @staticmethod
    def _documents_path(remote: str) -> str:
        """Resolve ``remote`` to an absolute path under the vended Documents dir.

        ``ios4 afc --documents`` can only reach files under ``/Documents``, so
        ``remote`` is always interpreted relative to that root: leading
        separators are stripped instead of escaping the sandbox, and ``..``
        segments are rejected.
        """
        rel = remote.strip().replace("\\", "/").lstrip("/")
        parts = [part for part in rel.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError(f"remote path must not contain '..': {remote}")
        return posixpath.join(_DOCUMENTS_ROOT, *parts)

    def _documents_command(self, app_id: str, *arguments: str) -> list[str]:
        """Build an ``ios4 afc --documents`` command for ``app_id``."""
        return self._command("afc", "--documents", app_id, *arguments)

    def _run_documents(self, app_id: str, *arguments: str) -> CommandResult:
        """Run an ``afc --documents`` subcommand without raising on failure."""
        return self._ios4cli.run_documents(app_id, *arguments)

    def _documents_stat(self, app_id: str, remote: str) -> str | None:
        """Return the ``st_ifmt`` of ``remote``, or ``None`` when it is missing."""
        result = self._run_documents(app_id, "info", remote)
        if result.returncode != 0:
            return None
        match = _DOCUMENTS_IFMT_PATTERN.search(result.stdout)
        return match.group(1) if match is not None else _DOCUMENTS_FILE_IFMT

    def _documents_is_dir(self, app_id: str, remote: str) -> bool:
        """Return whether ``remote`` is an existing directory."""
        return self._documents_stat(app_id, remote) == _DOCUMENTS_DIR_IFMT

    @staticmethod
    def _unescape_listing_entry(value: str) -> str:
        """Decode the escapes ``afc list`` writes in its quoted entry names."""
        escapes = {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}
        decoded: list[str] = []
        characters = iter(value)
        for character in characters:
            if character != "\\":
                decoded.append(character)
                continue
            escaped = next(characters, "")
            decoded.append(escapes.get(escaped, escaped))
        return "".join(decoded)

    @classmethod
    def _parse_documents_listing(cls, output: str) -> list[str]:
        """Parse entry names out of ``afc list`` output, dropping ``.``/``..``."""
        entries: list[str] = []
        for line in output.splitlines():
            match = _DOCUMENTS_LIST_ENTRY_PATTERN.match(line)
            if match is None:
                continue
            name = cls._unescape_listing_entry(match.group(1))
            if name in (".", ".."):
                continue
            entries.append(name)
        return entries

    def _documents_mkdir(self, app_id: str, remote: str) -> bool:
        """Create ``remote`` and any missing parents. Existing dirs are fine."""
        result = self._run_documents(app_id, "mkdir", remote)
        if result.returncode != 0:
            logger.error(
                f"{_LOG_TAG} Failed to create {self.device_id}:{remote}: "
                f"{result.stderr.strip()}"
            )
            return False
        return True

    def _documents_upload(self, app_id: str, local: Path, remote: str) -> bool:
        """Upload a single file, creating its remote parent directory first."""
        parent = posixpath.dirname(remote)
        if parent and not self._documents_mkdir(app_id, parent):
            return False
        result = self._run_documents(app_id, "upload", str(local), remote)
        if result.returncode != 0:
            logger.error(
                f"{_LOG_TAG} Failed to push {local} to {self.device_id}:{remote}: "
                f"{result.stderr.strip()}"
            )
            return False
        return True

    def _documents_download(self, app_id: str, remote: str, local: Path) -> bool:
        """Download a single file, creating its local parent directory first."""
        local.parent.mkdir(parents=True, exist_ok=True)
        result = self._run_documents(app_id, "download", remote, str(local))
        if result.returncode != 0:
            logger.error(
                f"{_LOG_TAG} Failed to pull {self.device_id}:{remote} to {local}: "
                f"{result.stderr.strip()}"
            )
            return False
        return True

    def _documents_push_dir(self, app_id: str, local: Path, remote: str) -> bool:
        """Upload the ``local`` directory tree to ``remote`` entry by entry.

        ``afc upload`` only handles single files, so directories are walked
        here and recreated with ``afc mkdir``.
        """
        if not self._documents_mkdir(app_id, remote):
            return False
        succeeded = True
        for entry in sorted(local.iterdir()):
            child = posixpath.join(remote, entry.name)
            if entry.is_dir():
                succeeded &= self._documents_push_dir(app_id, entry, child)
            else:
                succeeded &= self._documents_upload(app_id, entry, child)
        return succeeded

    def _documents_pull_dir(self, app_id: str, remote: str, local: Path) -> bool:
        """Download the ``remote`` directory tree into ``local`` entry by entry."""
        local.mkdir(parents=True, exist_ok=True)
        listing = self._run_documents(app_id, "list", remote)
        if listing.returncode != 0:
            logger.error(
                f"{_LOG_TAG} Failed to list {self.device_id}:{remote}: "
                f"{listing.stderr.strip()}"
            )
            return False
        succeeded = True
        for name in self._parse_documents_listing(listing.stdout):
            child = posixpath.join(remote, name)
            if self._documents_is_dir(app_id, child):
                succeeded &= self._documents_pull_dir(app_id, child, local / name)
            else:
                succeeded &= self._documents_download(app_id, child, local / name)
        return succeeded

    def documents_exists(self, app_id: str, remote: str) -> bool:
        """Check whether ``remote`` exists in the app's Documents sandbox."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        exists = self._documents_stat(app_id, path) is not None
        logger.debug(f"{_LOG_TAG} {self.device_id}:{path} exists: {exists}")
        return exists

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entry names under ``remote`` in the app's Documents sandbox.

        When ``remote`` points to a file, the file's own name is returned so the
        behaviour matches shell ``ls`` on both files and directories.
        """
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        ifmt = self._documents_stat(app_id, path)
        if ifmt is None:
            raise FileNotFoundError(f"Remote path not found: {self.device_id}:{path}")
        if ifmt != _DOCUMENTS_DIR_IFMT:
            return [posixpath.basename(path)]
        logger.info(f"{_LOG_TAG} Listing {self.device_id}:{path}")
        result = self._run_documents(app_id, "list", path)
        if result.returncode != 0:
            raise IOSDevice4Error(
                f"{_LOG_TAG} Failed to list {self.device_id}:{path}: "
                f"{result.stderr.strip()}"
            )
        return self._parse_documents_listing(result.stdout)

    def documents_pull(
        self, app_id: str, remote: str, local: Path | str
    ) -> bool:
        """Pull a file or directory from the app's Documents sandbox."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        ifmt = self._documents_stat(app_id, path)
        if ifmt is None:
            logger.warning(f"{_LOG_TAG} Remote path not found: {self.device_id}:{path}")
            return False
        local_path = Path(local)
        if local_path.is_dir():
            local_path = local_path / posixpath.basename(path)
        logger.info(f"{_LOG_TAG} Pulling {self.device_id}:{path} to {local_path}")
        if ifmt == _DOCUMENTS_DIR_IFMT:
            return self._documents_pull_dir(app_id, path, local_path)
        return self._documents_download(app_id, path, local_path)

    def documents_push(
        self, app_id: str, local: Path | str, remote: str
    ) -> bool:
        """Push a local file or directory into the app's Documents sandbox."""
        self._require_app_and_remote(app_id, remote)
        local_path = Path(local)
        if not local_path.exists():
            logger.warning(f"{_LOG_TAG} Local path not found: {local_path}")
            return False
        path = self._documents_path(remote)
        if self._documents_is_dir(app_id, path):
            path = posixpath.join(path, local_path.name)
        logger.info(f"{_LOG_TAG} Pushing {local_path} to {self.device_id}:{path}")
        if local_path.is_dir():
            return self._documents_push_dir(app_id, local_path, path)
        return self._documents_upload(app_id, local_path, path)

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Remove a file or directory from the app's Documents sandbox."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        ifmt = self._documents_stat(app_id, path)
        if ifmt is None:
            logger.warning(f"{_LOG_TAG} Remote path not found: {self.device_id}:{path}")
            return False
        logger.info(f"{_LOG_TAG} Removing {self.device_id}:{path}")
        # `afc remove` only unlinks files and empty directories; `remove_all`
        # is the recursive variant.
        subcommand = "remove_all" if ifmt == _DOCUMENTS_DIR_IFMT else "remove"
        result = self._run_documents(app_id, subcommand, path)
        if result.returncode != 0:
            logger.error(
                f"{_LOG_TAG} Failed to remove {self.device_id}:{path}: "
                f"{result.stderr.strip()}"
            )
            return False
        return True

    def pull2(
        self, data_path: AppDataPath, remote: str, local: Path | str
    ) -> bool:
        del data_path, remote, local
        self._unsupported("pull2")

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        del data_path, remote
        self._unsupported("delete2")
