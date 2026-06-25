"""HTTP client for the EndlessKeeper control server running on the mac host.

The keeper exposes ``/api/runs`` routes (see ``EndlessKeeper/src/server.rs``) used
to launch, query, kill, and export xctest runs keyed by device UDID.
"""

from __future__ import annotations

import logging

import requests

from idevice.host import config
from idevice.host.base.errors import KeeperError

logger = logging.getLogger(__name__)

_LOG_TAG = "[Keeper]"


class Keeper:
    """Thin client for the EndlessKeeper control server on this machine."""

    def __init__(self, ip: str, port: int, *, timeout: float | None = None) -> None:
        """Bind the client to a keeper control server.

        Args:
            ip: Keeper host IP address. Required and non-empty.
            port: Keeper control-server port.
            timeout: Per-request timeout in seconds; defaults to
                :func:`idevice.host.config.http_timeout`.

        Raises:
            ValueError: If ``ip`` is empty.
        """
        if not ip:
            raise ValueError("keeper ip is required and must be a non-empty string")
        self._ip = ip
        self._port = int(port)
        self._timeout = timeout if timeout is not None else config.http_timeout()
        self.base = f"http://{ip}:{port}"

    def _request(
        self,
        method: str,
        route: str,
        *,
        json_body: dict | None = None,
        timeout: float | None = None,
    ) -> dict | list:
        url = f"{self.base}{route}"
        logger.info(f"{_LOG_TAG} {method} {url}")
        try:
            response = requests.request(
                method,
                url,
                json=json_body,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except requests.RequestException as exc:
            raise KeeperError(f"{_LOG_TAG} request failed: {method} {url}: {exc}") from exc
        if not response.ok:
            raise KeeperError(
                f"{_LOG_TAG} {method} {url} returned {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise KeeperError(
                f"{_LOG_TAG} {method} {url} returned non-JSON body",
                status_code=response.status_code,
                body=response.text,
            ) from exc

    def health(self) -> dict:
        """Liveness probe (``GET /api/health``)."""
        result = self._request("GET", "/api/health")
        return result if isinstance(result, dict) else {}

    def list_runs(self) -> list:
        """List every tracked run (``GET /api/runs``)."""
        result = self._request("GET", "/api/runs")
        return result if isinstance(result, list) else []

    def launch(self, device_udid: str, **overrides) -> dict:
        """Launch a run for ``device_udid`` (``POST /api/runs``).

        Args:
            device_udid: Target device UDID and run key.
            **overrides: Optional launch fields accepted by the keeper, e.g.
                ``server_port``, ``scheme``, ``app_bundle_id``, ``only_testing``,
                ``skip_build``, ``performance_diagnostics``, ``stack_logging``,
                ``destination``, ``derived_data``, ``result_bundle``.

        Returns:
            dict: The launched run record (echoes the device ``server_port``).
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        payload: dict = {"device_udid": device_udid}
        payload.update({k: v for k, v in overrides.items() if v is not None})
        result = self._request("POST", "/api/runs", json_body=payload)
        return result if isinstance(result, dict) else {}

    def status(self, device_udid: str) -> dict:
        """Status of one run (``GET /api/runs/{udid}``)."""
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        result = self._request("GET", f"/api/runs/{device_udid}")
        return result if isinstance(result, dict) else {}

    def kill(self, device_udid: str) -> dict:
        """Kill a run's child process (``DELETE /api/runs/{udid}``)."""
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        result = self._request("DELETE", f"/api/runs/{device_udid}")
        return result if isinstance(result, dict) else {}

    def export(self, device_udid: str, presigned_url: str, content_type: str | None = None) -> dict:
        """Export memgraphs to a presigned URL (``POST /api/runs/{udid}/export``).

        Args:
            device_udid: Target device UDID / run key.
            presigned_url: S3 presigned PUT URL the archive is uploaded to.
            content_type: Optional content type the URL was signed for.

        Returns:
            dict: The export summary.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        if not presigned_url:
            raise ValueError("presigned_url is required and must be a non-empty string")
        payload: dict = {"presigned_url": presigned_url}
        if content_type:
            payload["content_type"] = content_type
        result = self._request(
            "POST",
            f"/api/runs/{device_udid}/export",
            json_body=payload,
            timeout=max(self._timeout, 600.0),
        )
        return result if isinstance(result, dict) else {}
