"""HTTP client for the on-device RemoteControlTest runner.

The runner embeds a small HTTP server on the iOS device (see
``RemoteControlTest/README.md``). The host talks to it directly at
``http://{device-ip}:{port}`` for app control, screenshots, and measurement.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from idevice.host import config
from idevice.host.base.errors import RunnerError

logger = logging.getLogger(__name__)

_LOG_TAG = "[Runner]"


class Runner:
    """Thin client for the runner's embedded HTTP server on the iOS device."""

    def __init__(self, ip: str, port: int, *, timeout: float | None = None) -> None:
        """Bind the client to an on-device runner server.

        Args:
            ip: Device IP address. Required and non-empty.
            port: Runner server port.
            timeout: Per-request timeout in seconds; defaults to
                :func:`idevice.host.config.http_timeout`.

        Raises:
            ValueError: If ``ip`` is empty.
        """
        if not ip:
            raise ValueError("device ip is required and must be a non-empty string")
        self._ip = ip
        self._port = int(port)
        self._timeout = timeout if timeout is not None else config.http_timeout()
        self.base = f"http://{ip}:{port}"

    def _get(self, route: str, *, params: dict | None = None, timeout: float | None = None) -> requests.Response:
        url = f"{self.base}{route}"
        logger.info(f"{_LOG_TAG} GET {url}")
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except requests.RequestException as exc:
            raise RunnerError(f"{_LOG_TAG} request failed: GET {url}: {exc}") from exc
        if not response.ok:
            raise RunnerError(
                f"{_LOG_TAG} GET {url} returned {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        return response

    @staticmethod
    def _json(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            return {"raw": response.text}
        return payload if isinstance(payload, dict) else {"data": payload}

    def health(self) -> dict:
        """Liveness probe (``GET /api/health``)."""
        return self._json(self._get("/api/health"))

    def launch_app(self, bundle_id: str) -> dict:
        """Terminate (if running) and relaunch ``bundle_id`` (``/api/launch``)."""
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(self._get("/api/launch", params={"bundleId": bundle_id}))

    def activate(self, bundle_id: str) -> dict:
        """Foreground ``bundle_id``, launching it if it has exited (``/api/activate``)."""
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(self._get("/api/activate", params={"bundleId": bundle_id}))

    def terminate(self, bundle_id: str) -> dict:
        """Terminate ``bundle_id`` (``/api/terminate``)."""
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(self._get("/api/terminate", params={"bundleId": bundle_id}))

    def start_measuring(self, bundle_id: str) -> dict:
        """Open an ``XCTMemoryMetric`` window on ``bundle_id`` (``/api/startMeasuring``)."""
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(self._get("/api/startMeasuring", params={"bundleId": bundle_id}))

    def stop_measuring(self) -> dict:
        """Close the measured window (``/api/stopMeasuring``)."""
        return self._json(self._get("/api/stopMeasuring"))

    def dt_measuring(self, seconds: int, bundle_id: str) -> dict:
        """Open a measured window that auto-closes after ``seconds`` (``/api/dtMeasuring/{seconds}``)."""
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(
            self._get(f"/api/dtMeasuring/{seconds}", params={"bundleId": bundle_id})
        )

    def screenshot(self, dest_path: Path | str) -> Path:
        """Capture one screenshot and write it to ``dest_path`` (``/api/screenshot``).

        Returns:
            Path: The written screenshot path.
        """
        response = self._get("/api/screenshot")
        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return path

    def exit(self) -> dict:
        """Quit the runner (``/api/exit``)."""
        return self._json(self._get("/api/exit"))
