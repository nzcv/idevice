"""Shared, device-agnostic wrapper around the Rust ``ios4`` CLI."""

from __future__ import annotations

import json
import logging
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, NoReturn

from idevice.device.base.device import AppDataPath
from idevice.device.base.errors import (
    AppNotInstalledError,
    CommandExecutionError,
    DeviceNotFoundError,
)
from idevice.device.base.runner import CommandResult, SubprocessRunner
from idevice.device.config import ideviceinstaller_binary, ios4_binary

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOS4CLI]"
_INSTALL_SUCCESS_MARKERS = (
    "install success",
    "install: complete",
    "install - complete",
)
_PID_PATTERN = re.compile(r"(?m)^PID:\s*(\d+)\s*$")
_UDID_PATTERN = re.compile(
    r'UniqueDeviceID["\']?\s*:\s*String\(\s*"([^"\\]+)"\s*,?\s*\)'
)
_DOCUMENTS_ROOT = "/Documents"
_DOCUMENTS_DIR_IFMT = "S_IFDIR"
_DOCUMENTS_FILE_IFMT = "S_IFREG"
_MEMGRAPH_TIMEOUT = 600


class IOS4CLIError(RuntimeError):
    """Raised when an ios4 CLI operation cannot be completed."""


def parse_afc_envelope(stdout: str) -> dict[str, Any] | None:
    """Parse one ``ios4 afc`` JSON envelope.

    Args:
        stdout: Standard output from an ``afc`` subcommand. Success and
            failure both write one compact JSON object and a newline.

    Returns:
        dict[str, Any] | None: The envelope when ``stdout`` is a JSON object,
        otherwise ``None``.
    """
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _envelope_error_message(payload: dict[str, Any] | None) -> str:
    """Return ``error.message`` when the envelope carries one."""
    if payload is None:
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    message = error.get("message")
    if isinstance(message, str):
        return message
    return ""


def afc_failure_detail(result: CommandResult) -> str:
    """Return the afc failure text for logs and exceptions.

    Prefers ``error.message`` from the JSON envelope. When stdout is not that
    envelope, falls back to stderr. The CLI reports only on stderr when it
    cannot write the JSON response.

    Args:
        result: Completed ``ios4 afc`` command.

    Returns:
        str: Diagnostic text, possibly empty.
    """
    message = _envelope_error_message(parse_afc_envelope(result.stdout))
    if message:
        return message
    return result.stderr.strip()


def afc_info_ifmt(result: CommandResult) -> str | None:
    """Return ``st_ifmt`` from a successful ``afc info`` response.

    A non-zero exit or ``ok: false`` means the path is missing. A successful
    envelope without ``st_ifmt`` is treated as a regular file.

    Args:
        result: Completed ``ios4 afc info`` command.

    Returns:
        str | None: The file type, or ``None`` when the path is absent.
    """
    if result.returncode != 0:
        return None
    payload = parse_afc_envelope(result.stdout)
    if payload is None or payload.get("ok") is not True:
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        ifmt = data.get("st_ifmt")
        if isinstance(ifmt, str) and ifmt:
            return ifmt
    return _DOCUMENTS_FILE_IFMT


def afc_list_entries(stdout: str) -> list[str]:
    """Return names from a successful ``afc list`` envelope.

    ``.`` and ``..`` are dropped. Entry strings are already decoded by JSON.

    Args:
        stdout: Standard output from ``ios4 afc list``.

    Returns:
        list[str]: Entry names under the listed directory.

    Raises:
        IOS4CLIError: If ``stdout`` is not a successful list envelope.
    """
    payload = parse_afc_envelope(stdout)
    if payload is None or payload.get("ok") is not True:
        detail = _envelope_error_message(payload)
        suffix = f": {detail}" if detail else ""
        raise IOS4CLIError(
            f"{_LOG_TAG} afc list did not return a successful JSON envelope"
            f"{suffix}"
        )
    data = payload.get("data")
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise IOS4CLIError(
            f"{_LOG_TAG} afc list JSON envelope has no entries array"
        )
    return [
        name
        for name in entries
        if isinstance(name, str) and name not in (".", "..")
    ]


