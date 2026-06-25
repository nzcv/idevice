"""macOS ``HostBase`` implementation: the keeper-backed measurement orchestrator."""

from __future__ import annotations

import logging
import time

from idevice.host import config
from idevice.host.base.errors import HostTimeoutError
from idevice.host.base.host import HostBase
from idevice.host.base.runner import Runner

logger = logging.getLogger(__name__)

_LOG_TAG = "[MacHost]"


class MacHost(HostBase):
    """Drive a measurement run on a device via the keeper and on-device runner."""

    def __init__(
        self,
        *,
        keeper_ip: str,
        keeper_port: int,
        device_udid: str,
        device_ip: str,
        keeper_id: str = "",
    ) -> None:
        super().__init__(
            keeper_ip=keeper_ip,
            keeper_port=keeper_port,
            device_udid=device_udid,
            device_ip=device_ip,
            platform="macos",
            keeper_id=keeper_id,
        )
        self._runner: Runner | None = None

    def _resolve_runner_port(self) -> int:
        """Resolve the on-device runner port from the keeper, with a config fallback."""
        for source in (self._safe_status,):
            record = source()
            port = record.get("server_port")
            if port:
                return int(port)
        return config.runner_port()

    def _safe_status(self) -> dict:
        try:
            return self.keeper.status(self.device_udid)
        except Exception as exc:  # noqa: BLE001 - status is best-effort for port discovery
            logger.debug(f"{_LOG_TAG} status unavailable while resolving runner port: {exc}")
            return {}

    def runner(self) -> Runner:
        port = self._resolve_runner_port()
        if self._runner is None or self._runner._port != port:
            logger.debug(f"{_LOG_TAG} binding runner to {self.device_ip}:{port}")
            self._runner = Runner(self.device_ip, port)
        return self._runner

    def health(self) -> bool:
        try:
            self.keeper.health()
        except Exception as exc:  # noqa: BLE001 - health is a boolean probe
            logger.debug(f"{_LOG_TAG} keeper health failed: {exc}")
            return False
        try:
            self.runner().health()
        except Exception as exc:  # noqa: BLE001 - health is a boolean probe
            logger.debug(f"{_LOG_TAG} runner health failed: {exc}")
            return False
        return True

    def launch(self, **overrides) -> dict:
        logger.info(f"{_LOG_TAG} launching run for {self.device_udid}")
        record = self.keeper.launch(self.device_udid, **overrides)
        port = record.get("server_port")
        if port:
            self._runner = Runner(self.device_ip, int(port))
        return record

    def status(self) -> dict:
        return self.keeper.status(self.device_udid)

    def kill(self) -> dict:
        logger.info(f"{_LOG_TAG} killing run for {self.device_udid}")
        return self.keeper.kill(self.device_udid)

    def export(self, presigned_url: str, content_type: str | None = None) -> dict:
        logger.info(f"{_LOG_TAG} exporting run for {self.device_udid}")
        return self.keeper.export(self.device_udid, presigned_url, content_type)

    def wait_until_ready(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        interval: float = 2.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.runner().health()
                logger.info(f"{_LOG_TAG} runner ready on {self.device_ip}")
                return
            except Exception as exc:  # noqa: BLE001 - retry until the runner is up
                last_error = exc
                logger.debug(f"{_LOG_TAG} runner not ready yet: {exc}")
                time.sleep(interval)
        raise HostTimeoutError(
            f"{_LOG_TAG} runner did not become ready within {timeout}s on {self.device_ip}: {last_error}"
        )

    def start_measuring(self, bundle_id: str) -> dict:
        return self.runner().start_measuring(bundle_id)

    def stop_measuring(self) -> dict:
        return self.runner().stop_measuring()

    def measure(
        self,
        bundle_id: str,
        *,
        duration_s: float,
        export_url: str | None = None,
        content_type: str | None = None,
        **launch_overrides,
    ) -> dict:
        if not bundle_id:
            raise ValueError("bundle_id is required and must be a non-empty string")
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")

        launch_record = self.launch(**launch_overrides)
        self.wait_until_ready()
        start = self.start_measuring(bundle_id)
        logger.info(f"{_LOG_TAG} measuring {bundle_id} for {duration_s}s")
        time.sleep(duration_s)
        stop = self.stop_measuring()

        summary: dict = {
            "device_udid": self.device_udid,
            "bundle_id": bundle_id,
            "duration_s": duration_s,
            "launch": launch_record,
            "start_measuring": start,
            "stop_measuring": stop,
        }
        if export_url:
            summary["export"] = self.export(export_url, content_type)
        return summary
