"""No-op ``HostBase`` implementation for non-macOS platforms.

The keeper-backed measurement workflow only runs on the mac host (see
``idevice.host.mac.host.MacHost``). On every other platform there is no keeper
or on-device runner to talk to, so :class:`DummyHost` stands in as a benign
placeholder: it constructs without requiring keeper/device coordinates, reports
itself as unhealthy, and returns inert placeholder results instead of raising
``HostNotSupportedError``.
"""

from __future__ import annotations

import logging

from idevice.host import config
from idevice.host.base.errors import HostNotSupportedError
from idevice.host.base.host import HostBase
from idevice.host.base.runner import Runner

logger = logging.getLogger(__name__)

_LOG_TAG = "[DummyHost]"


class DummyHost(HostBase):
    """A no-op host used on platforms without a keeper (anything but macOS)."""

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
        """Bind a dummy host without contacting a keeper.

        Unlike :class:`HostBase`, none of the coordinates are required: a dummy
        host is expected on platforms where no keeper/runner is available, so it
        must construct even when they are empty.
        """
        # Bypass HostBase.__init__ on purpose: it requires non-empty keeper/device
        # coordinates and builds a live Keeper client, neither of which applies here.
        self._keeper_ip = keeper_ip
        self._keeper_port = int(keeper_port)
        self._device_udid = device_udid
        self._device_ip = device_ip
        self._platform = platform
        self._keeper_id = keeper_id
        self.keeper = None
        logger.debug(
            f"{_LOG_TAG} created for platform={platform} device_udid={device_udid!r}"
        )

    def _noop(self, operation: str) -> dict:
        logger.warning(
            f"{_LOG_TAG} {operation} is a no-op on platform={self._platform}"
        )
        return {
            "status": "dummy",
            "platform": self._platform,
            "device_udid": self._device_udid,
            "operation": operation,
        }

    def health(self) -> bool:
        logger.debug(f"{_LOG_TAG} health is always False on platform={self._platform}")
        return False

    def launch(self, **overrides) -> dict:
        del overrides
        return self._noop("launch")

    def status(self) -> dict:
        return self._noop("status")

    def kill(self) -> dict:
        return self._noop("kill")

    def export(self, presigned_url: str, content_type: str | None = None) -> dict:
        del presigned_url, content_type
        return self._noop("export")

    def runner(self) -> Runner:
        raise HostNotSupportedError(
            f"{_LOG_TAG} no on-device runner is available on platform={self._platform}"
        )

    def wait_until_ready(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
        interval: float = 2.0,
    ) -> None:
        del timeout, interval
        logger.warning(
            f"{_LOG_TAG} wait_until_ready is a no-op on platform={self._platform}"
        )

    def start_measuring(self, bundle_id: str) -> dict:
        del bundle_id
        return self._noop("start_measuring")

    def stop_measuring(self) -> dict:
        return self._noop("stop_measuring")

    def measure(
        self,
        bundle_id: str,
        *,
        duration_s: float,
        export_url: str | None = None,
        content_type: str | None = None,
        **launch_overrides,
    ) -> dict:
        del export_url, content_type, launch_overrides
        logger.warning(
            f"{_LOG_TAG} measure({bundle_id!r}) is a no-op on platform={self._platform}"
        )
        return {
            "status": "dummy",
            "platform": self._platform,
            "device_udid": self._device_udid,
            "bundle_id": bundle_id,
            "duration_s": duration_s,
        }