class IOS4CLI:
    """Low-level ios4 CLI implementation shared by IOSDevice4 and IOSDevice5.

    This class owns only CLI concerns: command construction, subprocess
    execution, and output parsing. Launch-PID tracking lives on the device
    facades that compose this wrapper.
    """

    def __init__(
        self,
        device_id: str,
        *,
        binary: str | None = None,
        runner: SubprocessRunner | None = None,
    ) -> None:
        if not device_id or not isinstance(device_id, str):
            raise ValueError("device_id is required and must be a non-empty string")
        self.device_id = device_id
        self.binary = binary or ios4_binary()
        self.runner = runner or SubprocessRunner()

    @staticmethod
    def resolve_binary(binary: str) -> str | None:
        """Return the usable path for ``binary``, or ``None`` when missing."""
        resolved = shutil.which(binary)
        if resolved is not None:
            return resolved
        return binary if Path(binary).is_file() else None

    @classmethod
    def default_udid(
        cls,
        *,
        binary: str | None = None,
        runner: SubprocessRunner | None = None,
    ) -> str:
        """Return the first connected device UDID reported by ios4."""
        result = (runner or SubprocessRunner()).run(
            [binary or ios4_binary(), "ideviceinfo"]
        )
        match = _UDID_PATTERN.search(result.stdout)
        if match is None:
            raise DeviceNotFoundError(
                f"{_LOG_TAG} Could not read UniqueDeviceID from ideviceinfo output"
            )
        return match.group(1)

    def command(self, *arguments: str) -> list[str]:
        """Build an ios4 command bound to this device."""
        return [self.binary, "--udid", self.device_id, *arguments]

    def run(
        self,
        *arguments: str,
        check: bool = True,
        timeout: int | None = None,
    ) -> CommandResult:
        """Run an ios4 subcommand bound to this device."""
        command = self.command(*arguments)
        if timeout is not None:
            return self.runner.run(command, check=check, timeout=timeout)
        if not check:
            return self.runner.run(command, check=False)
        return self.runner.run(command)

    def install_command(self, package_path: Path) -> list[str]:
        """Build the install command, preferring ideviceinstaller."""
        standalone = self.resolve_binary(ideviceinstaller_binary())
        if standalone is not None:
            return [
                standalone,
                "--udid",
                self.device_id,
                "install",
                str(package_path),
            ]
        return self.command("ideviceinstaller", "install", str(package_path))

    @staticmethod
    def bundle_id_in_application_listing(output: str, app_id: str) -> bool:
        """Return whether a listing row starts with the exact bundle id."""
        for line in output.splitlines():
            columns = line.strip().split()
            if columns and columns[0] == app_id:
                return True
        return False

    @staticmethod
    def encode_environment(environment: dict[str, str]) -> str:
        """Encode environment variables for ``process_control --env``."""
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
    def encode_launch_arguments(arguments: list[str]) -> str:
        """Encode ordered arguments for ``process_control --args``."""
        encoded: list[str] = []
        for index, argument in enumerate(arguments):
            if not isinstance(argument, str):
                raise TypeError(f"launch argument at index {index} must be a string")
            if not argument:
                raise ValueError(f"launch argument at index {index} cannot be empty")
            encoded.append(argument.replace("\\", "\\\\").replace(",", "\\,"))
        return ",".join(encoded)

    def install(self, package_path: Path) -> bool:
        """Install a package and return whether ios4 reported success."""
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")
        result = self.runner.run(
            self.install_command(package_path), check=False, timeout=3600
        )
        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        succeeded = result.returncode == 0 and any(
            marker in combined_output for marker in _INSTALL_SUCCESS_MARKERS
        )
        return succeeded

    def uninstall(self, app_id: str) -> None:
        """Uninstall an application through ios4's app service."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        self.run("app_service", "uninstall", app_id)

    def is_installed(self, app_id: str) -> bool:
        """Check an exact bundle id using ``application_listing``."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        result = self.run("application_listing", check=False)
        return result.returncode == 0 and self.bundle_id_in_application_listing(
            result.stdout, app_id
        )

    def launch_app(
        self,
        app_id: str,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> int:
        """Launch an app directly through ``process_control``.

        Returns:
            int: The process id reported by ``process_control``.
        """
        if not self.is_installed(app_id):
            raise AppNotInstalledError(f"App not installed: {app_id}")
        command = self.command("process_control")
        if environment:
            command.extend(["--env", self.encode_environment(environment)])
        if args:
            command.extend(["--args", self.encode_launch_arguments(args)])
        command.append(app_id)
        result = self.runner.run(command)
        match = _PID_PATTERN.search(f"{result.stdout}\n{result.stderr}")
        if match is None:
            raise IOS4CLIError(
                f"{_LOG_TAG} process_control did not return a PID for {app_id}: "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        return int(match.group(1))

    def launch(self, app_id: str) -> None:
        """Launch an app without launch arguments."""
        self.run("process_control", app_id)

    def stop_app(self, app_id: str) -> None:
        """Stop an app using ``pkill --bundle``."""
        result = self.run("pkill", "--bundle", app_id, check=False)
        if result.returncode != 0:
            raise IOS4CLIError(
                f"{_LOG_TAG} Failed to stop {app_id} on {self.device_id}: "
                f"returncode={result.returncode}, stdout={result.stdout!r}, "
                f"stderr={result.stderr!r}"
            )

    def screenshot(self, local: Path | str) -> bool:
        """Capture atomically through the ios4 screenshot service."""
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
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
                result = self.run(
                    "screenshot", str(temporary_path), check=False, timeout=60
                )
            except CommandExecutionError as exc:
                logger.warning(f"{_LOG_TAG} screenshot failed: {exc}")
                return False
            if (
                result.returncode != 0
                or not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                return False
            temporary_path.replace(local_path)
            return True
        finally:
            temporary_path.unlink(missing_ok=True)

    def capture_memgraph(
        self, output: Path | str, *, pid: int | None = None
    ) -> Path:
        """Capture a memory graph through ios4's DVT service."""
        target_pid = pid
        if target_pid is None:
            raise IOS4CLIError(
                f"{_LOG_TAG} No PID available; launch the app or pass pid explicitly"
            )
        if (
            isinstance(target_pid, bool)
            or not isinstance(target_pid, int)
            or target_pid <= 0
        ):
            raise ValueError("pid must be a positive integer")
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}-",
            suffix=".memgraph",
            dir=output_path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        try:
            result = self.run(
                "memgraph",
                str(target_pid),
                str(temporary_path),
                check=False,
                timeout=_MEMGRAPH_TIMEOUT,
            )
            if (
                result.returncode != 0
                or not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise IOS4CLIError(
                    f"{_LOG_TAG} Memory graph capture failed for PID "
                    f"{target_pid}: returncode={result.returncode}, "
                    f"stdout={result.stdout!r}, stderr={result.stderr!r}"
                )
            temporary_path.replace(output_path)
            return output_path
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def require_app_and_remote(app_id: str, remote: str) -> None:
        """Validate arguments shared by Documents helpers."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")

    @staticmethod
    def documents_path(remote: str) -> str:
        """Resolve ``remote`` underneath the vended Documents directory."""
        relative = remote.strip().replace("\\", "/").lstrip("/")
        parts = [part for part in relative.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError(f"remote path must not contain '..': {remote}")
        return posixpath.join(_DOCUMENTS_ROOT, *parts)

    def documents_command(self, app_id: str, *arguments: str) -> list[str]:
        """Build an ``afc --documents`` command."""
        return self.command("afc", "--documents", app_id, *arguments)

    def run_documents(self, app_id: str, *arguments: str) -> CommandResult:
        """Run an AFC Documents command without raising on failure."""
        return self.runner.run(
            self.documents_command(app_id, *arguments), check=False
        )

    def documents_stat(self, app_id: str, remote: str) -> str | None:
        """Return the remote path's file type, or ``None`` if missing."""
        return afc_info_ifmt(self.run_documents(app_id, "info", remote))

    def documents_mkdir(self, app_id: str, remote: str) -> bool:
        return self.run_documents(app_id, "mkdir", remote).returncode == 0

    def documents_upload(self, app_id: str, local: Path, remote: str) -> bool:
        parent = posixpath.dirname(remote)
        if parent and not self.documents_mkdir(app_id, parent):
            return False
        return (
            self.run_documents(app_id, "upload", str(local), remote).returncode
            == 0
        )

    def documents_download(self, app_id: str, remote: str, local: Path) -> bool:
        local.parent.mkdir(parents=True, exist_ok=True)
        result = self.run_documents(app_id, "download", remote, str(local))
        if result.returncode != 0 or not local.is_file():
            detail = afc_failure_detail(result)
            logger.error(
                f"{_LOG_TAG} Failed to pull {self.device_id}:{remote} to {local}: "
                f"returncode={result.returncode}, local_exists={local.is_file()}"
                + (f", {detail}" if detail else "")
            )
            return False
        return True

    def documents_push_dir(self, app_id: str, local: Path, remote: str) -> bool:
        if not self.documents_mkdir(app_id, remote):
            return False
        succeeded = True
        for entry in sorted(local.iterdir()):
            child = posixpath.join(remote, entry.name)
            if entry.is_dir():
                succeeded &= self.documents_push_dir(app_id, entry, child)
            else:
                succeeded &= self.documents_upload(app_id, entry, child)
        return succeeded

    def documents_pull_dir(self, app_id: str, remote: str, local: Path) -> bool:
        local.mkdir(parents=True, exist_ok=True)
        listing = self.run_documents(app_id, "list", remote)
        if listing.returncode != 0:
            return False
        succeeded = True
        for name in afc_list_entries(listing.stdout):
            child = posixpath.join(remote, name)
            if self.documents_stat(app_id, child) == _DOCUMENTS_DIR_IFMT:
                succeeded &= self.documents_pull_dir(app_id, child, local / name)
            else:
                succeeded &= self.documents_download(app_id, child, local / name)
        return succeeded

    def documents_exists(self, app_id: str, remote: str) -> bool:
        self.require_app_and_remote(app_id, remote)
        return self.documents_stat(app_id, self.documents_path(remote)) is not None

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        self.require_app_and_remote(app_id, remote)
        path = self.documents_path(remote)
        ifmt = self.documents_stat(app_id, path)
        if ifmt is None:
            raise FileNotFoundError(f"Remote path not found: {self.device_id}:{path}")
        if ifmt != _DOCUMENTS_DIR_IFMT:
            return [posixpath.basename(path)]
        result = self.run_documents(app_id, "list", path)
        if result.returncode != 0:
            raise IOS4CLIError(
                f"{_LOG_TAG} Failed to list {self.device_id}:{path}: "
                f"{afc_failure_detail(result)}"
            )
        return afc_list_entries(result.stdout)

    def documents_pull(
        self, app_id: str, remote: str, local: Path | str
    ) -> bool:
        self.require_app_and_remote(app_id, remote)
        path = self.documents_path(remote)
        ifmt = self.documents_stat(app_id, path)
        if ifmt is None:
            return False
        local_path = Path(local)
        if local_path.is_dir():
            local_path = local_path / posixpath.basename(path)
        if ifmt == _DOCUMENTS_DIR_IFMT:
            return self.documents_pull_dir(app_id, path, local_path)
        return self.documents_download(app_id, path, local_path)

    def documents_push(
        self, app_id: str, local: Path | str, remote: str
    ) -> bool:
        self.require_app_and_remote(app_id, remote)
        local_path = Path(local)
        if not local_path.exists():
            return False
        path = self.documents_path(remote)
        if self.documents_stat(app_id, path) == _DOCUMENTS_DIR_IFMT:
            path = posixpath.join(path, local_path.name)
        if local_path.is_dir():
            return self.documents_push_dir(app_id, local_path, path)
        return self.documents_upload(app_id, local_path, path)

    def documents_rm(self, app_id: str, remote: str) -> bool:
        self.require_app_and_remote(app_id, remote)
        path = self.documents_path(remote)
        try:
            ifmt = self.documents_stat(app_id, path)
            if ifmt is None:
                return False
            subcommand = "remove_all" if ifmt == _DOCUMENTS_DIR_IFMT else "remove"
            return self.run_documents(app_id, subcommand, path).returncode == 0
        except CommandExecutionError as exc:
            logger.warning(f"{_LOG_TAG} documents_rm failed: {exc}")
            return False

    def _unsupported(self, operation: str) -> NoReturn:
        raise NotImplementedError(
            f"{_LOG_TAG} {operation} is not supported by the ios4 CLI layer"
        )

    def push(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._unsupported("push")

    def pull(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._unsupported("pull")

    def ls(self, *args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        self._unsupported("ls")

    def pull2(
        self, data_path: AppDataPath, remote: str, local: Path | str
    ) -> bool:
        del data_path, remote, local
        self._unsupported("pull2")

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        del data_path, remote
        self._unsupported("delete2")


__all__ = [
    "IOS4CLI",
    "IOS4CLIError",
    "afc_failure_detail",
    "afc_info_ifmt",
    "afc_list_entries",
    "parse_afc_envelope",
]
