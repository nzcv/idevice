"""Keeper-backed iOS memory-measurement host.

Runs on the **mac host**: :class:`Host` drives a run end to end via the
EndlessKeeper control server (:class:`~idevice.host.keeper.Keeper`) and the
on-device RemoteControlTest runner (:class:`~idevice.host.runner.Runner`).

Use :meth:`Host.create` / :meth:`Host.from_env` to build a host: ``macos``
yields a real :class:`Host`; every other platform yields a no-op
:class:`DummyHost` so the controller can drive any platform without special-casing.
"""

from __future__ import annotations

import logging
import time

from idevice.host import config
from idevice.host.errors import HostNotSupportedError, HostTimeoutError
from idevice.host.keeper import Keeper
from idevice.host.runner import Runner

logger = logging.getLogger(__name__)

_LOG_TAG = "[Host]"


class Host:
    """Drive a measurement run on one device via the keeper + on-device runner.

    ``Host`` is a process-wide singleton: the first construction binds the host
    to its keeper and device, and every subsequent ``Host(...)`` /
    :meth:`create` / :meth:`from_env` call returns that same instance. Use
    :meth:`reset` to drop the cached instance (mainly for tests).
    """

    _instance: Host | None = None

    def __new__(cls, *args, **kwargs) -> Host:  # noqa: D102 - singleton wiring
        del args, kwargs
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached singleton so the next construction rebinds the host."""
        cls._instance = None

    def __init__(
        self,
        platform: str,
        *,
        keeper_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        keeper_id: str = "",
        device_udid: str,
        device_ip: str,
        bundle_id: str,
    ) -> None:
        """Bind the host to a keeper control server and a target device.

        On the first call the host is bound to ``keeper_ip`` / ``device_udid`` /
        ``device_ip``; later calls on the existing singleton are ignored (a debug
        line is logged) so the bound coordinates stay stable.

        Raises:
            ValueError: If ``keeper_ip``, ``device_udid`` or ``device_ip`` is empty.
        """
        if getattr(self, "_initialized", False):
            logger.debug(
                f"{_LOG_TAG} reusing singleton bound to {self.device_udid}; "
                f"ignoring re-init with device_udid={device_udid}"
            )
            return
        if not keeper_ip:
            raise ValueError("keeper_ip is required and must be a non-empty string")
        if not device_udid:
            raise ValueError("device_udid is required and must be a non-empty string")
        if not device_ip:
            raise ValueError("device_ip is required and must be a non-empty string")
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        self.platform = platform
        self.bundle_id = bundle_id
        self.keeper_ip = keeper_ip
        self.keeper_port = int(keeper_port)
        self.device_udid = device_udid
        self.device_ip = device_ip
        self.keeper_id = keeper_id
        self.keeper = Keeper(keeper_ip, self.keeper_port)
        self._runner: Runner | None = None
        self._initialized = True

    @classmethod
    def create(
        cls,
        *,
        platform: str,
        keeper_ip: str,
        device_udid: str,
        device_ip: str,
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        keeper_id: str = "",
        bundle_id: str,
    ) -> Host | DummyHost:
        """Build a host for ``platform``: ``macos`` -> :class:`Host`, else :class:`DummyHost`."""
        logger.debug(f"{_LOG_TAG} create platform={platform} device_udid={device_udid}")
        if platform.lower() == "macos":
            return cls(
                platform=platform,
                keeper_ip=keeper_ip,
                keeper_port=keeper_port,
                device_udid=device_udid,
                device_ip=device_ip,
                keeper_id=keeper_id,
                bundle_id=bundle_id,
            )
        return DummyHost(
            keeper_ip=keeper_ip,
            keeper_port=keeper_port,
            device_udid=device_udid,
            device_ip=device_ip,
            platform=platform.lower(),
            keeper_id=keeper_id,
        )

    @classmethod
    def from_env(cls) -> Host | DummyHost:
        """Build a host from the ``GAUTO_*`` environment variables."""
        return cls.create(
            platform=config.host_platform(),
            keeper_ip=config.keeper_ip(),
            keeper_port=config.keeper_port(),
            device_udid=config.device_udid(),
            device_ip=config.device_ip(),
            keeper_id=config.keeper_id(),
            bundle_id=config.bundle_id(),
        )

    def runner(self) -> Runner:
        """Return a :class:`Runner` bound to the device's current runner port.

        The port is resolved from the keeper status ``server_port`` and falls
        back to :func:`idevice.host.config.runner_port`.
        """
        port = self._resolve_runner_port()
        if self._runner is None or self._runner._port != port:
            logger.debug(f"{_LOG_TAG} binding runner to {self.device_ip}:{port}")
            self._runner = Runner(self.device_ip, port)
        return self._runner

    def _resolve_runner_port(self) -> int:
        try:
            port = self.keeper.status(self.device_udid).get("server_port")
        except Exception as exc:  # noqa: BLE001 - best-effort port discovery
            logger.debug(f"{_LOG_TAG} status unavailable while resolving port: {exc}")
            port = None
        return int(port) if port else config.runner_port()

    def health(self) -> bool:
        """Return ``True`` if the keeper is reachable."""
        try:
            self.keeper.health()
        except Exception as exc:  # noqa: BLE001 - boolean probe
            logger.debug(f"{_LOG_TAG} keeper health failed: {exc}")
            return False
        return True

    def _launch_runner(self, **overrides) -> dict:
        """Launch the run for the bound device (``POST /api/runs``)."""
        logger.info(f"{_LOG_TAG} launching run for {self.device_udid}")
        record = self.keeper.launch(self.device_udid, **overrides)
        port = record.get("server_port")
        if port:
            self._runner = Runner(self.device_ip, int(port))
        return record

    def _runner_alive(self) -> bool:
        """Return ``True`` if the on-device runner currently answers health."""
        try:
            self.runner().health()
        except Exception as exc:  # noqa: BLE001 - boolean probe
            logger.debug(f"{_LOG_TAG} runner not alive on {self.device_ip}: {exc}")
            return False
        return True

    def _has_active_run(self) -> bool:
        """Return ``True`` if the keeper still tracks a run for this device."""
        try:
            record = self.keeper.status(self.device_udid)
        except Exception as exc:  # noqa: BLE001 - no run / keeper 404
            logger.debug(f"{_LOG_TAG} no active keeper run for {self.device_udid}: {exc}")
            return False
        return bool(record)

    def _wait_runner_down(self, *, deadline: float, interval: float) -> None:
        """Block until the on-device runner stops answering or ``deadline`` passes.

        Raises:
            HostTimeoutError: If the runner is still alive when ``deadline`` passes.
        """
        while time.monotonic() < deadline:
            if not self._runner_alive():
                logger.info(f"{_LOG_TAG} previous runner exited on {self.device_ip}")
                return
            logger.debug(f"{_LOG_TAG} waiting for previous runner to exit on {self.device_ip}")
            time.sleep(interval)
        raise HostTimeoutError(
            f"{_LOG_TAG} previous runner did not exit in time on {self.device_ip}"
        )

    def _ensure_fresh_runner(
        self,
        *,
        deadline: float,
        interval: float,
        **launch_overrides,
    ) -> dict:
        """Make the keeper drive a freshly-started runner for this device.

        If a runner from a previous run is still alive (or the keeper still
        tracks a run), it is killed and allowed to fully exit *before* a new
        run is launched, so the relaunch never attaches to a stale runner.

        Returns:
            dict: The launched run record from the keeper.
        """
        if self._runner_alive() or self._has_active_run():
            logger.info(
                f"{_LOG_TAG} existing runner detected for {self.device_udid}; "
                f"killing before relaunch"
            )
            try:
                self._kill()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                logger.debug(f"{_LOG_TAG} kill before relaunch failed: {exc}")
            self._wait_runner_down(deadline=deadline, interval=interval)
        return self._launch_runner(**launch_overrides)

    def launch_app(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        interval: float = 2.0,
    ) -> dict:
        """Launch ``bundle_id`` on the device and make sure it comes up running.

        Robust against the usual transient states during a run:

        - ensures the keeper is driving a freshly-started runner: a runner
          left over from a previous run is killed and allowed to fully exit
          before a new one is launched (otherwise the relaunch could attach
          to a stale runner);
        - waits for the on-device runner to be reachable (it may still be
          spawning right after launch);
        - terminates (if already running) and relaunches the app via the
          runner, so a stale/foreground instance is replaced by a fresh one;
        - retries transient runner failures (server not yet listening,
          connection resets) until the app launches or ``timeout`` elapses.

        Args:
            bundle_id: Target app bundle identifier. Required and non-empty.
            timeout: Overall budget in seconds covering runner restart,
                readiness, and the launch itself.
            interval: Delay in seconds between retries / state polls.

        Returns:
            dict: The runner's launch result.

        Raises:
            ValueError: If ``bundle_id`` is empty.
            HostTimeoutError: If the runner does not restart/become ready or
                the app is not launched within ``timeout``.
        """
        if not self.bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")

        deadline = time.monotonic() + timeout
        # Make sure the keeper has launched a fresh runner: if one from a
        # previous run is still alive, kill it and wait for it to exit, then
        # launch a new one. Only after that does waiting for readiness make sense.
        self._ensure_fresh_runner(deadline=deadline, interval=interval)
        self._wait_until_ready(
            timeout=max(deadline - time.monotonic(), 0.0),
            interval=interval,
        )

        last_error: Exception | None = None
        while True:
            try:
                result = self.runner().launch_app(self.bundle_id)
                logger.info(f"{_LOG_TAG} launched {self.bundle_id} on {self.device_ip}")
                return result
            except Exception as exc:  # noqa: BLE001 - retry transient runner failures
                last_error = exc
                logger.debug(
                    f"{_LOG_TAG} launch {self.bundle_id} failed, retrying: {exc}"
                )
            if time.monotonic() + interval >= deadline:
                break
            time.sleep(interval)

        raise HostTimeoutError(
            f"{_LOG_TAG} app {self.bundle_id} not launched within {timeout}s on "
            f"{self.device_ip}: {last_error}"
        )


    def _status(self) -> dict:
        """Keeper run status for the bound device."""
        return self.keeper.status(self.device_udid)

    def _kill(self) -> dict:
        """Kill the keeper run for the bound device."""
        logger.info(f"{_LOG_TAG} killing run for {self.device_udid}")
        return self.keeper.kill(self.device_udid)

    def export(self, presigned_url: str, content_type: str | None = None) -> dict:
        """Export the run's memgraphs to a presigned URL via the keeper."""
        logger.info(f"{_LOG_TAG} exporting run for {self.device_udid}")
        return self.keeper.export(self.device_udid, presigned_url, content_type)

    def _wait_until_ready(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        interval: float = 2.0,
    ) -> None:
        """Poll the on-device runner until healthy or ``timeout`` elapses.

        Raises:
            HostTimeoutError: If the runner does not become ready in time.
        """
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.runner().health()
                logger.info(f"{_LOG_TAG} runner ready on {self.device_ip}")
                return
            except Exception as exc:  # noqa: BLE001 - retry until the runner is up
                last_error = exc
                time.sleep(interval)
        raise HostTimeoutError(
            f"{_LOG_TAG} runner not ready within {timeout}s on {self.device_ip}: {last_error}"
        )

    def capture_memgraph(self, timeout: float = 60.0) -> dict:
        """Open a measured window that auto-closes after ``5 seconds``."""
        deadline = time.monotonic() + timeout
        ret = self.runner().dt_measuring(5, self.bundle_id)
        # wait state to be "stopped"
        while time.monotonic() < deadline:
            status = self.runner().measuring_status()
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
        """Quit the on-device runner."""
        return self.runner().exit()


