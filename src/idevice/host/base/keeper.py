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
        logger.info(f"{_LOG_TAG} Keeper initialized: {self.base}")

    def _request(
        self,
        method: str,
        route: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        timeout: float | None = None,
    ) -> dict | list:
        url = f"{self.base}{route}"
        logger.info(f"{_LOG_TAG} {method} {url}")
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except requests.RequestException as exc:
            raise KeeperError(f"{_LOG_TAG} request failed: {method} {url}: {exc}") from exc
        if not response.ok:
            body = response.text
            detail = f": {body}" if body else ""
            raise KeeperError(
                f"{_LOG_TAG} {method} {url} returned {response.status_code}{detail}",
                status_code=response.status_code,
                body=body,
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

    def launch_app(
        self,
        device_udid: str,
        *,
        ip: str,
        bundle_id: str,
        timeout_secs: int | None = None,
        timeout: float | None = None,
        memgraph: bool = False,
    ) -> dict:
        """Launch a run and the app in one call (``GET /api/runs/{udid}/launch``).

        The keeper launches the xctest run, waits for the on-device runner to
        come up, then launches ``bundle_id`` on the device. This single request
        blocks until the app is launched or the keeper's deadline elapses.

        Args:
            device_udid: Target device UDID and run key.
            ip: Device IP the keeper records as ``device_host`` and proxies the
                runner through.
            bundle_id: Bundle id of the app to launch.
            timeout_secs: Keeper-side budget covering build, runner startup, and
                launch; ``None`` uses the keeper's default (300s).
            timeout: Per-request HTTP timeout; must exceed ``timeout_secs`` since
                the keeper holds the request open until it finishes.
            memgraph: When ``True``, ask the keeper to cold-launch with
                performance diagnostics and ``MallocStackLogging`` so a later
                diagnostic memory graph carries allocation backtraces.

        Returns:
            dict: The keeper's combined result, e.g.
            ``{"status": "ok", "run": {...}, "launch": {...}}``.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        if not ip:
            raise ValueError("ip is required and must be a non-empty string")
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        params: dict = {"ip": ip, "bundleId": bundle_id}
        if timeout_secs is not None:
            params["timeout_secs"] = int(timeout_secs)
        if memgraph:
            params["memgraph"] = "true"
        result = self._request(
            "GET",
            f"/api/runs/{device_udid}/launch",
            params=params,
            timeout=timeout,
        )
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

    def exit(
        self,
        device_udid: str,
        *,
        device_host: str | None = None,
        shutdown_timeout_secs: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Gracefully quit the on-device runner (``POST /api/runs/{udid}/exit``).

        The keeper asks the runner to quit (``GET /api/exit``) and then waits
        for the run to reach a terminal state, so ``xcodebuild`` finalizes the
        ``.xcresult``. Unlike proxying ``/api/exit`` directly, the keeper only
        awaits the exit request's response headers and then polls the run
        status, so it is not affected by the runner tearing down its HTTP
        server (and the XCUITest process) while the response is still being
        written. This request blocks until the runner exits or the keeper's
        shutdown timeout elapses.

        Args:
            device_udid: Target device UDID / run key.
            device_host: Reachable device host; overrides the one stored at
                launch. ``None`` uses the stored host.
            shutdown_timeout_secs: How long the keeper waits for the runner to
                quit and the ``.xcresult`` to finalize; ``None`` uses the
                keeper's default (120s).
            timeout: Per-request HTTP timeout; must exceed
                ``shutdown_timeout_secs`` since the keeper holds the request
                open until the runner exits. ``None`` derives a value that
                comfortably covers the keeper's shutdown budget.

        Returns:
            dict: The final run record.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        body: dict = {}
        if device_host is not None:
            body["device_host"] = device_host
        if shutdown_timeout_secs is not None:
            body["shutdown_timeout_secs"] = int(shutdown_timeout_secs)
        if timeout is None:
            # Keep the HTTP request open longer than the keeper's shutdown
            # budget (default 120s), which it blocks on while the runner quits.
            budget = shutdown_timeout_secs if shutdown_timeout_secs is not None else 120
            timeout = budget + self._timeout
        result = self._request(
            "POST",
            f"/api/runs/{device_udid}/exit",
            json_body=body,
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}

    def export(self, device_udid: str) -> dict:
        """Export memgraphs (``POST /api/runs/{udid}/export``).

        The keeper presigns the upload itself, uploads the archive, and signs it
        with its own content type (``application/x-xz``), so callers pass nothing
        beyond the device.

        Args:
            device_udid: Target device UDID / run key.

        Returns:
            dict: The export summary, including the ``download_url`` of the
            uploaded archive.
        """
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        result = self._request(
            "POST",
            f"/api/runs/{device_udid}/export",
            json_body={},
            timeout=max(self._timeout, 600.0),
        )
        return result if isinstance(result, dict) else {}
