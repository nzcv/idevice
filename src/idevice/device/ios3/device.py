"""iOS ``DeviceBase`` implementation via the pymobiledevice3 CLI."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pymobiledevice3.services.afc import AfcService

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import AppNotInstalledError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.config import device_id as env_device_id
from idevice.device.config import device_ip as env_device_ip
from idevice.device.config import ios3_binary

logger = logging.getLogger('[IOSDevice3]')

_LOG_TAG = "[IOSDevice3]"

_WDA_PROCESS_MARKERS = (
    "webdriveragent",
    "xctrunner",
)


class IOSDevice3Error(RuntimeError):
    """Raised when an iOS (pymobiledevice3) device operation fails."""


class IOSDevice3(DeviceBase):
    """``DeviceBase`` implementation for iOS using the pymobiledevice3 CLI.

    The pymobiledevice3 ``apps`` service handles install/uninstall/list and
    app-container file transfers, while process control (launch/kill) and
    WebDriverAgent inspection go through the ``developer dvt`` instrumentation
    APIs. Developer-mode commands require a mounted DeveloperDiskImage and, on
    iOS 17+, an active tunnel (pymobiledevice3 retries with ``--tunnel``).
    """

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str,
        package_name: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(
            device_id, device_ip, platform="ios3", package_name=package_name
        )
        self._binary = ios3_binary()
        self._runner = SubprocessRunner()
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        if shutil.which(self._binary) is None and not Path(self._binary).exists():
            logger.error(f"{_LOG_TAG} `{self._binary}` CLI not found")
            raise IOSDevice3Error(
                f"`{self._binary}` CLI not found. Install pymobiledevice3: "
                "https://github.com/doronz88/pymobiledevice3"
            )

    @classmethod
    def from_env(cls) -> IOSDevice3:
        """Build an :class:`IOSDevice3` from the ``GAUTO_*`` environment variables.

        Reads ``GAUTO_DEVICE_UDID`` and ``GAUTO_DEVICE_IP``.

        Returns:
            IOSDevice3: A device bound to the UDID/IP described by the
            environment.
        """
        return cls(env_device_id(), device_ip=env_device_ip())

    def _command(self, *args: str) -> list[str]:
        """Build a pymobiledevice3 command targeting this device's UDID."""
        return [self._binary, *args, "--udid", self.device_id]

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        logger.info(f"{_LOG_TAG} Installing package on iOS device {self.device_id}: {package_path}")
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")
        
        try:
            self.uninstall(app_id)  # Uninstall first to avoid conflicts
        except AppNotInstalledError:
            logger.info(f"{_LOG_TAG} App {app_id} not installed")
        
        cmd = self._command("apps", "install", str(package_path))
        result = self._runner.run(cmd, timeout=3600)
        if result.returncode != 0:
            return False
        if app_id:
            self._app_cache.add(
                app_id, version=package_path.stem, path=None
            )
            logger.debug(f"{_LOG_TAG} Cached package name for app_id={app_id}")
        return True

    def uninstall(self, app_id: str) -> None:
        logger.info(f"{_LOG_TAG} Uninstalling {app_id} on {self.device_id}")
        cmd = self._command("apps", "uninstall", app_id)
        self._runner.run(cmd)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        cmd = self._command("apps", "list", "--type", "User")
        result = self._runner.run(cmd)
        # logger.debug(f"{_LOG_TAG} apps list output: {result.stdout}")
        installed = self._bundle_id_in_apps_output(result.stdout, app_id)
        logger.info(f"{_LOG_TAG} {app_id} installed on {self.device_id}: {installed}")
        return installed

    @staticmethod
    def _bundle_id_in_apps_output(output: str, app_id: str) -> bool:
        """Check whether ``app_id`` appears in ``apps list`` JSON output.

        ``pymobiledevice3 apps list`` prints a JSON object keyed by bundle id.
        Falls back to a substring scan if the payload is not valid JSON.
        """
        try:
            apps = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return app_id in output
        if isinstance(apps, dict):
            return app_id in apps
        if isinstance(apps, list):
            return any(
                isinstance(entry, dict) and entry.get("CFBundleIdentifier") == app_id for entry in apps
            )
        return False

    def launch_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not self.is_installed(app_id):
            raise AppNotInstalledError(f"App not installed: {app_id}")
        logger.info(f"{_LOG_TAG} Launching app on iOS device {self.device_id}: {app_id}")
        cmd = self._command("developer", "dvt", "launch", app_id)
        self._runner.run(cmd)
    
    def stop_app(self, app_id: str | None = None) -> None:
        target = self._resolve_app_id(app_id)
        try:
            logger.info(
                f"{_LOG_TAG} Stopping app on iOS device {self.device_id}: {target}"
            )
            cmd = self._command("developer", "dvt", "pkill", "--bundle", target)
            self._runner.run(cmd)
        except Exception as e:
            logger.error(
                f"{_LOG_TAG} Failed to stop app {target} on {self.device_id}: {e}"
            )

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        if not self.is_installed(app_id):
            logger.debug(f"{_LOG_TAG} App {app_id} not installed on {self.device_id}")
            return None
        cached = self._app_cache.get(app_id)
        logger.debug(f"{_LOG_TAG} Cached app info for app_id={app_id}: {cached}")
        return cached

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
        raise NotImplementedError(
            f"{_LOG_TAG} swipe is not supported on iOS yet; use WebDriverAgent for touch input"
        )

    def host_is_running(self) -> bool:
        cmd = self._command("developer", "dvt", "proclist")
        result = self._runner.run(cmd, check=False)
        if result.returncode != 0:
            logger.debug(
                f"{_LOG_TAG} WDA host check failed on iOS device {self.device_id} "
                f"(exit code {result.returncode})"
            )
            return False
        output = result.stdout.lower()
        running = any(marker in output for marker in _WDA_PROCESS_MARKERS)
        logger.debug(f"{_LOG_TAG} WDA host running on iOS device {self.device_id}: {running}")
        return running

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        """Push a local file to the device via pymobiledevice3.

        When ``app_id`` is given the file is written into that app's sandbox
        container (``apps push``); otherwise it goes to the public media
        directory (``afc push`` under ``/var/mobile/Media``).

        Args:
            local: Path to the local file.
            remote: Destination path on the device.
            app_id: Optional bundle id to target an app's sandbox container.
            documents_only: When ``app_id`` is set, use ``apps push --documents``
                (Documents directory only; typical for ``UIFileSharingEnabled`` apps).

        Raises:
            ValueError: If ``remote`` is empty.
            FileNotFoundError: If ``local`` does not exist.
        """
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        local_path = Path(local)
        if not local_path.exists():
            raise FileNotFoundError(f"Local path not found: {local_path}")
        logger.info(f"{_LOG_TAG} Pushing {local_path} to {self.device_id}:{remote}")
        if app_id:
            cmd = self._command("apps", "push", app_id, str(local_path), remote)
            if documents_only:
                cmd.append("--documents")
        else:
            cmd = self._command("afc", "push", str(local_path), remote)
        self._runner.run(cmd)

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        """Pull a remote file from the device via pymobiledevice3.

        When ``app_id`` is given the file is read from that app's sandbox
        container (``apps pull``); otherwise it is read from the public media
        directory (``afc pull`` under ``/var/mobile/Media``).

        Args:
            remote: Source path on the device.
            local: Destination path on the host.
            app_id: Optional bundle id to target an app's sandbox container.
            documents_only: When ``app_id`` is set, use ``apps pull --documents``.

        Raises:
            ValueError: If ``remote`` is empty.
        """
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"{_LOG_TAG} Pulling {self.device_id}:{remote} to {local_path}")
        if app_id:
            cmd = self._command("apps", "pull", app_id, remote, str(local_path))
            if documents_only:
                cmd.append("--documents")
        else:
            cmd = self._command("afc", "pull", remote, str(local_path), "--ignore-errors")
        self._runner.run(cmd)

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        """List a remote directory via pymobiledevice3 ``afc ls``.

        Uses the public media AFC service (``/var/mobile/Media``). App sandbox
        listing is not exposed by the pymobiledevice3 CLI.

        Args:
            remote: Directory path on the device (e.g. ``/Documents``).
            app_id: Not supported; raises ``NotImplementedError`` when set.
            recursive: Pass ``-r`` to recurse into subdirectories.

        Raises:
            ValueError: If ``remote`` is empty.
            NotImplementedError: When ``app_id`` is set.
        """
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        if app_id:
            raise NotImplementedError(
                f"{_LOG_TAG} ls with app_id is not supported on iOS3; "
                "use AFC paths (e.g. /Documents) for file-sharing apps"
            )
        logger.info(f"{_LOG_TAG} Listing {self.device_id}:{remote}")
        cmd = self._command("afc", "ls", remote)
        if recursive:
            cmd.append("-r")
        result = self._runner.run(cmd)
        return [line for line in result.stdout.splitlines() if line.strip()]

    @asynccontextmanager
    async def _house_arrest_afc(
        self, app_id: str, *, documents_only: bool
    ) -> AsyncIterator[AfcService]:
        """Open a House Arrest AFC session on an app container."""
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.house_arrest import HouseArrestService
        from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS, get_tunneld_devices

        rsds = await get_tunneld_devices(TUNNELD_DEFAULT_ADDRESS)
        if rsds:
            lockdown = next((r for r in rsds if r.udid == self.device_id), None)
            if lockdown is None:
                available = ", ".join(r.udid for r in rsds)
                raise IOSDevice3Error(
                    f"{_LOG_TAG} Device {self.device_id!r} not found in tunneld "
                    f"(available: {available}). Connect the device or start a tunnel "
                    f"for this UDID."
                )
        else:
            lockdown = await create_using_usbmux(serial=self.device_id)
        try:
            async with await HouseArrestService.create(
                lockdown, app_id, documents_only=documents_only
            ) as afc:
                yield afc
        finally:
            close = getattr(lockdown, "close", None)
            if close is not None:
                await close()

    @asynccontextmanager
    async def _documents_afc(self, app_id: str) -> AsyncIterator[AfcService]:
        """Open an AFC session on an app's Documents directory (House Arrest)."""
        async with self._house_arrest_afc(app_id, documents_only=True) as afc:
            yield afc

    @staticmethod
    def _afc_relative_path(remote: str) -> str:
        """Normalize ``remote`` to an AFC path under the container root."""
        rel = remote.strip().replace("\\", "/").lstrip("/")
        if not rel or rel == ".":
            raise ValueError("remote is required and must be a non-empty string")
        parts = [part for part in rel.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError(f"remote path must not contain '..': {remote}")
        return "/" + "/".join(parts)

    def documents_exists(self, app_id: str, remote: str) -> bool:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        import asyncio
        remote = f'/Documents/{remote}'
        async def main() -> bool:
            async with self._documents_afc(app_id) as afc:
                return await afc.exists(remote)

        return asyncio.run(main())

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entries under ``remote`` in an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        import asyncio
        remote = f'/Documents/{remote}'
        async def main() -> list[str]:
            entries: list[str] = []
            async with self._documents_afc(app_id) as afc:
                async for path in afc.dirlist(remote, 1):
                    entries.append(path)
            return entries

        return asyncio.run(main())

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        import asyncio
        remote = f'/Documents/{remote}'
        async def main() -> bool:
            async with self._documents_afc(app_id) as afc:
                if not await afc.exists(remote):
                    return False
                await afc.pull(remote, local)
                return True

        return asyncio.run(main())

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        """Push a local file or directory into an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        import asyncio
        import os
        import posixpath
        remote = f'/Documents/{remote}'
        async def main() -> bool:
            async with self._documents_afc(app_id) as afc:
                if not os.path.exists(local):
                    return False
                # pymobiledevice3 only auto-creates parent directories when the
                # source is a directory. For single-file pushes it requires the
                # remote parent to already exist, otherwise it re-raises
                # AfcFileNotFoundError. Ensure the parent directory exists first.
                if os.path.isfile(local):
                    remote_parent = posixpath.dirname(remote)
                    if remote_parent:
                        await afc.makedirs(remote_parent)
                await afc.push(local, remote)
                return True

        return asyncio.run(main())

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Remove a file or directory from an app's Documents sandbox."""
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        import asyncio
        remote = f'/Documents/{remote}'
        async def main() -> bool:
            async with self._documents_afc(app_id) as afc:
                undeleted = await afc.rm(remote, force=True)
                return len(undeleted) == 0

        return asyncio.run(main())

    def screenshot(self, local: Path | str) -> bool:
        """Capture the screen via ``pymobiledevice3 developer dvt screenshot``.

        Requires developer setup (mounted DDI and, on iOS 17+, an active tunnel).
        """
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"{_LOG_TAG} Capturing screenshot on {self.device_id} to {local_path}")
        cmd = self._command("developer", "dvt", "screenshot", str(local_path))
        result = self._runner.run(cmd, check=False)
        return result.returncode == 0 and local_path.exists()

    @classmethod
    def default_udid(cls) -> str:
        """Return the UDID of the first USB-connected iOS device."""
        cmd = [ios3_binary(), "usbmux", "list"]
        result = SubprocessRunner().run(cmd)
        logger.debug(f"{_LOG_TAG} usbmux list output: {result.stdout}")
        try:
            devices = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise IOSDevice3Error(
                f"{_LOG_TAG} Unexpected usbmux list output: {result.stdout[:200]!r}"
            ) from exc
        if not devices:
            raise IOSDevice3Error(
                f"{_LOG_TAG} No connected iOS device found. Connect a device via USB."
            )
        device = devices[0]
        udid = device.get("UniqueDeviceID") or device.get("Identifier")
        if not udid:
            raise IOSDevice3Error(
                f"{_LOG_TAG} Could not read UDID from device entry: {device!r}"
            )
        return str(udid)

    def pull2(self, data_path: AppDataPath, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from Local or Persistent app data.

        Persistent uses the Documents sandbox (House Arrest ``documents_only``).
        Local uses the full app container so callers can reach paths such as
        ``Library/...`` relative to the container root.
        """
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        app_id = self._resolve_app_id(None)
        if data_path == AppDataPath.Persistent:
            return self.documents_pull(app_id, remote, local)
        if data_path == AppDataPath.Local:
            return self._container_pull(app_id, remote, local)
        raise ValueError(f"Invalid data path: {data_path}")

    def _container_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull ``remote`` from the full app container (not Documents-only)."""
        import asyncio

        afc_path = self._afc_relative_path(remote)

        async def main() -> bool:
            async with self._house_arrest_afc(app_id, documents_only=False) as afc:
                if not await afc.exists(afc_path):
                    logger.warning(
                        f"{_LOG_TAG} Remote path not found: {self.device_id}:{afc_path}"
                    )
                    return False
                await afc.pull(afc_path, local)
                return True

        return asyncio.run(main())