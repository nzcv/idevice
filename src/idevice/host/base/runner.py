"""HTTP client for the RemoteControlTest runner, reached via the keeper proxy.

The runner embeds a small HTTP server on the iOS device (see
``RemoteControlTest/README.md``). The host never dials the device directly:
requests are sent to the EndlessKeeper control server's runner proxy
(``/api/runs/{udid}/proxy``), which forwards them to the on-device runner for
app control, screenshots, and measurement.
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
    """Thin client for the runner, reached through the keeper proxy.

    The ``base_url`` points at the keeper's runner proxy prefix
    (e.g. ``http://{keeper-ip}:{port}/api/runs/{udid}/proxy``). Route paths such
    as ``/api/health`` are appended verbatim and forwarded by the keeper to the
    on-device runner.
    """

    def __init__(self, base_url: str, *, timeout: float | None = None) -> None:
        """Bind the client to the keeper's runner proxy.

        Args:
            base_url: Keeper runner-proxy prefix. Required and non-empty;
                a trailing slash is stripped.
            timeout: Per-request timeout in seconds; defaults to
                :func:`idevice.host.config.http_timeout`.

        Raises:
            ValueError: If ``base_url`` is empty.
        """
        if not base_url:
            raise ValueError("base_url is required and must be a non-empty string")
        self._timeout = timeout if timeout is not None else config.http_timeout()
        self.base = base_url.rstrip("/")
        logger.info(f"{_LOG_TAG} Runner initialized: {self.base}")

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
        """Liveness probe (``GET /api/health``).

        The runner returns ``200`` with ``{"status": "ok"}`` only once its
        main-thread command loop is ready. While it is still coming up it
        returns ``503`` with ``{"status": "not_ready", "reason": ...}`` where
        ``reason`` is the runner phase (``notStarted`` before the loop starts,
        ``initializing`` during prompt acceptance / backgrounding); ``_get``
        surfaces that ``503`` as a :class:`RunnerError`.
        """
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
        """Open an ``XCTMemoryMetric`` window on ``bundle_id`` (``/api/measuring/start``)."""
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(self._get("/api/measuring/start", params={"bundleId": bundle_id}))

    def stop_measuring(self) -> dict:
        """Close the measured window (``/api/measuring/stop``)."""
        return self._json(self._get("/api/measuring/stop"))

    def measuring_status(self) -> dict:
        """Report the current measuring state (``/api/measuring/status``).

        The server's ``state`` walks through ``idle`` (before any measurement),
        ``started`` (while a window is open), and ``stopped`` (after one closes).
        """
        return self._json(self._get("/api/measuring/status"))

    def dt_measuring(self, seconds: int, bundle_id: str) -> dict:
        """Open a measured window that auto-closes after ``seconds`` (``/api/measuring/period/{seconds}``)."""
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._json(
            self._get(f"/api/measuring/period/{seconds}", params={"bundleId": bundle_id})
        )

    def start_periodic_screenshots(
        self, interval: float = 1.0, limit: int | None = None
    ) -> dict:
        """Begin periodic screenshots (``/api/screenshot/start``).

        Args:
            interval: Seconds between captures (server default ``1``).
            limit: Stop after this many captures; ``None`` / ``0`` is unlimited.
        """
        if interval <= 0:
            raise ValueError("interval must be positive")
        params: dict = {"interval": interval}
        if limit:
            params["limit"] = limit
        return self._json(self._get("/api/screenshot/start", params=params))

    def stop_periodic_screenshots(self) -> dict:
        """Stop periodic screenshots (``/api/screenshot/stop``)."""
        return self._json(self._get("/api/screenshot/stop"))

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

    def tap(self, x: float, y: float, bundle_id: str | None = None) -> dict:
        """Tap the screen at a normalized point (``/api/tap``).

        Coordinates are fractions of the screen, so they are independent of the
        device's pixel resolution and point scale: ``(0, 0)`` is the top-left
        corner and ``(1, 1)`` the bottom-right. To tap a feature located in a
        screenshot, divide its pixel coordinates by the screenshot's width and
        height.

        Args:
            x: Horizontal position in ``[0, 1]`` (fraction of screen width).
            y: Vertical position in ``[0, 1]`` (fraction of screen height).
            bundle_id: Foreground app to anchor the offset to. Required to tap
                correctly in landscape: SpringBoard is portrait-locked, so
                without it the offset is measured against a portrait frame and
                lands at the wrong physical point for a landscape app. When set,
                the runner anchors the offset to that app's frame, which tracks
                the current interface orientation and matches the screenshot.

        Raises:
            ValueError: If ``x`` or ``y`` is outside ``[0, 1]``.
        """
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError("x and y must be normalized coordinates in [0, 1]")
        params: dict = {"x": x, "y": y}
        if bundle_id:
            params["bundleId"] = bundle_id
        return self._json(self._get("/api/tap", params=params))

    def exit(self) -> dict:
        """Quit the runner (``/api/exit``)."""
        return self._json(self._get("/api/exit"))
