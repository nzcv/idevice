"""HTTP client for the iRecord control server running on the mac host.

The iRecord server exposes ``/api/{uuid}/...`` routes (see
``iRecord/Sources/irecord/HTTPServer.swift``) used to start, stop, and query
video-only screen recordings keyed by device UDID. It records USB-connected iOS
devices directly via CoreMediaIO, so there is no on-device runner or proxy.
"""

from __future__ import annotations

import logging

import requests

from idevice.record import config
from idevice.record.base.errors import RecordServerError

logger = logging.getLogger(__name__)

_LOG_TAG = "[IRecord]"

VALID_PRESETS = ("480p", "540p", "720p", "1080p", "2160p")


class IRecordClient:
    """Thin client for the iRecord control server on the mac host."""

    def __init__(self, ip: str, port: int, *, timeout: float | None = None) -> None:
        """Bind the client to an iRecord control server.

        Args:
            ip: iRecord server IP address. Required and non-empty.
            port: iRecord control-server port.
            timeout: Per-request timeout in seconds; defaults to
                :func:`idevice.record.config.http_timeout`.

        Raises:
            ValueError: If ``ip`` is empty.
        """
        if not ip:
            raise ValueError("iRecord ip is required and must be a non-empty string")
        self._ip = ip
        self._port = int(port)
        self._timeout = timeout if timeout is not None else config.http_timeout()
        self.base = f"http://{ip}:{port}"
        logger.info(f"{_LOG_TAG} IRecordClient initialized: {self.base}")

    def _request(
        self,
        method: str,
        route: str,
        *,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        url = f"{self.base}{route}"
        logger.info(f"{_LOG_TAG} {method} {url}")
        try:
            response = requests.request(
                method,
                url,
                params=params,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except requests.RequestException as exc:
            raise RecordServerError(f"{_LOG_TAG} request failed: {method} {url}: {exc}") from exc
        if not response.ok:
            body = response.text
            detail = f": {body}" if body else ""
            raise RecordServerError(
                f"{_LOG_TAG} {method} {url} returned {response.status_code}{detail}",
                status_code=response.status_code,
                body=body,
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise RecordServerError(
                f"{_LOG_TAG} {method} {url} returned non-JSON body",
                status_code=response.status_code,
                body=response.text,
            ) from exc
        return result if isinstance(result, dict) else {}

    def start(
        self,
        device_udid: str,
        *,
        timeout: float | str | None = None,
    ) -> dict:
        """Start a recording for ``device_udid`` (``GET /api/{udid}/start``).

        Args:
            device_udid: Target device UDID and recording key.
            timeout: Optional auto-stop duration. Accepts a duration string the
                server understands (e.g. ``2h``, ``30m``, ``90s``) or a number of
                seconds; ``None`` records until an explicit :meth:`stop`.

        Returns:
            dict: The recording status report.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        params: dict = {}
        if timeout is not None:
            params["timeout"] = str(timeout)
        return self._request("GET", f"/api/{device_udid}/start", params=params)

    def stop(
        self,
        device_udid: str,
        *,
        upload: bool = False,
        preset: str | None = None,
    ) -> dict:
        """Stop a recording for ``device_udid`` (``GET /api/{udid}/stop``).

        Args:
            device_udid: Target device UDID and recording key.
            upload: Whether the server should upload the finished file (requires
                the server to be configured with a presign URL).
            preset: Optional downscale preset; one of ``480p``, ``540p``,
                ``720p``, ``1080p``, ``2160p``.

        Returns:
            dict: The recording status report.

        Raises:
            ValueError: If ``preset`` is not a recognized preset.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        if preset is not None and preset not in VALID_PRESETS:
            raise ValueError(
                f"invalid preset {preset!r}; use one of {', '.join(VALID_PRESETS)}"
            )
        params: dict = {"upload": "true" if upload else "false"}
        if preset is not None:
            params["preset"] = preset
        return self._request("GET", f"/api/{device_udid}/stop", params=params)

    def status(self, device_udid: str) -> dict:
        """Status of one recording (``GET /api/{udid}/status``)."""
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        return self._request("GET", f"/api/{device_udid}/status")
