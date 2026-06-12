"""Exceptions raised by ``DeviceBase`` implementations."""

from __future__ import annotations


class DeviceError(Exception):
    """Base exception for ``DeviceBase`` and platform device classes."""


class DeviceNotFoundError(DeviceError):
    """Raised when the specified device is not found."""


class AppNotInstalledError(DeviceError):
    """Raised when an operation requires an app that is not installed."""


class CommandExecutionError(DeviceError):
    """Raised when an underlying CLI command fails."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str] | None = None,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
