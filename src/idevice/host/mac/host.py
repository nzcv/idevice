"""macOS keeper-backed :class:`HostBase` implementation.

Runs on the **mac host**: :class:`MacHost` drives a run end to end via the
EndlessKeeper control server (:class:`~idevice.host.base.keeper.Keeper`) and the
on-device RemoteControlTest runner (:class:`~idevice.host.base.runner.Runner`).
"""

from __future__ import annotations

import logging
import time

from idevice.host import config
from idevice.host.base.errors import HostTimeoutError, KeeperError
from idevice.host.base.host import HostBase
from idevice.host.base.keeper import Keeper
from idevice.host.base.runner import Runner

logger = logging.getLogger(__name__)

_LOG_TAG = "[MacHost]"


class MacHost(HostBase):
    """Drive a measurement run on one device via the keeper + on-device runner."""

    #: Keeper run states that still own the device; a relaunch must wait for
    #: these to finish before ``POST /api/runs`` will succeed.
    _ACTIVE_RUN_STATES = frozenset({"pending", "building", "running"})

    def __init__(
        self,
        platform: str = "macos",
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
            platform,
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

        Reads ``GAUTO_PLATFORM``, ``GAUTO_HOST_IP``, ``GAUTO_HOST_PORT``,
        ``GAUTO_HOST_ID``, ``GAUTO_DEVICE_UDID``, ``GAUTO_DEVICE_IP`` and
        ``GAUTO_BUNDLE_ID``.

        Returns:
            MacHost: A host bound to the keeper/device described by the
            environment.

        Raises:
            ValueError: If ``GAUTO_HOST_IP``, ``GAUTO_DEVICE_UDID``,
                ``GAUTO_DEVICE_IP`` or ``GAUTO_BUNDLE_ID`` is empty.
        """
        return cls(
            platform=config.host_platform(),
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

    def _launch_runner(
        self,
        *,
        deadline: float | None = None,
        interval: float = 2.0,
        **overrides,
    ) -> dict:
        """Launch the run for the bound device (``POST /api/runs``).

        The keeper's ``DELETE`` is asynchronous, so a just-killed run can still
        be tracked for a short window and make ``POST /api/runs`` return
        ``409 Conflict``. Retry on that transient conflict until ``deadline``.
        """
        logger.info(f"{_LOG_TAG} launching run for {self.device_udid}")
        # The keeper proxies runner traffic to ``device_host``, so it must be
        # told how to reach the device; supply the device IP at launch.
        overrides.setdefault("device_host", self.device_ip)
        while True:
            try:
                record = self.keeper.launch(self.device_udid, **overrides)
                break
            except KeeperError as exc:
                if (
                    exc.status_code == 409
                    and deadline is not None
                    and time.monotonic() + interval < deadline
                ):
                    logger.debug(
                        f"{_LOG_TAG} launch conflicted (409); keeper still "
                        f"releasing previous run for {self.device_udid}, retrying"
                    )
                    time.sleep(interval)
                    continue
                raise
        return record

    def _run_state(self) -> str | None:
        """Return the keeper run ``state`` for this device, or ``None``.

        ``None`` means the keeper tracks no run (``GET`` 404) or the state
        could not be read.
        """
        try:
            record = self.keeper.status(self.device_udid)
        except Exception as exc:  # noqa: BLE001 - no run / keeper 404
            logger.debug(f"{_LOG_TAG} no keeper run for {self.device_udid}: {exc}")
            return None
        status = record.get("status") if record else None
        return status.get("state") if isinstance(status, dict) else None

    def _has_active_run(self) -> bool:
        """Return ``True`` if the keeper still drives an *active* run.

        Only ``pending``/``building``/``running`` runs make ``POST /api/runs``
        return ``409`` ("a run is already active"). A finished run record
        (``exited``/``killed``/``failed``) does not block a relaunch: the
        keeper overwrites it on the next launch.
        """
        return self._run_state() in self._ACTIVE_RUN_STATES

    def _wait_run_inactive(self, *, deadline: float, interval: float) -> None:
        """Block until the keeper run for this device leaves its active state.

        The keeper's kill (``DELETE``) is asynchronous: it signals
        ``xcodebuild`` and the run only flips to a terminal state once the
        process tree tears down. Relaunching while the run is still active
        makes ``POST /api/runs`` fail with ``409``, so wait for it to finish.

        Raises:
            HostTimeoutError: If the run is still active when ``deadline``
                passes.
        """
        while time.monotonic() < deadline:
            if not self._has_active_run():
                logger.info(f"{_LOG_TAG} previous run finished for {self.device_udid}")
                return
            logger.debug(
                f"{_LOG_TAG} waiting for previous run to finish for {self.device_udid}"
            )
            time.sleep(interval)
        raise HostTimeoutError(
            f"{_LOG_TAG} previous run did not finish in time for {self.device_udid}"
        )

    def _ensure_fresh_runner(
        self,
        *,
        deadline: float,
        interval: float,
        **launch_overrides,
    ) -> dict:
        """Make the keeper drive a freshly-launched run for this device.

        If a previous run is still active it is killed and allowed to reach a
        terminal state *before* a new run is launched, otherwise the relaunch
        would attach to a stale runner or be rejected with ``409``. A finished
        run record needs no teardown: the launch overwrites it.

        Returns:
            dict: The launched run record from the keeper.
        """
        if self._has_active_run():
            logger.info(
                f"{_LOG_TAG} active run detected for {self.device_udid}; "
                f"killing before relaunch"
            )
            try:
                self.kill()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                logger.debug(f"{_LOG_TAG} kill before relaunch failed: {exc}")
            self._wait_run_inactive(deadline=deadline, interval=interval)
        return self._launch_runner(
            deadline=deadline, interval=interval, **launch_overrides
        )

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

    def status(self) -> dict:
        """Keeper run status for the bound device."""
        return self.keeper.status(self.device_udid)

    def kill(self) -> dict:
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
