"""No-op ``HostBase`` used when no real (macOS) host can be bound."""

from __future__ import annotations

import logging

from idevice.host import config
from idevice.host.base.errors import HostNotSupportedError
from idevice.host.base.host import HostBase
from idevice.host.base.runner import Runner

logger = logging.getLogger(__name__)

_LOG_TAG = "[DummyHost]"


class DummyHost(HostBase):
    """No-op :class:`HostBase` returned when no real host can be bound.

    :meth:`idevice.host.host.Host.from_env` / :meth:`~idevice.host.host.Host.create`
    build this for non-macOS platforms or when a required ``GAUTO_*`` variable is
    missing/blank, so the controller can drive any platform without special-casing
    an unconfigured environment. ``keeper_ip`` / ``device_udid`` / ``device_ip`` /
    ``bundle_id`` / ``platform`` expose whatever (possibly empty) values were read
    from the environment; every host *operation* reports itself unhealthy and
    returns an inert placeholder result instead of raising.
    """

    def __init__(
        self,
        reason: str,
        *,
        keeper_ip: str = "",
        keeper_port: int = config.DEFAULT_KEEPER_PORT,
        device_udid: str = "",
        device_ip: str = "",
        bundle_id: str = "",
        platform: str = "dummy",
        keeper_id: str = "",
    ) -> None:
        """Bind a no-op host without the strict coordinate validation.

        The base initializer rejects empty coordinates; a dummy host must be
        constructible from a blank environment, so the backing attributes are
        set directly (the public properties keep working) instead of calling
        ``super().__init__``.
        """
        self._reason = reason
        self._platform = platform
        self._keeper_ip = keeper_ip
        self._keeper_port = int(keeper_port)
        self._device_udid = device_udid
        self._device_ip = device_ip
        self._bundle_id = bundle_id
        self._keeper_id = keeper_id
        self.keeper = None
        logger.error(
            f"{_LOG_TAG} no host bound: {reason}; all host operations will be no-ops"
        )

    def _noop(self, operation: str) -> dict:
        """Log that ``operation`` was ignored because no host is bound."""
        logger.warning(
            f"{_LOG_TAG} `{operation}` is a no-op on platform={self.platform}: {self._reason}"
        )
        return {
            "status": "dummy",
            "platform": self.platform,
            "device_udid": self.device_udid,
            "operation": operation,
        }

    def health(self) -> bool:
        return False

    def runner(self) -> Runner:
        raise HostNotSupportedError(
            f"{_LOG_TAG} no on-device runner is available on platform={self.platform}"
        )

    def launch_app(
        self,
        *,
        timeout: float = config.DEFAULT_READY_TIMEOUT,
    ) -> dict:
        del timeout
        return self._noop("launch_app")

    def capture_memgraph(self, timeout: float = 60.0) -> dict:
        del timeout
        return self._noop("capture_memgraph")

    def export(self, presigned_url: str, content_type: str | None = None) -> dict:
        del presigned_url, content_type
        return self._noop("export")

    def status(self) -> dict:
        return self._noop("status")

    def kill(self) -> dict:
        return self._noop("kill")

    def exit(self) -> dict:
        return self._noop("exit")
