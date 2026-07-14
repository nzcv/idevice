"""No-op ``DeviceBase`` used when no real device can be bound."""

from __future__ import annotations

import logging
from pathlib import Path

from idevice.device.base.device import DeviceBase
from idevice.device.cache import InstalledAppInfo

logger = logging.getLogger(__name__)

_LOG_TAG = "[DummyDevice]"


class DummyDevice(DeviceBase):
    """No-op :class:`DeviceBase` returned when no device can be bound.

    :meth:`idevice.device.device.Device.from_env` builds this when a required
    ``GAUTO_*`` variable (platform / device id) is missing/blank or the platform
    is unsupported, so the controller can keep running without special-casing an
    unconfigured environment. ``device_id`` / ``device_ip`` / ``platform`` expose
    whatever (possibly empty) values were read from the environment; every device
    *operation* logs an error explaining why no real device is bound and returns
    an inert default instead of raising.
    """

    def __init__(
        self,
        reason: str,
        *,
        device_id: str = "",
        device_ip: str = "",
        platform: str = "dummy",
    ) -> None:
        """Bind a no-op device without the strict ``device_id`` validation.

        The base initializer rejects an empty ``device_id``; a dummy device must
        be constructible from a blank environment, so the backing attributes are
        set directly (the public properties keep working) instead of calling
        ``super().__init__``.
        """
        self._reason = reason
        self._device_id = device_id
        self._device_ip = device_ip
        self._platform = platform
        logger.error(
            f"{_LOG_TAG} no device bound: {reason}; "
            f"all device operations will be no-ops"
        )

    def _noop(self, operation: str) -> None:
        """Log that ``operation`` was ignored because no device is bound."""
        logger.error(
            f"{_LOG_TAG} `{operation}` ignored: {self._reason}; no device is bound"
        )

    @classmethod
    def default_udid(cls) -> str:
        logger.error(f"{_LOG_TAG} `default_udid` ignored: no device is bound")
        return ""

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        del package_path, app_id
        self._noop("install")
        return False

    def uninstall(self, app_id: str) -> None:
        del app_id
        self._noop("uninstall")

    def is_installed(self, app_id: str) -> bool:
        del app_id
        self._noop("is_installed")
        return False

    def launch_app(self, app_id: str) -> None:
        del app_id
        self._noop("launch_app")

    def stop_app(self, app_id: str) -> None:
        del app_id
        self._noop("stop_app")

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        del app_id
        self._noop("get_installed_pkg_name")
        return None

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
    ) -> None:
        del x1, y1, x2, y2, duration_ms
        self._noop("swipe")

    def host_is_running(self) -> bool:
        self._noop("host_is_running")
        return False

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        del local, remote, app_id, documents_only
        self._noop("push")

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        del remote, local, app_id, documents_only
        self._noop("pull")

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        del remote, app_id, recursive
        self._noop("ls")
        return []

    def documents_exists(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        self._noop("documents_exists")
        return False

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        del app_id, remote
        self._noop("documents_ls")
        return []

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        del app_id, remote, local
        self._noop("documents_pull")
        return False

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        del app_id, local, remote
        self._noop("documents_push")
        return False

    def documents_rm(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        self._noop("documents_rm")
        return False

    def screenshot(self, local: Path | str) -> bool:
        del local
        self._noop("screenshot")
        return False
