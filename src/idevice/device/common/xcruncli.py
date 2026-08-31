"""Shared, device-agnostic wrapper around Apple's ``xcrun devicectl`` CLI."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from idevice.device.base.errors import (
    AppNotInstalledError,
    CommandExecutionError,
    DeviceNotFoundError,
)
from idevice.device.base.runner import SubprocessRunner
from idevice.device.config import xcrun_binary

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice5]"
_APP_DATA_DOMAIN = "appDataContainer"
_WIRED_TRANSPORT = "wired"
_DOCUMENTS_ROOT = "Documents"
_DEFAULT_TIMEOUT = 120
_INSTALL_TIMEOUT = 3600


class XcrunCLIError(RuntimeError):
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


class XcrunCLI:
    """Low-level wrapper for operations exposed by CoreDevice.

    This class owns only CLI concerns: command construction, subprocess
    execution, JSON parsing, and devicectl-backed state. Device policy and
    ios4 fallback routing live in :class:`~idevice.device.ios5.device.IOSDevice5`,
    which composes this wrapper.

    * ``device install app`` / ``device uninstall app`` for lifecycle.
    * ``device info apps`` for exact bundle-id checks.
    * ``device process launch`` for launch environment and ordered arguments.
    * ``device info processes`` and ``device process terminate`` for teardown.
    * ``device copy to`` / ``device copy from`` and ``device info files`` for
      the app data container, including the Documents sandbox.

    It deliberately does not inherit ``DeviceBase`` so the command adapter can
    be reused independently of the public device hierarchy.
    """

    def __init__(
        self,
        device_id: str,
        *,
        binary: str | None = None,
        runner: SubprocessRunner | None = None,
        package_name: str = "",
    ) -> None:
        if not device_id or not isinstance(device_id, str):
            raise ValueError("device_id is required and must be a non-empty string")
        self.device_id = device_id
        self.package_name = package_name
        self.binary = binary or xcrun_binary()
        self.runner = runner or SubprocessRunner()
        self.last_launch_pid: int | None = None
        self.last_launch_app_id = ""
        self._capture_screenshot_supported: bool | None = None

    @classmethod
    def default_udid(
        cls,
        *,
        binary: str | None = None,
        runner: SubprocessRunner | None = None,
    ) -> str:
        """Return the UDID of the first USB-attached device, in listing order.

        A farm host remembers every device it ever paired, and CoreDevice keeps
        reaching most of them over Wi-Fi (``localNetwork``) or not at all. Only
        a cabled device reports the ``wired`` transport, which makes it the one
        unambiguous answer to "the device on this host".

        Raises:
            DeviceNotFoundError: If no USB-attached device reports a UDID.
        """
        outcome = _run_devicectl(
            binary or xcrun_binary(),
            runner or SubprocessRunner(),
            ["list", "devices"],
            timeout=30,
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

    def _resolve_app_id(self, app_id: str | None) -> str:
        """Return an explicit app id or the default bound to this wrapper."""
        target = app_id or self.package_name
        if not target:
            raise ValueError("app_id is required and must be a non-empty string")
        return target

    @staticmethod
    def resolve_binary(binary: str) -> str | None:
        """Return the usable path for ``binary``, or ``None`` when missing."""
        resolved = shutil.which(binary)
        if resolved is not None:
            return resolved
        return binary if Path(binary).is_file() else None

    def command(self, subcommand: list[str], *arguments: str) -> list[str]:
        """Build a devicectl argument list bound to this device."""
        return [*subcommand, "--device", self.device_id, *arguments]

    def run(
        self, arguments: list[str], *, timeout: int = _DEFAULT_TIMEOUT
    ) -> DevicectlOutcome:
        """Run one devicectl command for this device."""
        return _run_devicectl(
            self.binary, self.runner, arguments, timeout=timeout
        )

    def require(
        self, outcome: DevicectlOutcome, action: str
    ) -> dict[str, Any]:
        """Return the result section, raising when devicectl reported failure."""
        if not outcome.succeeded:
            raise XcrunCLIError(
                f"{_LOG_TAG} {action} failed on {self.device_id}: {outcome.error}"
            )
        return outcome.result

    @staticmethod
    def encode_environment(environment: dict[str, str]) -> str:
        """Encode launch environment variables as devicectl's JSON dictionary."""
        for key, value in environment.items():
            if not key or not isinstance(key, str):
                raise ValueError("environment names must be non-empty strings")
            if not isinstance(value, str):
                raise TypeError(f"environment value for {key!r} must be a string")
        return json.dumps(environment, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def validate_launch_arguments(arguments: list[str]) -> list[str]:
        """Return ``arguments`` unchanged after rejecting unusable entries."""
        for index, argument in enumerate(arguments):
            if not isinstance(argument, str):
                raise TypeError(f"launch argument at index {index} must be a string")
            if not argument:
                raise ValueError(f"launch argument at index {index} cannot be empty")
        return list(arguments)

    def install(self, package_path: Path) -> dict[str, Any]:
        """Install an IPA or ``.app`` directory with ``device install app``.

        Args:
            package_path: IPA file or app directory to install.

        Returns:
            dict[str, Any]: The first installed-application record reported by
                devicectl, or an empty dictionary when it reports none.

        Raises:
            FileNotFoundError: If ``package_path`` does not exist.
        """
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")

        logger.info(
            f"{_LOG_TAG} Installing package on {self.device_id}: {package_path}"
        )
        outcome = self.run(
            self.command(["device", "install", "app"], str(package_path)),
            timeout=_INSTALL_TIMEOUT,
        )
        if not outcome.succeeded:
            raise XcrunCLIError(
                f"{_LOG_TAG} Installation failed on {self.device_id}: "
                f"{outcome.error}, stdout={outcome.stdout!r}, "
                f"stderr={outcome.stderr!r}"
            )

        installed = outcome.result.get("installedApplications") or []
        record = installed[0] if installed and isinstance(installed[0], dict) else {}
        return record

    def uninstall(self, app_id: str) -> None:
        """Uninstall an application with ``device uninstall app``."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        logger.info(f"{_LOG_TAG} Uninstalling {app_id} on {self.device_id}")
        outcome = self.run(self.command(["device", "uninstall", "app"], app_id))
        self.require(outcome, f"uninstall of {app_id}")

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
            XcrunCLIError: If ``strict`` and devicectl could not list apps.
        """
        outcome = self.run(
            self.command(
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
            raise XcrunCLIError(
                f"{_LOG_TAG} App listing failed on {self.device_id}: "
                f"{outcome.error}"
            )
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

    def _launch_process(
        self,
        app_id: str | None,
        *,
        args: list[str] | None,
        environment: dict[str, str] | None,
        terminate_existing: bool,
        activate: bool,
    ) -> tuple[str, int]:
        """Launch an app and return its resolved bundle id and process id."""
        target = self._resolve_app_id(app_id)
        options: list[str] = ["--activate" if activate else "--no-activate"]
        if terminate_existing:
            options.append("--terminate-existing")
        if environment:
            options.extend(
                ["--environment-variables", self.encode_environment(environment)]
            )
        launch_arguments = self.validate_launch_arguments(args or [])

        logger.info(f"{_LOG_TAG} Launching {target} on {self.device_id}")
        outcome = self.run(
            self.command(
                ["device", "process", "launch"],
                *options,
                "--",
                target,
                *launch_arguments,
            )
        )
        result = self.require(outcome, f"launch of {target}")
        process = result.get("process")
        pid = process.get("processIdentifier") if isinstance(process, dict) else None
        if not isinstance(pid, int):
            raise XcrunCLIError(
                f"{_LOG_TAG} Launch of {target} returned no PID: {result!r}"
            )
        logger.info(f"{_LOG_TAG} Launched {target} on {self.device_id} with PID {pid}")
        return target, pid

    def launch(
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
            XcrunCLIError: If the device is unreachable, the launch fails, or
                no PID comes back.
        """
        target, pid = self._launch_process(
            app_id,
            args=args,
            environment=environment,
            terminate_existing=terminate_existing,
            activate=activate,
        )
        if not app_id:
            self.last_launch_pid = pid
            self.last_launch_app_id = target

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
            XcrunCLIError: If the device is unreachable, the launch fails, or
                no PID comes back.
        """
        target = self._resolve_app_id(app_id)
        if self._app_record(target, strict=True) is None:
            raise AppNotInstalledError(f"App not installed: {target}")
        target, pid = self._launch_process(
            target,
            args=args,
            environment=environment,
            terminate_existing=terminate_existing,
            activate=activate,
        )
        self.last_launch_pid = pid
        self.last_launch_app_id = target

    def _processes(self) -> list[dict[str, Any]]:
        """Return the device process table.

        Raises:
            XcrunCLIError: If devicectl cannot read the process table.
        """
        outcome = self.run(self.command(["device", "info", "processes"]))
        if not outcome.succeeded:
            raise XcrunCLIError(
                f"{_LOG_TAG} Process listing failed on {self.device_id}: "
                f"{outcome.error}"
            )
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
        outcome = self.run(
            self.command(["device", "process", "terminate"], *arguments), timeout=60
        )
        self.require(outcome, f"terminate PID {pid}")
        return True

    def stop_app(self, app_id: str | None = None) -> None:
        """Kill every process of the app, whoever launched it.

        A stopped app is not an error; a missing app is.

        Raises:
            AppNotInstalledError: If the resolved bundle id is not installed.
            XcrunCLIError: If the device is unreachable.
        """
        target = self._resolve_app_id(app_id)
        record = self._app_record(target, strict=True)
        if record is None:
            raise AppNotInstalledError(f"App not installed: {target}")

        logger.info(f"{_LOG_TAG} Stopping app on iOS device {self.device_id}: {target}")
        for pid in self._bundle_process_ids(str(record.get("url") or "")):
            self._terminate(pid)
        if self.last_launch_app_id == target:
            self.last_launch_pid = None
            self.last_launch_app_id = ""

    def _supports_capture_screenshot(self) -> bool:
        """Return whether this Xcode ships ``device capture``, added in Xcode 27.

        An unknown subcommand still exits ``0`` when ``--help`` is present, so
        the listing is inspected instead of the exit status.
        """
        if self._capture_screenshot_supported is None:
            result = self.runner.run(
                [self.binary, "devicectl", "device", "--help"],
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
        """Capture the screen through devicectl when CoreDevice supports it."""
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not self._supports_capture_screenshot():
                return False
            return self._screenshot_via_devicectl(local_path)
        except CommandExecutionError as exc:
            logger.warning(f"{_LOG_TAG} devicectl screenshot failed: {exc}")
            return False

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
            outcome = self.run(
                self.command(
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
        remove_existing_content: bool = False,
    ) -> None:
        """Copy a host file or directory into the app data container.

        Args:
            local: Path to the local file or directory.
            remote: Destination path relative to the selected container scope.
            app_id: Bundle id, defaulting to the id bound to this device.
            documents_only: Scope ``remote`` to the app's Documents directory.
            remove_existing_content: Remove the destination directory's
                existing contents before copying ``local``. CoreDevice only
                applies this option when ``local`` is a directory.

        Raises:
            ValueError: If ``remote`` is empty.
            FileNotFoundError: If ``local`` does not exist.
            XcrunCLIError: If devicectl rejects the transfer.
        """
        local_path = Path(local)
        if not local_path.exists():
            raise FileNotFoundError(f"Local path not found: {local_path}")
        target = self._resolve_app_id(app_id)
        destination = self._container_path(remote, documents_only=documents_only)
        replacement = (
            ["--remove-existing-content", "true"]
            if remove_existing_content
            else []
        )
        outcome = self.run(
            self.command(
                ["device", "copy", "to"],
                *self._container_arguments(target),
                "--source",
                str(local_path),
                "--destination",
                destination,
                *replacement,
            ),
            timeout=_INSTALL_TIMEOUT,
        )
        self.require(outcome, f"push to {target}:{destination}")

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
            XcrunCLIError: If devicectl rejects the transfer.
        """
        target = self._resolve_app_id(app_id)
        source = self._container_path(remote, documents_only=documents_only)
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        outcome = self.run(
            self.command(
                ["device", "copy", "from"],
                *self._container_arguments(target),
                "--source",
                source,
                "--destination",
                str(local_path),
            ),
            timeout=_INSTALL_TIMEOUT,
        )
        self.require(outcome, f"pull from {target}:{source}")

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
    ) -> list[str]:
        """List a container subdirectory.

        An empty or ``.`` ``remote`` selects the domain root, which devicectl
        expresses by omitting ``--subdirectory``.

        Raises:
            XcrunCLIError: If devicectl cannot read the directory.
        """
        relative = remote.strip("/") if isinstance(remote, str) else ""
        if documents_only:
            subdirectory = self._container_path(
                relative or _DOCUMENTS_ROOT, documents_only=True
            )
        else:
            subdirectory = "" if relative in {"", "."} else relative
        selector = ["--subdirectory", subdirectory] if subdirectory else []
        outcome = self.run(
            self.command(
                ["device", "info", "files"],
                *self._container_arguments(app_id),
                *selector,
                "--recurse" if recursive else "--no-recurse",
            ),
            timeout=120,
        )
        if not outcome.succeeded:
            raise XcrunCLIError(
                f"{_LOG_TAG} Could not list {app_id}:"
                f"{subdirectory or '/'} on {self.device_id}: {outcome.error}"
            )
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
            XcrunCLIError: If the directory cannot be listed.
        """
        target = self._resolve_app_id(app_id)
        if not remote or not isinstance(remote, str):
            raise ValueError("remote is required and must be a non-empty string")
        names = self._list_container(
            target, remote, documents_only=documents_only, recursive=recursive
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
        return names

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        try:
            self.pull(remote, local, app_id=app_id, documents_only=True)
        except XcrunCLIError as exc:
            logger.warning(f"{_LOG_TAG} documents_pull failed: {exc}")
            return False
        return True

    def documents_push(
        self,
        app_id: str,
        local: Path | str,
        remote: str,
        *,
        remove_existing_content: bool = False,
    ) -> bool:
        """Push a local file or directory into an app's Documents sandbox.

        Set ``remove_existing_content`` when copying a directory to replace
        the destination directory's contents instead of merging into it.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        try:
            self.push(
                local,
                remote,
                app_id=app_id,
                documents_only=True,
                remove_existing_content=remove_existing_content,
            )
        except FileNotFoundError:
            logger.warning(f"{_LOG_TAG} documents_push source not found: {local}")
            return False
        except XcrunCLIError as exc:
            logger.warning(f"{_LOG_TAG} documents_push failed: {exc}")
            return False
        return True


IOSDevice5Error = XcrunCLIError

__all__ = [
    "DevicectlOutcome",
    "IOSDevice5Error",
    "XcrunCLI",
    "XcrunCLIError",
]
