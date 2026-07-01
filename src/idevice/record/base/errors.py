"""Exceptions raised by ``RecordBase`` implementations and the iRecord client."""

from __future__ import annotations


class RecordError(Exception):
    """Base exception for the record client and its HTTP layer."""


class RecordNotSupportedError(RecordError):
    """Raised when a record feature is requested on an unsupported platform."""


class RecordServerError(RecordError):
    """Raised when an iRecord control-server request fails."""

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