class DummyHost:
    """No-op host for non-macOS platforms; mirrors :class:`Host`'s interface.

    There is no keeper or on-device runner to talk to, so it constructs without
    requiring coordinates, reports itself unhealthy, and returns inert
    placeholder results instead of raising.
    """

    def __init__(
        self,
        *,
        keeper_ip: str = "",
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        device_udid: str = "",
        device_ip: str = "",
        platform: str = "dummy",
        keeper_id: str = "",
    ) -> None:
        self.platform = platform
        self.keeper_ip = keeper_ip
        self.keeper_port = int(keeper_port)
        self.device_udid = device_udid
        self.device_ip = device_ip
        self.keeper_id = keeper_id
        self.keeper = None
        logger.debug(f"{_LOG_TAG} dummy host for platform={platform} udid={device_udid!r}")

    def _noop(self, operation: str) -> dict:
        logger.warning(f"{_LOG_TAG} {operation} is a no-op on platform={self.platform}")
        return {
            "status": "dummy",
            "platform": self.platform,
            "device_udid": self.device_udid,
            "operation": operation,
        }

    def health(self) -> bool:
        return False

    def launch(self, **overrides) -> dict:
        del overrides
        return self._noop("launch")

    def launch_app(
        self,
        bundle_id: str,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        *,
        interval: float = 2.0,
    ) -> dict:
        del timeout, interval
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        return self._noop("launch_app")

    def status(self) -> dict:
        return self._noop("status")

    def kill(self) -> dict:
        return self._noop("kill")

    def export(self, presigned_url: str, content_type: str | None = None) -> dict:
        del presigned_url, content_type
        return self._noop("export")

    def runner(self) -> Runner:
        raise HostNotSupportedError(
            f"{_LOG_TAG} no on-device runner is available on platform={self.platform}"
        )

    def wait_until_ready(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        interval: float = 2.0,
    ) -> None:
        del timeout, interval
        logger.warning(f"{_LOG_TAG} wait_until_ready is a no-op on platform={self.platform}")