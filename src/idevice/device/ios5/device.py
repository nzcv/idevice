"""iOS lifecycle backend built on Apple's ``xcrun devicectl`` (CoreDevice)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import (
    AppNotInstalledError,
    CommandExecutionError,
    DeviceNotFoundError,
)
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.config import device_id as env_device_id
from idevice.device.config import device_ip as env_device_ip
from idevice.device.config import ios4_binary, xcrun_binary
from idevice.device.config import package_name as env_package_name

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice5]"
_WDA_PROCESS_MARKERS = ("webdriveragent", "xctrunner")
_APP_DATA_DOMAIN = "appDataContainer"
_WIRED_TRANSPORT = "wired"
_DOCUMENTS_ROOT = "Documents"
_DEFAULT_TIMEOUT = 120
_INSTALL_TIMEOUT = 3600
_MEMGRAPH_TIMEOUT = 600


class IOSDevice5Error(RuntimeError):
    """Raised when a ``devicectl`` device operation fails."""


@dataclass(frozen=True)
class DevicectlOutcome:
    """Parsed outcome of a single ``xcrun devicectl`` invocation.

    Attributes:
        returncode: Exit status of the ``xcrun devicectl`` process.
        result: The ``result`` section of the JSON output document, or ``{}``.
        error: Human-readable failure description, empty when successful.
        stdout: Raw standard output, kept for diagnostics.
        stderr: Raw standard error, kept for diagnostics.
    """

    returncode: int
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self) -> bool:
        """Return whether devicectl exited cleanly and reported no error."""
        return self.returncode == 0 and not self.error


def _error_message(document: dict[str, Any]) -> str:
    """Flatten a devicectl ``error`` document into one readable line."""
    error = document.get("error")
    if not isinstance(error, dict):
        return ""
    parts: list[str] = []
    while isinstance(error, dict):
        user_info = error.get("userInfo")
        user_info = user_info if isinstance(user_info, dict) else {}
        description = user_info.get("NSLocalizedDescription")
        if isinstance(description, dict):
            description = description.get("string")
        if isinstance(description, str) and description:
            parts.append(description)
        elif error.get("domain"):
            parts.append(f"{error.get('domain')} error {error.get('code')}")
        underlying = user_info.get("NSUnderlyingError")
        error = underlying.get("error") if isinstance(underlying, dict) else None
    return ": ".join(parts) or "devicectl reported an unspecified error"


def _read_document(json_path: Path) -> dict[str, Any]:
    """Read a devicectl JSON output file, tolerating a missing or partial write."""
    try:
        raw = json_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug(f"{_LOG_TAG} Could not read devicectl JSON output: {exc}")
        return {}
    if not raw.strip():
        return {}
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug(f"{_LOG_TAG} Malformed devicectl JSON output: {exc}")
        return {}
    return document if isinstance(document, dict) else {}


def _run_devicectl(
    xcrun: str,
    runner: SubprocessRunner,
    arguments: list[str],
    *,
    timeout: int,
) -> DevicectlOutcome:
    """Run ``xcrun devicectl`` and parse the JSON document it writes.

    JSON output is the only interface Apple guarantees to stay stable, and up to
    Xcode 26 it can only be written to a file, hence the temporary path.
    """
    with tempfile.NamedTemporaryFile(
        prefix="devicectl-", suffix=".json", delete=False
    ) as handle:
        json_path = Path(handle.name)
    # ``device process launch`` uses ``--`` to separate devicectl options from
    # the launched application's argv.  Keep our output options before that
    # boundary or devicectl will pass them to the application instead of
    # writing the JSON result file.
    try:
        separator_index = arguments.index("--")
    except ValueError:
        separator_index = len(arguments)
    command = [
        xcrun,
        "devicectl",
        *arguments[:separator_index],
        "--quiet",
        "--timeout",
        str(timeout),
        "--json-output",
        str(json_path),
        *arguments[separator_index:],
    ]
    try:
        result = runner.run(command, check=False, timeout=timeout + 30)
        document = _read_document(json_path)
    finally:
        json_path.unlink(missing_ok=True)

    error = _error_message(document)
    if not error and result.returncode != 0:
        error = result.stderr.strip() or f"devicectl exited with {result.returncode}"
    section = document.get("result")
    return DevicectlOutcome(
        returncode=result.returncode,
        result=section if isinstance(section, dict) else {},
        error=error,
        stdout=result.stdout,
        stderr=result.stderr,
    )


class IOSDevice5(DeviceBase):
    """Drive an iOS device through Apple's own ``xcrun devicectl``.

    Every operation but one is a CoreDevice command, so the backend needs no
    third-party CLI and inherits Apple's stable JSON output:

    * ``device install app`` / ``device uninstall app`` for lifecycle.
    * ``device info apps`` for exact bundle-id checks.
    * ``device process launch`` for launch environment and ordered arguments.
    * ``device info processes`` and ``device process terminate`` for teardown.
    * ``device copy to`` / ``device copy from`` and ``device info files`` for
      the app data container, including the Documents sandbox.

    CoreDevice does not expose every operation this backend needs:

    * :meth:`capture_memgraph` shells out to the Rust ``ios4`` CLI, the only
      tool that reaches the DVT memory-graph service.
    * :meth:`screenshot` uses ``device capture screenshot`` where Xcode
      provides it (Xcode 27+), then falls back to ``ios4``.

    File removal (``documents_rm``, ``delete2``) and ``swipe`` stay
    unimplemented because CoreDevice offers no matching service.
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
            device_id, device_ip, platform="ios5", package_name=package_name
        )
        if sys.platform != "darwin":
            raise IOSDevice5Error(
                f"{_LOG_TAG} devicectl is only available on macOS with Xcode "
                f"installed; this host is {sys.platform}"
            )
        self._xcrun = xcrun_binary()
        self._runner = SubprocessRunner()
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        self._last_launch_pid: int | None = None
        self._last_launch_app_id = ""
        self._capture_screenshot_supported: bool | None = None

        if self._resolve_binary(self._xcrun) is None:
            logger.error(f"{_LOG_TAG} `{self._xcrun}` CLI not found")
            raise IOSDevice5Error(
                f"`{self._xcrun}` CLI not found. Install Xcode, or set "
                "IDEVICE_XCRUN_BINARY."
            )

    @property
    def last_launch_pid(self) -> int | None:
        """Return the PID from the most recent successful launch."""
        return self._last_launch_pid

    @classmethod
    def from_env(cls) -> IOSDevice5:
        """Build an :class:`IOSDevice5` from the ``GAUTO_*`` environment."""
        return cls(
            env_device_id(),
            device_ip=env_device_ip(),
            package_name=env_package_name(),
        )

    @classmethod
    def default_udid(cls) -> str:
        """Return the UDID of the first USB-attached device, in listing order.

        A farm host remembers every device it ever paired, and CoreDevice keeps
        reaching most of them over Wi-Fi (``localNetwork``) or not at all. Only
        a cabled device reports the ``wired`` transport, which makes it the one
        unambiguous answer to "the device on this host".

        Raises:
            DeviceNotFoundError: If no USB-attached device reports a UDID.
        """
        outcome = _run_devicectl(
            xcrun_binary(), SubprocessRunner(), ["list", "devices"], timeout=30
        )
        if not outcome.succeeded:
            raise DeviceNotFoundError(
                f"{_LOG_TAG} Could not list CoreDevice devices: {outcome.error}"
            )
        for entry in outcome.result.get("devices", []):
            if not isinstance(entry, dict):
                continue
            connection = entry.get("connectionProperties", {})
            if connection.get("transportType") != _WIRED_TRANSPORT:
                continue
            udid = entry.get("hardwareProperties", {}).get("udid")
            if udid:
                return udid
        raise DeviceNotFoundError(
            f"{_LOG_TAG} No USB-attached device was found; connect one by cable "
            "or pass an explicit UDID"
        )

    @staticmethod
    def _resolve_binary(binary: str) -> str | None:
        """Return the usable path for ``binary``, or ``None`` when missing."""
        resolved = shutil.which(binary)
        if resolved is not None:
            return resolved
        return binary if Path(binary).is_file() else None

    def _command(self, subcommand: list[str], *arguments: str) -> list[str]:
        """Build a devicectl argument list bound to this device."""
        return [*subcommand, "--device", self.device_id, *arguments]

    def _run(
        self, arguments: list[str], *, timeout: int = _DEFAULT_TIMEOUT
    ) -> DevicectlOutcome:
        """Run one devicectl command for this device."""
        return _run_devicectl(
            self._xcrun, self._runner, arguments, timeout=timeout
        )

    def _require(
        self, outcome: DevicectlOutcome, action: str
    ) -> dict[str, Any]:
        """Return the result section, raising when devicectl reported failure."""
        if not outcome.succeeded:
            raise IOSDevice5Error(
                f"{_LOG_TAG} {action} failed on {self.device_id}: {outcome.error}"
            )
        return outcome.result

    @staticmethod
    def _encode_environment(environment: dict[str, str]) -> str:
        """Encode launch environment variables as devicectl's JSON dictionary."""
        for key, value in environment.items():
            if not key or not isinstance(key, str):
                raise ValueError("environment names must be non-empty strings")
            if not isinstance(value, str):
                raise TypeError(f"environment value for {key!r} must be a string")
        return json.dumps(environment, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _validate_launch_arguments(arguments: list[str]) -> list[str]:
        """Return ``arguments`` unchanged after rejecting unusable entries."""
        for index, argument in enumerate(arguments):
            if not isinstance(argument, str):
                raise TypeError(f"launch argument at index {index} must be a string")
            if not argument:
                raise ValueError(f"launch argument at index {index} cannot be empty")
        return list(arguments)

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        """Install an IPA or ``.app`` directory with ``device install app``.

        The installed bundle id comes back in devicectl's JSON output, so it is
        cached even when the caller does not name it.

        Args:
            package_path: IPA file or app directory to install.
            app_id: Bundle identifier overriding the one devicectl reports.

        Returns:
            bool: Whether the installation reported success.

        Raises:
            FileNotFoundError: If ``package_path`` does not exist.
        """
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")

        logger.info(
            f"{_LOG_TAG} Installing package on {self.device_id}: {package_path}"
        )
        outcome = self._run(
            self._command(["device", "install", "app"], str(package_path)),
            timeout=_INSTALL_TIMEOUT,
        )
        if not outcome.succeeded:
            logger.error(
                f"{_LOG_TAG} Installation failed on {self.device_id}: "
                f"{outcome.error}, stdout={outcome.stdout!r}, "
                f"stderr={outcome.stderr!r}"
            )
            return False

        installed = outcome.result.get("installedApplications") or []
        record = installed[0] if installed and isinstance(installed[0], dict) else {}
        resolved = app_id or record.get("bundleID") or ""
        if resolved:
            self._app_cache.add(
                resolved,
                version=package_path.stem,
                path=record.get("installationURL"),
            )
        return True

    def uninstall(self, app_id: str) -> None:
        """Uninstall an application with ``device uninstall app``."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        logger.info(f"{_LOG_TAG} Uninstalling {app_id} on {self.device_id}")
        outcome = self._run(self._command(["device", "uninstall", "app"], app_id))
        self._require(outcome, f"uninstall of {app_id}")
        self._app_cache.remove(app_id)

    def _app_record(
        self, app_id: str, *, strict: bool = False
    ) -> dict[str, Any] | None:
        """Return the ``device info apps`` entry for an exact bundle id.

        Args:
            app_id: Bundle identifier to look up.
            strict: Raise when the listing itself fails instead of reporting the
                app as absent, so a dropped tunnel is never mistaken for an
                uninstalled app.

        Raises:
            IOSDevice5Error: If ``strict`` and devicectl could not list apps.
        """
        outcome = self._run(
            self._command(
                ["device", "info", "apps"],
                "--bundle-id",
                app_id,
                "--include-default-apps",
                "--include-removable-apps",
                "--include-app-clips",
            ),
            timeout=60,
        )
        if not outcome.succeeded:
            if strict:
                raise IOSDevice5Error(
                    f"{_LOG_TAG} App listing failed on {self.device_id}: "
                    f"{outcome.error}"
                )
            logger.warning(
                f"{_LOG_TAG} App listing failed on {self.device_id}: {outcome.error}"
            )
            return None
        for entry in outcome.result.get("apps", []):
            if isinstance(entry, dict) and entry.get("bundleIdentifier") == app_id:
                return entry
        return None

    def is_installed(self, app_id: str) -> bool:
        """Check an exact bundle id through ``device info apps``.

        An unreachable device reads as not installed; use :meth:`launch_app` or
        :meth:`stop_app` when the difference matters.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        return self._app_record(app_id) is not None

    def launch_app(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        terminate_existing: bool = True,
        activate: bool = True,
    ) -> None:
        """Launch an installed app with optional environment and ``argv``.

        Args:
            app_id: Bundle identifier to launch. When omitted or empty, uses
                the bound :attr:`package_name`.
            args: Ordered command-line arguments passed to the app process.
            environment: Environment variables injected before process start.
            terminate_existing: Kill a running instance before launching.
            activate: Bring the app to the foreground.

        Raises:
            ValueError: If both ``app_id`` and :attr:`package_name` are empty.
            AppNotInstalledError: If the resolved bundle id is not installed.
            IOSDevice5Error: If the device is unreachable, the launch fails, or
                no PID comes back.
        """
        target = self._resolve_app_id(app_id)
        if self._app_record(target, strict=True) is None:
            raise AppNotInstalledError(f"App not installed: {target}")

        options: list[str] = ["--activate" if activate else "--no-activate"]
        if terminate_existing:
            options.append("--terminate-existing")
        if environment:
            options.extend(
                ["--environment-variables", self._encode_environment(environment)]
            )
        launch_arguments = self._validate_launch_arguments(args or [])

        logger.info(f"{_LOG_TAG} Launching {target} on {self.device_id}")
        outcome = self._run(
            self._command(
                ["device", "process", "launch"],
                *options,
                "--",
                target,
                *launch_arguments,
            )
        )
        result = self._require(outcome, f"launch of {target}")
        process = result.get("process")
        pid = process.get("processIdentifier") if isinstance(process, dict) else None
        if not isinstance(pid, int):
            raise IOSDevice5Error(
                f"{_LOG_TAG} Launch of {target} returned no PID: {result!r}"
            )
        self._last_launch_pid = pid
        self._last_launch_app_id = target
        logger.info(f"{_LOG_TAG} Launched {target} on {self.device_id} with PID {pid}")

    def _processes(self) -> list[dict[str, Any]]:
        """Return the device process table, or an empty list when unreadable."""
        outcome = self._run(self._command(["device", "info", "processes"]))
        if not outcome.succeeded:
            logger.warning(
                f"{_LOG_TAG} Process listing failed on {self.device_id}: "
                f"{outcome.error}"
            )
            return []
        return [
            entry
            for entry in outcome.result.get("runningProcesses", [])
            if isinstance(entry, dict)
        ]

    def _bundle_process_ids(self, bundle_url: str) -> list[int]:
        """Return PIDs whose executable lives inside ``bundle_url``."""
        if not bundle_url:
            return []
        prefix = bundle_url if bundle_url.endswith("/") else f"{bundle_url}/"
        pids: list[int] = []
        for entry in self._processes():
            executable = entry.get("executable")
            pid = entry.get("processIdentifier")
            if isinstance(executable, str) and isinstance(pid, int):
                if executable.startswith(prefix):
                    pids.append(pid)
        return pids

    def _terminate(self, pid: int, *, kill: bool = True) -> bool:
        """Terminate one device process, returning whether devicectl agreed."""
        arguments = ["--pid", str(pid)]
        if kill:
            arguments.append("--kill")
        outcome = self._run(
            self._command(["device", "process", "terminate"], *arguments), timeout=60
        )
        if not outcome.succeeded:
            logger.warning(
                f"{_LOG_TAG} Could not terminate PID {pid} on {self.device_id}: "
                f"{outcome.error}"
            )
        return outcome.succeeded

    def stop_app(self, app_id: str | None = None) -> None:
        """Kill every process of the app, whoever launched it.

        A stopped app is not an error; a missing app is.

        Raises:
            AppNotInstalledError: If the resolved bundle id is not installed.
            IOSDevice5Error: If the device is unreachable.
        """
        target = self._resolve_app_id(app_id)
        record = self._app_record(target, strict=True)
        if record is None:
            raise AppNotInstalledError(f"App not installed: {target}")

        logger.info(f"{_LOG_TAG} Stopping app on iOS device {self.device_id}: {target}")
        for pid in self._bundle_process_ids(str(record.get("url") or "")):
            self._terminate(pid)
        if self._last_launch_app_id == target:
            self._last_launch_pid = None
            self._last_launch_app_id = ""

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        """Return cached package information when the app is still installed."""
        if not self.is_installed(app_id):
            return None
        return self._app_cache.get(app_id)

    def host_is_running(self) -> bool:
        """Return whether a WebDriverAgent-style process is running."""
        for entry in self._processes():
            executable = str(entry.get("executable", "")).lower()
            if any(marker in executable for marker in _WDA_PROCESS_MARKERS):
                return True
        return False

    def capture_memgraph(
        self, output: Path | str, *, pid: int | None = None
    ) -> Path:
        """Capture an Xcode-compatible memory graph through the ``ios4`` CLI.

        CoreDevice exposes no memory-graph service, so this is the one
        operation that still needs the Rust ``ios4`` binary on the host.

        Args:
            output: Destination ``.memgraph`` file. An existing destination is
                replaced only after a complete non-empty snapshot is received.
            pid: Process id to capture. Defaults to :attr:`last_launch_pid`.

        Returns:
            Path: The resolved destination path.

        Raises:
            IOSDevice5Error: If ``ios4`` is missing, there is no PID, or the
                snapshot is empty.
            ValueError: If an explicit PID is not a positive integer.
        """
        binary = self._resolve_binary(ios4_binary())
        if binary is None:
            raise IOSDevice5Error(
                f"{_LOG_TAG} capture_memgraph needs the `{ios4_binary()}` CLI, "
                "which CoreDevice does not replace. Install it or set "
                "IDEVICE_IOS4_BINARY."
            )
        target_pid = self._last_launch_pid if pid is None else pid
        if target_pid is None:
            raise IOSDevice5Error(
                f"{_LOG_TAG} No PID available; launch the app or pass pid explicitly"
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
                [
                    binary,
                    "--udid",
                    self.device_id,
                    "memgraph",
                    str(target_pid),
                    str(temporary_path),
                ],
                check=False,
                timeout=_MEMGRAPH_TIMEOUT,
            )
            if (
                result.returncode != 0
                or not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise IOSDevice5Error(
                    f"{_LOG_TAG} Memory graph capture failed for PID {target_pid}: "
                    f"returncode={result.returncode}, stdout={result.stdout!r}, "
                    f"stderr={result.stderr!r}"
                )
            temporary_path.replace(output_path)
            return output_path
        finally:
            temporary_path.unlink(missing_ok=True)

    def _supports_capture_screenshot(self) -> bool:
        """Return whether this Xcode ships ``device capture``, added in Xcode 27.

        An unknown subcommand still exits ``0`` when ``--help`` is present, so
        the listing is inspected instead of the exit status.
        """
        if self._capture_screenshot_supported is None:
            result = self._runner.run(
                [self._xcrun, "devicectl", "device", "--help"],
                check=False,
                timeout=30,
            )
            self._capture_screenshot_supported = (
                re.search(r"(?m)^\s+capture\s", result.stdout) is not None
            )
            logger.debug(
                f"{_LOG_TAG} devicectl screenshot support: "
                f"{self._capture_screenshot_supported}"
            )
        return self._capture_screenshot_supported

    def screenshot(self, local: Path | str) -> bool:
        """Capture the screen through devicectl, falling back to ios4."""
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if (
                self._supports_capture_screenshot()
                and self._screenshot_via_devicectl(local_path)
            ):
                return True
        except CommandExecutionError as exc:
            logger.warning(f"{_LOG_TAG} devicectl screenshot failed: {exc}")
        return self._screenshot_via_ios4(local_path)

    def _screenshot_via_devicectl(self, local_path: Path) -> bool:
        """Capture through ``device capture screenshot``, which requires PNG."""
        destination = local_path
        temporary_path: Path | None = None
        if local_path.suffix.lower() != ".png":
            with tempfile.NamedTemporaryFile(
                prefix=f".{local_path.stem}-",
                suffix=".png",
                dir=local_path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
            destination = temporary_path
        try:
            outcome = self._run(
                self._command(
                    ["device", "capture", "screenshot"],
                    "--destination",
                    str(destination),
                ),
                timeout=60,
            )
            if not outcome.succeeded or not destination.is_file():
                logger.warning(
                    f"{_LOG_TAG} Screenshot failed on {self.device_id}: "
                    f"{outcome.error}"
                )
                return False
            if temporary_path is not None:
                temporary_path.replace(local_path)
            return True
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _screenshot_via_ios4(self, local_path: Path) -> bool:
        """Capture through the ios4 screenshot service when it is available."""
        configured_binary = ios4_binary()
        binary = self._resolve_binary(configured_binary)
        if binary is None:
            logger.debug(
                f"{_LOG_TAG} ios4 screenshot unavailable: "
                f"`{configured_binary}` CLI not found"
            )
            return False

        suffix = local_path.suffix or ".png"
        with tempfile.NamedTemporaryFile(
            prefix=f".{local_path.stem}-",
            suffix=suffix,
            dir=local_path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        try:
            try:
                result = self._runner.run(
                    [
                        binary,
                        "--udid",
                        self.device_id,
                        "screenshot",
                        str(temporary_path),
                    ],
                    check=False,
                    timeout=60,
                )
            except CommandExecutionError as exc:
                logger.warning(f"{_LOG_TAG} ios4 screenshot failed: {exc}")
                return False
            if (
                result.returncode != 0
                or not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                logger.warning(
                    f"{_LOG_TAG} ios4 screenshot failed on {self.device_id}: "
                    f"returncode={result.returncode}, stderr={result.stderr!r}"
                )
                return False
            temporary_path.replace(local_path)
            return True
        finally:
            temporary_path.unlink(missing_ok=True)

    def _container_arguments(self, app_id: str) -> list[str]:
        """Build the app-data-container domain selector for file commands."""
        return [
            "--domain-type",
            _APP_DATA_DOMAIN,
            "--domain-identifier",
            app_id,
        ]

    @staticmethod
    def _container_path(remote: str, *, documents_only: bool) -> str:
        """Return a container-relative path, optionally scoped to Documents."""
        if not remote or not isinstance(remote, str):
            raise ValueError("remote is required and must be a non-empty string")
        relative = remote.strip("/")
        if not relative:
            raise ValueError("remote is required and must be a non-empty string")
        if not documents_only:
            return relative
        if relative == _DOCUMENTS_ROOT or relative.startswith(f"{_DOCUMENTS_ROOT}/"):
            return relative
        return f"{_DOCUMENTS_ROOT}/{relative}"

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        """Copy a host file or directory into the app data container.

        Raises:
            ValueError: If ``remote`` is empty.
            FileNotFoundError: If ``local`` does not exist.
            IOSDevice5Error: If devicectl rejects the transfer.
        """
        local_path = Path(local)
        if not local_path.exists():
            raise FileNotFoundError(f"Local path not found: {local_path}")
        target = self._resolve_app_id(app_id)
        destination = self._container_path(remote, documents_only=documents_only)
        outcome = self._run(
            self._command(
                ["device", "copy", "to"],
                *self._container_arguments(target),
                "--source",
                str(local_path),
                "--destination",
                destination,
            ),
            timeout=_INSTALL_TIMEOUT,
        )
        self._require(outcome, f"push to {target}:{destination}")

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        """Copy a file or directory out of the app data container.

        Raises:
            ValueError: If ``remote`` is empty.
            IOSDevice5Error: If devicectl rejects the transfer.
        """
        target = self._resolve_app_id(app_id)
        source = self._container_path(remote, documents_only=documents_only)
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        outcome = self._run(
            self._command(
                ["device", "copy", "from"],
                *self._container_arguments(target),
                "--source",
                source,
                "--destination",
                str(local_path),
            ),
            timeout=_INSTALL_TIMEOUT,
        )
        self._require(outcome, f"pull from {target}:{source}")

    @staticmethod
    def _file_names(result: dict[str, Any]) -> list[str]:
        """Extract entry names from a ``device info files`` result section.

        Apple documents the JSON as stable but not its field names, so both the
        ``path`` and ``name`` spellings are accepted.
        """
        names: list[str] = []
        for entry in result.get("files", []):
            if isinstance(entry, str):
                names.append(entry)
                continue
            if not isinstance(entry, dict):
                continue
            value = entry.get("path") or entry.get("name") or entry.get("url")
            if isinstance(value, str) and value:
                names.append(value)
        return names

    def _list_container(
        self, app_id: str, remote: str, *, documents_only: bool, recursive: bool
    ) -> list[str] | None:
        """List a container subdirectory, or ``None`` when it cannot be read.

        An empty or ``.`` ``remote`` selects the domain root, which devicectl
        expresses by omitting ``--subdirectory``.
        """
        relative = remote.strip("/") if isinstance(remote, str) else ""
        if documents_only:
            subdirectory = self._container_path(
                relative or _DOCUMENTS_ROOT, documents_only=True
            )
        else:
            subdirectory = "" if relative in {"", "."} else relative
        selector = ["--subdirectory", subdirectory] if subdirectory else []
        outcome = self._run(
            self._command(
                ["device", "info", "files"],
                *self._container_arguments(app_id),
                *selector,
                "--recurse" if recursive else "--no-recurse",
            ),
            timeout=120,
        )
        if not outcome.succeeded:
            logger.debug(
                f"{_LOG_TAG} Listing {app_id}:{subdirectory or '/'} failed: "
                f"{outcome.error}"
            )
            return None
        return self._file_names(outcome.result)

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
        documents_only: bool = True,
    ) -> list[str]:
        """List entries under a path in an app data container.

        By default paths are relative to the app's Documents directory.
        Set ``documents_only`` to ``False`` to address the full container.
        ``/`` and ``.`` select the root of the chosen scope.

        Raises:
            ValueError: If ``remote`` is empty.
            IOSDevice5Error: If the directory cannot be listed.
        """
        target = self._resolve_app_id(app_id)
        if not remote or not isinstance(remote, str):
            raise ValueError("remote is required and must be a non-empty string")
        names = self._list_container(
            target, remote, documents_only=documents_only, recursive=recursive
        )
        if names is None:
            raise IOSDevice5Error(
                f"{_LOG_TAG} Could not list {target}:{remote} on {self.device_id}"
            )
        return names

    def documents_exists(self, app_id: str, remote: str) -> bool:
        """Check whether ``remote`` exists in an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        relative = PurePosixPath(self._container_path(remote, documents_only=True))
        names = self._list_container(
            app_id, str(relative.parent), documents_only=False, recursive=False
        )
        if names is None:
            return False
        return any(PurePosixPath(name).name == relative.name for name in names)

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entries under ``remote`` in an app's Documents sandbox.

        ``/`` and ``.`` select the Documents root.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote or not isinstance(remote, str):
            raise ValueError("remote is required and must be a non-empty string")
        names = self._list_container(
            app_id, remote, documents_only=True, recursive=False
        )
        if names is None:
            raise IOSDevice5Error(
                f"{_LOG_TAG} Could not list Documents/{remote} for {app_id}"
            )
        return names

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        try:
            self.pull(remote, local, app_id=app_id, documents_only=True)
        except IOSDevice5Error as exc:
            logger.warning(f"{_LOG_TAG} documents_pull failed: {exc}")
            return False
        return True

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        """Push a local file or directory into an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        try:
            self.push(local, remote, app_id=app_id, documents_only=True)
        except FileNotFoundError:
            logger.warning(f"{_LOG_TAG} documents_push source not found: {local}")
            return False
        except IOSDevice5Error as exc:
            logger.warning(f"{_LOG_TAG} documents_push failed: {exc}")
            return False
        return True

    def pull2(self, data_path: AppDataPath, remote: str, local: Path | str) -> bool:
        """Pull from the container root (Local) or Documents (Persistent)."""
        if not isinstance(data_path, AppDataPath):
            raise ValueError(f"Invalid data_path: {data_path!r}")
        try:
            self.pull(
                remote,
                local,
                documents_only=data_path is AppDataPath.Persistent,
            )
        except IOSDevice5Error as exc:
            logger.warning(f"{_LOG_TAG} pull2 failed: {exc}")
            return False
        return True

    def _unsupported(self, operation: str) -> NoReturn:
        raise NotImplementedError(
            f"{_LOG_TAG} {operation} has no CoreDevice service; use the ios4 "
            "backend for it"
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

    def documents_rm(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        self._unsupported("documents_rm")

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        del data_path, remote
        self._unsupported("delete2")
