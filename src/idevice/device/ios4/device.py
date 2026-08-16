"""iOS game installation and launch via the Rust ``ios4`` CLI."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, NoReturn

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
_WDA_PROCESS_MARKERS = ("webdriveragent", "xctrunner", "iwda2-runner")
_IWDA2_PROCESS_PATTERN = re.compile(r"(?mi)^\s*(\d+)\s+iwda2-runner(?:\s|$)")
_IWDA2_DEFAULT_RUNNER_BUNDLE_ID = "com.idevice.iwda2.xctrunner"
_IWDA2_DEFAULT_SERVER_PORT = 18201


class IOSDevice4Error(RuntimeError):
    """Raised when an ``ios4`` device operation fails."""


class IOSDevice4(DeviceBase):
    """Install and launch iOS games through ``ios4``.

    IPA installation prefers the standalone libimobiledevice
    ``ideviceinstaller`` CLI and falls back to ``ios4 ideviceinstaller install``
    when that binary is not on the host. Every other operation uses these
    ``ios4`` subcommands:

    * ``application_listing`` for exact bundle-id checks.
    * ``process_control`` for launch environment and ordered arguments.
    * ``xctest`` for launching the preinstalled iwda2 Runner.
    * ``memgraph`` for Xcode-compatible process memory snapshots.
    * ``app_service uninstall`` and ``signal`` for lifecycle cleanup.

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
        self._iwda2_process: subprocess.Popen[str] | None = None
        self._iwda2_log_handle: IO[str] | None = None
        self._iwda2_server_port: int | None = None
        self._iwda2_startup_thread: threading.Thread | None = None
        self._iwda2_startup_error: Exception | None = None
        self._iwda2_stop_requested = threading.Event()

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
    def iwda2_process_id(self) -> int | None:
        """Return the host-side ios4 XCTest client PID, if running."""
        process = self._iwda2_process
        if process is None:
            return None
        if process.poll() is None:
            return process.pid
        self._clear_iwda2_process()
        return None

    @property
    def iwda2_startup_error(self) -> Exception | None:
        """Return the most recent background iwda2 startup error, if any."""
        return self._iwda2_startup_error

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
        app_id: str,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        """Launch an installed game with optional environment and ``argv``.

        Args:
            app_id: Bundle identifier to launch.
            args: Ordered command-line arguments passed to the game process.
            environment: Environment variables injected before process start.

        Raises:
            AppNotInstalledError: If ``app_id`` is not installed.
            IOSDevice4Error: If process control does not return a PID.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not self.is_installed(app_id):
            raise AppNotInstalledError(f"App not installed: {app_id}")

        command = self._command("process_control")
        if environment:
            command.extend(["--env", self._encode_environment(environment)])
        if args:
            command.extend(["--args", self._encode_launch_arguments(args)])
        command.append(app_id)

        logger.info(f"{_LOG_TAG} Launching {app_id} on {self.device_id}")
        result = self._runner.run(command)
        match = _PID_PATTERN.search(f"{result.stdout}\n{result.stderr}")
        if match is None:
            raise IOSDevice4Error(
                f"{_LOG_TAG} process_control did not return a PID for {app_id}: "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        self._last_launch_pid = int(match.group(1))
        self._last_launch_app_id = app_id

    def run_iwda2(
        self,
        *,
        runner_bundle_id: str = _IWDA2_DEFAULT_RUNNER_BUNDLE_ID,
        target_bundle_id: str | None = None,
        server_port: int = _IWDA2_DEFAULT_SERVER_PORT,
        auto_dismiss_dialogs: bool = True,
        dialog_scan_interval: float = 0.5,
        max_session_seconds: float = 3600,
        command_timeout_seconds: float = 30,
        wait_ready: bool = True,
        ready_timeout: float = 60,
        log_path: Path | str | None = None,
    ) -> threading.Thread:
        """Launch the preinstalled iwda2 XCTest Runner on a background thread.

        The method returns as soon as the startup thread has been scheduled, so
        XCTest startup and HTTP readiness polling never block the calling
        thread. Use :attr:`iwda2_process_id` to read the host-side client PID and
        :attr:`iwda2_startup_error` to inspect an asynchronous startup failure.

        Args:
            runner_bundle_id: Installed iwda2 ``.xctrunner`` bundle identifier.
            target_bundle_id: Optional target app used for app-owned dialog
                scans and orientation-aware taps.
            server_port: iwda2 HTTP port on the device.
            auto_dismiss_dialogs: Enable the runtime dialog watcher.
            dialog_scan_interval: Seconds between automatic dialog scans.
            max_session_seconds: Maximum lifetime of the XCTest session.
            command_timeout_seconds: iwda2 HTTP command timeout.
            wait_ready: Poll ``/api/health`` on the startup thread when this
                device has a non-empty :attr:`device_ip`.
            ready_timeout: Maximum number of seconds to wait for readiness.
            log_path: Optional host file receiving ios4 output.

        Returns:
            threading.Thread: The started background startup thread.

        Raises:
            AppNotInstalledError: If the Runner is not installed.
            IOSDevice4Error: If another client or startup thread is active.
            ValueError: If an identifier or numeric option is invalid.
        """
        if not runner_bundle_id:
            raise ValueError("runner_bundle_id must be a non-empty string")
        if target_bundle_id is not None and not target_bundle_id:
            raise ValueError("target_bundle_id must be non-empty when provided")
        if not isinstance(auto_dismiss_dialogs, bool):
            raise ValueError("auto_dismiss_dialogs must be a bool")
        self._validate_port(server_port)
        self._validate_positive_number(
            dialog_scan_interval, "dialog_scan_interval"
        )
        self._validate_positive_number(max_session_seconds, "max_session_seconds")
        self._validate_positive_number(
            command_timeout_seconds, "command_timeout_seconds"
        )
        self._validate_positive_number(ready_timeout, "ready_timeout")

        active_process = self._iwda2_process
        if active_process is not None and active_process.poll() is None:
            raise IOSDevice4Error(
                f"{_LOG_TAG} iwda2 is already running with client PID "
                f"{active_process.pid}"
            )
        active_thread = self._iwda2_startup_thread
        if active_thread is not None and active_thread.is_alive():
            raise IOSDevice4Error(f"{_LOG_TAG} iwda2 startup is already running")
        self._clear_iwda2_process()

        if not self.is_installed(runner_bundle_id):
            raise AppNotInstalledError(f"iwda2 Runner not installed: {runner_bundle_id}")

        environment = {
            "SERVER_PORT": str(server_port),
            "AUTO_DISMISS_DIALOGS": str(auto_dismiss_dialogs).lower(),
            "DIALOG_SCAN_INTERVAL": self._format_number(dialog_scan_interval),
            "MAX_SESSION_SECONDS": self._format_number(max_session_seconds),
            "COMMAND_TIMEOUT_SECONDS": self._format_number(
                command_timeout_seconds
            ),
        }
        if target_bundle_id:
            environment["TARGET_BUNDLE_ID"] = target_bundle_id

        command = self._command(
            "xctest", "--env", self._encode_environment(environment)
        )
        command.append(runner_bundle_id)
        if target_bundle_id:
            command.append(target_bundle_id)

        log_handle: IO[str] | None = None
        output: int | IO[str] = subprocess.DEVNULL
        if log_path is not None:
            resolved_log_path = Path(log_path).expanduser().resolve()
            resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = resolved_log_path.open(
                "a", encoding="utf-8", errors="replace"
            )
            output = log_handle

        self._iwda2_log_handle = log_handle
        self._iwda2_server_port = server_port
        self._iwda2_startup_error = None
        self._iwda2_stop_requested.clear()
        startup_thread = threading.Thread(
            target=self._run_iwda2_startup,
            kwargs={
                "command": command,
                "output": output,
                "server_port": server_port,
                "wait_ready": wait_ready,
                "ready_timeout": ready_timeout,
            },
            name=f"iwda2-startup-{self.device_id}",
            daemon=True,
        )
        self._iwda2_startup_thread = startup_thread
        startup_thread.start()
        return startup_thread

    def _run_iwda2_startup(
        self,
        *,
        command: list[str],
        output: int | IO[str],
        server_port: int,
        wait_ready: bool,
        ready_timeout: float,
    ) -> None:
        """Start and monitor iwda2 without occupying the caller's thread."""
        logger.info(f"{_LOG_TAG} Starting iwda2 on {self.device_id}")
        try:
            process = subprocess.Popen(
                command,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._iwda2_process = process
            if self._iwda2_stop_requested.is_set():
                self.stop_iwda2(graceful=False)
                return
            self._wait_for_iwda2_startup(
                process,
                server_port=server_port,
                wait_ready=wait_ready,
                timeout=ready_timeout,
            )
        except Exception as exc:
            if isinstance(exc, OSError):
                exc = IOSDevice4Error(
                    f"{_LOG_TAG} Could not start iwda2 XCTest client: {exc}"
                )
            self._iwda2_startup_error = exc
            logger.error(f"{_LOG_TAG} iwda2 background startup failed: {exc}")
            process = self._iwda2_process
            if process is not None and process.poll() is None:
                process.terminate()
            self._clear_iwda2_process()

    def stop_iwda2(
        self, *, graceful: bool = True, timeout: float = 10
    ) -> None:
        """Stop the iwda2 XCTest session and its host-side client process."""
        self._validate_positive_number(timeout, "timeout")
        self._iwda2_stop_requested.set()
        process = self._iwda2_process
        startup_thread = self._iwda2_startup_thread
        if process is None and startup_thread is not None and startup_thread.is_alive():
            startup_thread.join(timeout=min(timeout, 1))
            process = self._iwda2_process
        if process is None:
            self._ensure_iwda2_runner_stopped(timeout=timeout, wait_first=False)
            self._clear_iwda2_process()
            return

        exit_requested = False
        if graceful and process.poll() is None and self.device_ip:
            exit_requested = self._request_iwda2_exit(timeout=min(timeout, 3))

        if exit_requested and process.poll() is None:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._ensure_iwda2_runner_stopped(timeout=timeout, wait_first=True)
        self._clear_iwda2_process()

    @staticmethod
    def _validate_positive_number(value: float, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a positive number")
        if value <= 0:
            raise ValueError(f"{name} must be a positive number")

    @staticmethod
    def _validate_port(port: int) -> None:
        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("server_port must be an integer between 1 and 65535")
        if not 1 <= port <= 65535:
            raise ValueError("server_port must be an integer between 1 and 65535")

    @staticmethod
    def _format_number(value: float) -> str:
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else str(numeric)

    def _wait_for_iwda2_startup(
        self,
        process: subprocess.Popen[str],
        *,
        server_port: int,
        wait_ready: bool,
        timeout: float,
    ) -> None:
        """Fail on early process exit and optionally wait for HTTP readiness."""
        if not wait_ready or not self.device_ip:
            time.sleep(min(0.25, timeout))
            returncode = process.poll()
            if returncode is not None:
                raise IOSDevice4Error(
                    f"{_LOG_TAG} iwda2 XCTest client exited during startup "
                    f"with code {returncode}"
                )
            return

        deadline = time.monotonic() + timeout
        url = f"http://{self.device_ip}:{server_port}/api/health"
        last_error = "service did not respond"
        while time.monotonic() < deadline:
            returncode = process.poll()
            if returncode is not None:
                raise IOSDevice4Error(
                    f"{_LOG_TAG} iwda2 XCTest client exited during startup "
                    f"with code {returncode}"
                )
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return
                    last_error = f"HTTP {response.status}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.25)
        raise IOSDevice4Error(
            f"{_LOG_TAG} iwda2 did not become ready at {url} within "
            f"{self._format_number(timeout)}s: {last_error}"
        )

    def _request_iwda2_exit(self, *, timeout: float) -> bool:
        port = self._iwda2_server_port or _IWDA2_DEFAULT_SERVER_PORT
        url = f"http://{self.device_ip}:{port}/api/exit"
        try:
            with urllib.request.urlopen(url, timeout=timeout):
                return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(f"{_LOG_TAG} Could not request iwda2 exit: {exc}")
            return False

    def _clear_iwda2_process(self) -> None:
        self._iwda2_process = None
        self._iwda2_server_port = None
        if self._iwda2_log_handle is not None:
            self._iwda2_log_handle.close()
            self._iwda2_log_handle = None

    def _iwda2_runner_pids(self) -> list[int]:
        """Return device-side iwda2-Runner PIDs from DVT process listing."""
        result = self._runner.run(
            self._command("device_info", "processes"), check=False
        )
        if result.returncode != 0:
            return []
        return [
            int(match.group(1))
            for match in _IWDA2_PROCESS_PATTERN.finditer(result.stdout)
        ]

    def _ensure_iwda2_runner_stopped(
        self, *, timeout: float, wait_first: bool
    ) -> None:
        """Wait for XCTest teardown, then kill only a lingering iwda2 Runner."""
        deadline = time.monotonic() + timeout
        pids = self._iwda2_runner_pids()
        while wait_first and pids and time.monotonic() < deadline:
            time.sleep(0.25)
            pids = self._iwda2_runner_pids()
        for pid in pids:
            logger.warning(
                f"{_LOG_TAG} Terminating lingering device iwda2-Runner PID {pid}"
            )
            self._runner.run(
                self._command("app_service", "signal", str(pid), "9"),
                check=False,
            )

    def xmemory_shot(
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

    def capture_memgraph(
        self,
        output: Path | str,
        *,
        pid: int | None = None,
    ) -> Path:
        """Compatibility alias for :meth:`xmemory_shot`."""
        return self.xmemory_shot(output, pid=pid)

    def stop_app(self, app_id: str | None = None) -> None:
        """Signal the process most recently launched by this instance."""
        target = self._resolve_app_id(app_id)
        if self._last_launch_pid is None or self._last_launch_app_id != target:
            raise IOSDevice4Error(
                f"{_LOG_TAG} No tracked PID for {target}; launch it with this instance first"
            )
        self._runner.run(
            self._command("app_service", "signal", str(self._last_launch_pid), "9")
        )
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
