"""Exceptions raised by ``HostBase`` implementations and their HTTP clients."""

from __future__ import annotations


class HostError(Exception):
    """Base exception for the host and its clients."""


class HostNotSupportedError(HostError):
    """Raised when a host feature is requested on an unsupported platform."""


class HostTimeoutError(HostError):
    """Raised when a host operation does not complete within its deadline."""


class KeeperError(HostError):
    """Raised when an ikeeper control-server request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class RunnerError(HostError):
    """Raised when an on-device runner request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
