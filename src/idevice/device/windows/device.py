"""Windows ``DeviceBase`` implementation via PowerShell AppX cmdlets."""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

from idevice.device.base.device import DeviceBase
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.config import powershell_binary

logger = logging.getLogger(__name__)


class WindowsDevice(DeviceBase):
    """``DeviceBase`` implementation for Windows MSIX/AppX packages."""

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(device_id, device_ip, platform="windows")
        self._runner = SubprocessRunner()
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        _app_dir = os.environ.get("IDEVICE_APP_DIR", "D:\\IDeviceExtractedApps")
        self._app_dir = Path(_app_dir)

    @classmethod
    def default_udid(cls) -> str:
        """Return the local host name as the default Windows device id."""
        return platform.node()

    def _run_powershell(self, script: str) -> str:
        command = [
            powershell_binary(),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"$ProgressPreference='SilentlyContinue'; {script}",
        ]
        result = self._runner.run(command)
        return result.stdout

    @staticmethod
    def _quote(value: str) -> str:
        """Quote a value as a PowerShell single-quoted string literal."""
        return "'" + value.replace("'", "''") + "'"

    def _pkg_dir(self, pkg_name: str) -> Path:
        """Return the per-app extraction directory derived from the zip name."""
        return self._app_dir / Path(pkg_name).stem

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        logger.info(f"Installing package on Windows device {self.device_id}: {package_path}")
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")
        if package_path.suffix != ".zip":
            raise ValueError("Package must be a zip file")
        if app_id is None:
            raise ValueError("app_id is required")

        # Remove only this app's extraction dir so other apps are left intact.
        pkg_dir = self._pkg_dir(package_path.name)
        if pkg_dir.exists():
            script = f"Remove-Item -Path {self._quote(str(pkg_dir))} -Recurse -Force"
            self._run_powershell(script)

        script = (
            f"Expand-Archive -Path {self._quote(str(package_path.resolve()))} "
            f"-DestinationPath {self._quote(str(self._app_dir))} -Force"
        )
        self._run_powershell(script)

        exe = pkg_dir / app_id
        if not exe.exists():
            raise FileNotFoundError(f"Exe not found: {exe}")

        self._app_cache.add(
            app_id, version=package_path.stem, path=str(exe.resolve())
        )
        logger.debug(f"Cached package name for app_id={app_id}")
        return True

    def uninstall(self, app_id: str) -> None:
        logger.info(f"Uninstalling app on Windows device {self.device_id}: {app_id}")
        cached = self._app_cache.get(app_id)
        if cached is not None:
            pkg_dir = self._app_dir / cached.version
            if pkg_dir.exists():
                script = f"Remove-Item -Path {self._quote(str(pkg_dir))} -Recurse -Force"
                self._run_powershell(script)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        cached = self._app_cache.get(app_id)
        if cached is None or not cached.path:
            return False
        return Path(cached.path).exists()

    def launch_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        raise NotImplementedError("launch_app is not supported on Windows devices")

    def stop_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        logger.info(f"Stopping app on Windows device {self.device_id}: {app_id}")

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        if not self.is_installed(app_id):
            return None
        return self._app_cache.get(app_id)

    def host_is_running(self) -> bool:
        return False

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
        raise NotImplementedError("swipe is not supported on Windows devices")

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        del local, remote, app_id, documents_only
        raise NotImplementedError("push is not supported on Windows devices")

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        del remote, local, app_id, documents_only
        raise NotImplementedError("pull is not supported on Windows devices")

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        del remote, app_id, recursive
        raise NotImplementedError("ls is not supported on Windows devices")

    def documents_exists(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        raise NotImplementedError(
            "documents_exists is not supported on Windows devices"
        )

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        del app_id, remote
        raise NotImplementedError("documents_ls is not supported on Windows devices")

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        del app_id, remote, local
        raise NotImplementedError(
            "documents_pull is not supported on Windows devices"
        )

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        del app_id, local, remote
        raise NotImplementedError(
            "documents_push is not supported on Windows devices"
        )

    def documents_rm(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        raise NotImplementedError("documents_rm is not supported on Windows devices")
