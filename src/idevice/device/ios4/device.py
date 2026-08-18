"""iOS game installation and launch via the Rust ``ios4`` CLI."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import NoReturn

import wda

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import AppNotInstalledError, DeviceNotFoundError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
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
_UDID_PATTERN = re.compile(
    r'UniqueDeviceID["\']?\s*:\s*String\(\s*"([^"\\]+)"\s*\)'
)
_WDA_PROCESS_MARKERS = ("webdriveragent", "xctrunner")
_WDA_PORT = 8100
_POPUP_BUTTON_LABELS = (
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
    * WebDriverAgent for launches, with ``process_control`` as the fallback
      that also reports the launch PID.
    * ``screenshot`` for screen capture.
    * ``memgraph`` for Xcode-compatible process memory snapshots.
    * WebDriverAgent, with ``pkill --bundle`` as the stop fallback.
    * ``app_service uninstall`` for lifecycle cleanup.

    File transfer and Documents-sandbox operations are intentionally not
    implemented because the ios4 backend currently focuses on the game
    installation and process-launch workflow.
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
        self._binary = ios4_binary()
        self._runner = SubprocessRunner()
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        self._last_launch_pid: int | None = None
        self._last_launch_app_id = ""
        self._message_popup_stop_event = threading.Event()
        self._message_popup_stop_event.set()
        self._message_popup_thread: threading.Thread | None = None
        self._message_popup_lock = threading.Lock()

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
        command = [ios4_binary(), "ideviceinfo"]
        result = SubprocessRunner().run(command)
        match = _UDID_PATTERN.search(result.stdout)
        if match is None:
            raise DeviceNotFoundError(
                f"{_LOG_TAG} Could not read UniqueDeviceID from ideviceinfo output"
            )
        return match.group(1)

    def _command(self, *arguments: str) -> list[str]:
        """Build an ios4 command for the bound device."""
        return [self._binary, "--udid", self.device_id, *arguments]

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
        self._runner.run(self._command("app_service", "uninstall", app_id))
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        """Check an exact bundle id using ``application_listing``."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        result = self._runner.run(
            self._command("application_listing"), check=False
        )
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

        result = self._runner.run(command)
        match = _PID_PATTERN.search(f"{result.stdout}\n{result.stderr}")
        if match is None:
            raise IOSDevice4Error(
                f"{_LOG_TAG} process_control did not return a PID for {target}: "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        self._last_launch_pid = int(match.group(1))
        self._last_launch_app_id = target

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
            result = self._runner.run(
                self._command(
                    "memgraph", str(target_pid), str(temporary_path)
                ),
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
        result = self._runner.run(
            self._command("pkill", "--bundle", target), check=False
        )
        if result.returncode != 0:
            raise IOSDevice4Error(
                f"{_LOG_TAG} Failed to stop {target} on {self.device_id}: "
                f"returncode={result.returncode}, stdout={result.stdout!r}, "
                f"stderr={result.stderr!r}"
            )
        self._clear_last_launch(target)

    def _wda_url(self) -> str | None:
        """Return the bound WDA base URL, or ``None`` for the client default."""
        return f"http://{self.device_ip}:{_WDA_PORT}" if self.device_ip else None

    def _launch_app_via_wda(
        self, app_id: str, args: list[str], environment: dict[str, str]
    ) -> bool:
        """Return whether WDA launched ``app_id``.

        The WDA session is deliberately left open: deleting it terminates the
        application under test on some WebDriverAgent builds.
        """
        try:
            session = wda.Client(self._wda_url()).session(
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
            with wda.Client(self._wda_url()).session() as session:
                session.app_terminate(app_id)
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
        result = self._runner.run(
            self._command("device_info", "processes"), check=False
        )
        if result.returncode != 0:
            return False
        output = result.stdout.lower()
        return any(marker in output for marker in _WDA_PROCESS_MARKERS)

    def screenshot(self, local: Path | str) -> bool:
        """Capture a screenshot with the ios4 screenshot service."""
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._runner.run(
            self._command("screenshot", str(local_path)), check=False
        )
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
            with wda.Client(self._wda_url()).session() as session:
                session.click(float(x), float(y))
        except Exception as exc:
            raise IOSDevice4Error(
                f"{_LOG_TAG} WDA failed to tap ({x}, {y}) on "
                f"{self.device_id}{app_context}: {exc}"
            ) from exc

    def start_message_popup_handler(
        self,
        *,
        button_labels: tuple[str, ...] | list[str] | None = None,
        interval: float = 1.0,
        timeout: float = 1.0,
    ) -> None:
        """Start a background thread to auto-dismiss message popups.

        Args:
            button_labels: Labels tried per popup. Defaults to built-in labels.
            interval: Seconds between each scan.
            timeout: Per-button/alert timeout passed to WDA.
        """
        if interval <= 0:
            raise ValueError("interval must be a positive number")
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")

        labels = self._normalize_message_popup_labels(button_labels)

        with self._message_popup_lock:
            if (
                self._message_popup_thread is not None
                and self._message_popup_thread.is_alive()
            ):
                return

            self._message_popup_stop_event.clear()
            self._message_popup_thread = threading.Thread(
                target=self._run_message_popup_handler,
                args=(labels, interval, timeout),
                daemon=True,
            )
            self._message_popup_thread.start()

    def stop_message_popup_handler(self) -> None:
        """Stop the background popup handler thread."""
        with self._message_popup_lock:
            thread = self._message_popup_thread
            self._message_popup_stop_event.set()
            self._message_popup_thread = None

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _run_message_popup_handler(
        self,
        labels: tuple[str, ...],
        interval: float,
        timeout: float,
    ) -> None:
        try:
            session = wda.Client(self._wda_url()).session()
        except Exception as exc:
            logger.debug(
                f"{_LOG_TAG} popup handler stopped due to WDA error on "
                f"{self.device_id}: {exc}"
            )
            return

        try:
            while not self._message_popup_stop_event.is_set():
                try:
                    self._dismiss_message_popup_with_session(session, labels, timeout)
                except Exception:
                    logger.debug(
                        f"{_LOG_TAG} popup handler ignored transient WDA error "
                        f"on {self.device_id}"
                    )
                if self._message_popup_stop_event.wait(interval):
                    break
        finally:
            self._close_wda_session(session)

    def _dismiss_message_popup_with_session(
        self, session: wda.Client, labels: tuple[str, ...], timeout: float
    ) -> bool:
        """Try to dismiss one popup using a single WDA session."""
        alert = getattr(session, "alert", None)
        if alert is None or not self._exists_with_timeout(alert, timeout):
            return False

        for label in labels:
            if self._tap_button_if_present(session, label, timeout):
                logger.info(
                    f"{_LOG_TAG} Dismissed message popup on "
                    f"{self.device_id} using button={label!r}"
                )
                return True
        return False

    def dismiss_message_popup(
        self,
        *,
        button_labels: tuple[str, ...] | list[str] | None = None,
        timeout: float = 1.0,
    ) -> bool:
        """Dismiss the topmost message popup via WDA alert controls.

        Args:
            button_labels: Labels tried in order. Defaults to a built-in set
                that covers common iOS and Chinese popups.
            timeout: Per-element check timeout for alert/button presence.

        Returns:
            bool: ``True`` when a button is clicked.
        """
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")

        labels = self._normalize_message_popup_labels(button_labels)

        try:
            with wda.Client(self._wda_url()).session() as session:
                return self._dismiss_message_popup_with_session(
                    session, labels, timeout
                )
        except Exception as exc:
            raise IOSDevice4Error(
                f"{_LOG_TAG} WDA failed to handle message popup on "
                f"{self.device_id}: {exc}"
            ) from exc

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
    def _close_wda_session(session: object | None) -> None:
        """Close a WDA session created manually in a background thread."""
        if session is None:
            return
        exit_method = getattr(session, "__exit__", None)
        if callable(exit_method):
            try:
                exit_method(None, None, None)
                return
            except Exception:
                pass
        close_method = getattr(session, "close", None)
        if callable(close_method):
            close_method()

    @staticmethod
    def _tap_button_if_present(session: wda.Client, label: str, timeout: float) -> bool:
        """Return ``True`` when a button named ``label`` exists and is clicked."""
        button = session(text=label)
        return IOSDevice4._tap_if_exists(button, timeout)

    @staticmethod
    def _tap_if_exists(element: object, timeout: float) -> bool:
        exists = getattr(element, "exists", None)
        if not callable(exists):
            return False

        try:
            present = exists(timeout=timeout)
        except TypeError:
            present = exists()
        if not present:
            return False

        click = getattr(element, "click", None)
        if not callable(click):
            return False
        click()
        return True

    @staticmethod
    def _exists_with_timeout(element: object, timeout: float) -> bool:
        exists = getattr(element, "exists", None)
        if not callable(exists):
            return False

        try:
            return bool(exists(timeout=timeout))
        except TypeError:
            return bool(exists())

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

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
    ) -> None:
        del x1, y1, x2, y2, duration_ms
        self._unsupported("swipe")

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

    def documents_exists(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        self._unsupported("documents_exists")

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        del app_id, remote
        self._unsupported("documents_ls")

    def documents_pull(
        self, app_id: str, remote: str, local: Path | str
    ) -> bool:
        del app_id, remote, local
        self._unsupported("documents_pull")

    def documents_push(
        self, app_id: str, local: Path | str, remote: str
    ) -> bool:
        del app_id, local, remote
        self._unsupported("documents_push")

    def documents_rm(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        self._unsupported("documents_rm")

    def pull2(
        self, data_path: AppDataPath, remote: str, local: Path | str
    ) -> bool:
        del data_path, remote, local
        self._unsupported("pull2")

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        del data_path, remote
        self._unsupported("delete2")
