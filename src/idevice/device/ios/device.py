"""iOS ``DeviceBase`` implementation via go-ios."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from idevice.device.base.device import DeviceBase
from idevice.device.base.errors import AppNotInstalledError, CommandExecutionError, DeviceNotFoundError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache
from idevice.device.config import ios_binary

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice]"

_WDA_PROCESS_MARKERS = (
    "webdriveragent",
    "xctrunner",
)


class IOSDeviceError(RuntimeError):
    """Raised when an iOS device operation fails."""


class IOSDevice(DeviceBase):
    """``DeviceBase`` implementation for iOS using the go-ios CLI."""

    DEFAULT_BINARY = "ios"

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(device_id, device_ip, platform="ios")
        self._binary = self.DEFAULT_BINARY
        self._runner = SubprocessRunner()
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        if shutil.which(self._binary) is None:
            logger.error(f"{_LOG_TAG} `{self._binary}` CLI not found on PATH")
            raise IOSDeviceError(
                f"`{self._binary}` CLI not found on PATH. Install go-ios: https://github.com/danielpaulus/go-ios"
            )

    @classmethod
    def default_udid(cls) -> str:
        """Return the UDID of the first USB-connected iOS device (via ``ios list``)."""
        cmd = [ios_binary(), "list"]
        result = SubprocessRunner().run(cmd)
        logger.debug(f"{_LOG_TAG} ios list output: {result.stdout}")
        try:
            devices = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise IOSDeviceError(
                f"{_LOG_TAG} Unexpected ios list output: {result.stdout[:200]!r}"
            ) from exc
        if not devices:
            raise DeviceNotFoundError(
                f"{_LOG_TAG} No connected iOS device found. Connect a device via USB."
            )
        entry = devices[0]
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            udid = entry.get("udid") or entry.get("UDID") or entry.get("UniqueDeviceID")
            if udid:
                return str(udid)
        raise IOSDeviceError(
            f"{_LOG_TAG} Could not read UDID from device entry: {entry!r}"
        )

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        logger.info(f"{_LOG_TAG} Installing package on iOS device {self.device_id}: {package_path}")
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")
        cmd = [
            self._binary,
            "--udid",
            self.device_id,
            "install",
            f"--path={package_path}",
        ]
        self._runner.run(cmd, timeout=3600)
        if app_id:
            self._app_cache.add(app_id, package_path)
            logger.debug(f"{_LOG_TAG} Cached package name for app_id={app_id}")
        return True

    def uninstall(self, app_id: str) -> None:
        logger.info(f"{_LOG_TAG} Uninstalling {app_id} on {self.device_id}")
        cmd = [self._binary, "--udid", self.device_id, "uninstall", app_id]
        self._runner.run(cmd)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        cmd = [self._binary, "--udid", self.device_id, "apps", "--list"]
        result = self._runner.run(cmd)
        installed = self._bundle_id_in_apps_output(result.stdout, app_id)
        logger.debug(f"{_LOG_TAG} {app_id} installed on {self.device_id}: {installed}")
        return installed

    @staticmethod
    def _bundle_id_in_apps_output(output: str, app_id: str) -> bool:
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == app_id:
                return True
            if stripped.startswith(app_id + " "):
                return True
            if app_id in stripped.split():
                return True
        return False

    def launch_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not self.is_installed(app_id):
            raise AppNotInstalledError(f"App not installed: {app_id}")
        logger.info(f"{_LOG_TAG} Launching app on iOS device {self.device_id}: {app_id}")
        cmd = [self._binary, "--udid", self.device_id, "launch", app_id]
        self._runner.run(cmd)

    def stop_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        logger.info(f"{_LOG_TAG} Stopping app on iOS device {self.device_id}: {app_id}")
        try:
            cmd = [self._binary, "--udid", self.device_id, "kill", app_id]
            self._runner.run(cmd)
        except CommandExecutionError as exc:
            logger.warning(f"{_LOG_TAG} Failed to stop app {app_id} on {self.device_id}: {exc}")
            return False
        return True

    def get_installed_pkg_name(self, app_id: str) -> str | None:
        if not self.is_installed(app_id):
            return None
        cached = self._app_cache.get(app_id)
        return cached.name if cached else None

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
        cmd = [self._binary, "--udid", self.device_id, "ps", "--apps"]
        result = self._runner.run(cmd, check=False)
        if result.returncode != 0:
            logger.debug(
                f"{_LOG_TAG} WDA host check failed on iOS device {self.device_id} (exit code {result.returncode})"
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
        """Push a local file or directory to the device via go-ios ``fsync push``.

        Args:
            local: Path to the local file or directory.
            remote: Destination path on the device.
            app_id: Optional bundle id to target an app's sandbox container.
            documents_only: Ignored on go-ios.

        Raises:
            ValueError: If ``remote`` is empty.
            FileNotFoundError: If ``local`` does not exist.
            IOSDeviceError: If go-ios push fails.
        """
        del documents_only
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        local_path = Path(local)
        if not local_path.exists():
            raise FileNotFoundError(f"Local path not found: {local_path}")
        logger.info(f"{_LOG_TAG} Pushing {local_path} to {self.device_id}:{remote}")
        command = [self._binary, "--udid", self.device_id]
        if app_id:
            command.append(f"--app={app_id}")
        command.extend(
            [
                "fsync",
                "push",
                f"--srcPath={local_path}",
                f"--dstPath={remote}",
            ]
        )
        self._runner.run(command)

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        """Pull a remote file or directory from the device via go-ios ``fsync pull``.

        Args:
            remote: Source path on the device.
            local: Destination path on the host.
            app_id: Optional bundle id to target an app's sandbox container.
            documents_only: Ignored on go-ios.

        Raises:
            ValueError: If ``remote`` is empty.
            IOSDeviceError: If go-ios pull fails.
        """
        del documents_only
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"{_LOG_TAG} Pulling {self.device_id}:{remote} to {local_path}")
        command = [self._binary, "--udid", self.device_id]
        if app_id:
            command.append(f"--app={app_id}")
        command.extend(
            [
                "fsync",
                "pull",
                f"--srcPath={remote}",
                f"--dstPath={local_path.parent}",
            ]
        )
        self._runner.run(command)
        pulled_path = local_path.parent / Path(remote).name
        if pulled_path != local_path and pulled_path.exists():
            pulled_path.rename(local_path)

    @staticmethod
    def _parse_file_ls_output(stdout: str) -> list[str]:
        """Parse ``ios file ls`` JSON (or plain-text fallback)."""
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return [line.strip() for line in stdout.splitlines() if line.strip()]
        if isinstance(payload, dict):
            files = payload.get("files")
            if isinstance(files, list):
                return [str(name) for name in files]
        return []

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        """List a remote directory via go-ios ``file ls``.

        Args:
            remote: Directory path on the device.
            app_id: Optional bundle id to list inside an app container.
            recursive: Not supported on go-ios; raises ``NotImplementedError``.

        Raises:
            ValueError: If ``remote`` is empty.
        """
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        if recursive:
            raise NotImplementedError(
                f"{_LOG_TAG} recursive ls is not supported on go-ios"
            )
        logger.info(f"{_LOG_TAG} Listing {self.device_id}:{remote}")
        command = [self._binary, "--udid", self.device_id]
        if app_id:
            command.append(f"--app={app_id}")
        command.extend(["file", "ls", f"--path={remote}"])
        result = self._runner.run(command)
        return self._parse_file_ls_output(result.stdout)

    def documents_exists(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        raise NotImplementedError(
            f"{_LOG_TAG} documents_exists is not supported on go-ios"
        )

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        del app_id, remote
        raise NotImplementedError(f"{_LOG_TAG} documents_ls is not supported on go-ios")

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        del app_id, remote, local
        raise NotImplementedError(
            f"{_LOG_TAG} documents_pull is not supported on go-ios"
        )

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        del app_id, local, remote
        raise NotImplementedError(
            f"{_LOG_TAG} documents_push is not supported on go-ios"
        )

    def documents_rm(self, app_id: str, remote: str) -> bool:
        del app_id, remote
        raise NotImplementedError(
            f"{_LOG_TAG} documents_rm is not supported on go-ios"
        )
