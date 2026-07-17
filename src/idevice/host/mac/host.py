"""macOS keeper-backed :class:`HostBase` implementation.

Runs on the **mac host**: :class:`MacHost` drives a run end to end via the
ikeeper control server (:class:`~idevice.host.base.keeper.Keeper`) and the
on-device RemoteControlTest runner (:class:`~idevice.host.base.runner.Runner`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from idevice.host import config
from idevice.host.base.host import HostBase
from idevice.host.base.keeper import Keeper
from idevice.host.base.runner import Runner

logger = logging.getLogger(__name__)

_LOG_TAG = "[MacHost]"


class MacHost(HostBase):
    """Drive a measurement run on one device via the keeper + on-device runner."""

    def __init__(
        self,
        host_type: str = "macos",
        *,
        keeper_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        keeper_id: str = "",
        device_udid: str,
        device_ip: str,
        bundle_id: str,
    ) -> None:
        """Bind the host to a keeper control server and a target device.

        Raises:
            ValueError: If ``keeper_ip``, ``device_udid``, ``device_ip`` or
                ``bundle_id`` is empty.
        """
        super().__init__(
            host_type,
            keeper_ip=keeper_ip,
            keeper_port=keeper_port,
            device_udid=device_udid,
            device_ip=device_ip,
            bundle_id=bundle_id,
            keeper_id=keeper_id,
        )
        self.keeper = Keeper(self.keeper_ip, self.keeper_port)
        self._runner: Runner | None = None

    @classmethod
    def from_env(cls) -> MacHost:
        """Build a :class:`MacHost` from the ``GAUTO_*`` environment variables.

        Reads ``GAUTO_HOST_TYPE``, ``GAUTO_HOST_IP``, ``GAUTO_HOST_PORT``,
        ``GAUTO_HOST_ID``, ``GAUTO_DEVICE_UDID``, ``GAUTO_DEVICE_IP`` and
        ``GAUTO_PACKAGE_NAME``.

        Returns:
            MacHost: A host bound to the keeper/device described by the
            environment.

        Raises:
            ValueError: If ``GAUTO_HOST_IP``, ``GAUTO_DEVICE_UDID``,
                ``GAUTO_DEVICE_IP`` or ``GAUTO_PACKAGE_NAME`` is empty.
        """
        return cls(
            host_type=config.host_type(),
            keeper_ip=config.keeper_ip(),
            keeper_port=config.keeper_port(),
            keeper_id=config.keeper_id(),
            device_udid=config.device_udid(),
            device_ip=config.device_ip(),
            bundle_id=config.bundle_id(),
        )

    def runner(self) -> Runner:
        """Return a :class:`Runner` bound to the keeper's runner proxy.

        All runner traffic is forwarded by the keeper to the on-device runner
        (``/api/runs/{udid}/proxy``), so the host only ever talks to the keeper
        and no device port discovery is needed.
        """
        if self._runner is None:
            base = f"{self.keeper.base}/api/runs/{self.device_udid}/proxy"
            logger.debug(f"{_LOG_TAG} binding runner to keeper proxy {base}")
            self._runner = Runner(base)
        return self._runner

    def health(self) -> bool:
        """Return ``True`` if the keeper is reachable."""
        try:
            self.keeper.health()
        except Exception as exc:  # noqa: BLE001 - boolean probe
            logger.debug(f"{_LOG_TAG} keeper health failed: {exc}")
            return False
        return True

    def launch_app(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        memgraph: bool = False,
    ) -> dict:
        """Launch ``bundle_id`` via the keeper's combined launch endpoint.

        Delegates the whole flow to ``GET /api/runs/{udid}/launch``: the keeper
        frees the device by killing any still-active run and waiting for it to
        finish, launches the xctest run, waits for the on-device runner to come
        up, then launches the app, returning both the run record and the launch
        result. The host therefore does not need to kill a leftover run first;
        the keeper guarantees the device is free before relaunching.

        Args:
            timeout: Overall budget in seconds covering build, runner startup,
                and the launch itself; passed to the keeper as ``timeout_secs``.
            memgraph: When ``True``, ask the keeper to cold-launch with
                performance diagnostics and ``MallocStackLogging`` so a later
                diagnostic memory graph carries allocation backtraces.

        Returns:
            dict: The keeper's combined result, e.g.
            ``{"status": "ok", "run": {...}, "launch": {...}}``.

        Raises:
            ValueError: If ``bundle_id`` is empty.
            KeeperError: If the keeper cannot launch the run or the app.
        """
        if not self.bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")

        result = self.keeper.launch_app(
            self.device_udid,
            ip=self.device_ip,
            bundle_id=self.bundle_id,
            timeout_secs=int(timeout),
            # Hold the HTTP request open a bit longer than the keeper's own
            # budget, since it blocks until the launch finishes.
            timeout=timeout + config.http_timeout(),
            memgraph=memgraph,
        )
        logger.info(f"{_LOG_TAG} launched {self.bundle_id} on {self.device_ip}")
        return result

    def screenshot(self, dest_path: Path | str) -> dict:
        """Capture one screenshot via the runner and write it to ``dest_path``."""
        path = self.runner().screenshot(dest_path)
        logger.info(f"{_LOG_TAG} saved screenshot for {self.device_udid} to {path}")
        return {"status": "ok", "action": "screenshot", "path": str(path)}

    def tap(self, x: float, y: float) -> dict:
        """Tap the device screen at the normalized point ``(x, y)`` via the runner.

        The bound ``bundle_id`` is forwarded so the runner anchors the offset to
        the foreground app's frame, which tracks the current interface
        orientation (e.g. a landscape game) and matches the screenshot.
        """
        ret = self.runner().tap(x, y, bundle_id=self.bundle_id)
        logger.info(f"{_LOG_TAG} tapped ({x:.4f}, {y:.4f}) on {self.device_udid}")
        return ret

    def status(self) -> dict:
        """Keeper run status for the bound device."""
        return self.keeper.status(self.device_udid)

    def kill(self) -> dict:
        """Kill the keeper run for the bound device."""
        logger.info(f"{_LOG_TAG} killing run for {self.device_udid}")
        return self.keeper.kill(self.device_udid)

    def export(self) -> dict:
        """Export the run's memgraphs via the keeper (keeper presigns + uploads)."""
        logger.info(f"{_LOG_TAG} exporting run for {self.device_udid}")
        return self.keeper.export(self.device_udid)

    def capture_memgraph(self, timeout: float = 60.0) -> dict:
        """Open a measured window that auto-closes after ``5 seconds``."""
        deadline = time.monotonic() + timeout
        ret = self.runner().dt_measuring(5, self.bundle_id)
        # wait state to be "stopped"
        while time.monotonic() < deadline:
            status = self.runner().measuring_status(timeout=timeout)
            if status["state"] == "stopped":
                break
            logger.info(f"{_LOG_TAG} waiting for memgraph to be captured on {self.device_ip}")
            time.sleep(1)
        if time.monotonic() >= deadline:
            raise HostTimeoutError(
                f"{_LOG_TAG} memgraph not captured within {timeout}s on {self.device_ip}"
            )
        return ret

    def exit(self) -> dict:
        """Gracefully quit the on-device runner via the keeper.

        Uses the keeper's dedicated graceful-exit endpoint
        (``POST /api/runs/{udid}/exit``) instead of proxying ``/api/exit``
        directly. The keeper asks the runner to quit and then waits for the run
        to reach a terminal state (finalizing the ``.xcresult``), only awaiting
        the exit request's response headers rather than its body. This avoids
        the spurious ``502`` the proxy path returns when the runner tears down
        its HTTP server (and the XCUITest process) before the ``/api/exit``
        response is fully written back.
        """
        logger.info(f"{_LOG_TAG} exiting run for {self.device_udid}")
        return self.keeper.exit(self.device_udid, device_host=self.device_ip)
