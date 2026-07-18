"""Windows ``DeviceBase`` implementation via PowerShell AppX cmdlets."""

from __future__ import annotations

import logging
import os
import platform
import shutil
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
        company_name: str,
        package_name: str,
        cache_dir: Path | None = None,
    ) -> None:
        if not company_name:
            raise ValueError("company_name is required and must be a non-empty string")
        if not package_name:
            raise ValueError("package_name is required and must be a non-empty string")
        super().__init__(
            device_id, device_ip, platform="windows", package_name=package_name
        )
        self._runner = SubprocessRunner()
        self._company_name = company_name
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        _app_dir = os.environ.get("IDEVICE_APP_DIR", "D:\\IDeviceExtractedApps")
        self._app_dir = Path(_app_dir)
        self._doc_dir = self._documents_root()

    @classmethod
    def default_udid(cls) -> str:
        """Return the local host name as the default Windows device id."""
        return platform.node()

    def _run_powershell(self, script: str, timeout: int = 20 * 60) -> str:
        command = [
            powershell_binary(),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"$ProgressPreference='SilentlyContinue'; {script}",
        ]
        result = self._runner.run(command, timeout=timeout)
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
        cached = self._app_cache.get(app_id)
        if cached is None or not cached.path:
            raise FileNotFoundError(f"App is not installed: {app_id}")
        exe = Path(cached.path)
        if not exe.exists():
            raise FileNotFoundError(f"Exe not found: {exe}")
        logger.info(f"Launching app on Windows device {self.device_id}: {exe}")
        # Start-Process returns immediately, so the app runs detached instead of
        # blocking the runner until the process exits.
        script = (
            f"Start-Process -FilePath {self._quote(str(exe))} "
            f"-WorkingDirectory {self._quote(str(exe.parent))}"
        )
        self._run_powershell(script)

    def stop_app(self, app_id: str | None = None) -> None:
        target = self._resolve_app_id(app_id)
        process_name = Path(target).stem
        logger.info(f"Stopping app on Windows device {self.device_id}: {target}")
        script = (
            f"Stop-Process -Name {self._quote(process_name)} "
            f"-Force -ErrorAction SilentlyContinue"
        )
        self._run_powershell(script)

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

    def _documents_root(self) -> Path:
        """Return the app's LocalAppData documents directory."""
        return (
            Path.home()
            / "AppData"
            / "LocalLow"
            / self._company_name
            / Path(self._package_name).stem
        )

    @staticmethod
    def _require_app_and_remote(app_id: str, remote: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")

    def _documents_path(self, remote: str) -> Path:
        """Resolve ``remote`` (relative to the Documents root) to a local path.

        The Windows Documents sandbox is fixed at construction time (derived from
        ``company_name`` / ``package_name``), so ``remote`` is always interpreted
        relative to :attr:`_doc_dir`. Leading path separators are stripped so an
        absolute-looking ``remote`` never escapes the sandbox.
        """
        rel = remote.strip().replace("\\", "/").lstrip("/")
        if not rel or rel == ".":
            return self._doc_dir
        return self._doc_dir / rel

    def documents_exists(self, app_id: str, remote: str) -> bool:
        """Check whether ``remote`` (file or directory) exists in the sandbox."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        exists = path.exists()
        logger.debug(f"{path} exists: {exists}")
        return exists

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entry names under ``remote`` in the sandbox.

        When ``remote`` points to a file, the file's own name is returned so the
        behaviour matches shell ``ls`` on both files and directories.
        """
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        if not path.exists():
            raise FileNotFoundError(f"Remote path not found: {path}")
        logger.info(f"Listing {self.device_id}:{path}")
        if path.is_dir():
            return sorted(entry.name for entry in path.iterdir())
        return [path.name]

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from the sandbox to ``local``."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        if not path.exists():
            logger.warning(f"Remote path not found: {self.device_id}:{path}")
            return False
        local_path = Path(local)
        logger.info(f"Pulling {self.device_id}:{path} to {local_path}")
        try:
            if path.is_dir():
                dest = local_path / path.name if local_path.is_dir() else local_path
                shutil.copytree(path, dest, dirs_exist_ok=True)
            else:
                if local_path.is_dir():
                    dest = local_path / path.name
                else:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    dest = local_path
                shutil.copy2(path, dest)
        except OSError as exc:
            logger.error(f"Failed to pull {path} to {local_path}: {exc}")
            return False
        return True

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        """Push a local file or directory into the sandbox at ``remote``."""
        self._require_app_and_remote(app_id, remote)
        local_path = Path(local)
        if not local_path.exists():
            logger.warning(f"Local path not found: {local_path}")
            return False
        dest = self._documents_path(remote)
        logger.info(f"Pushing {local_path} to {self.device_id}:{dest}")
        try:
            if local_path.is_dir():
                target = dest / local_path.name if dest.is_dir() else dest
                shutil.copytree(local_path, target, dirs_exist_ok=True)
            else:
                if dest.is_dir():
                    target = dest / local_path.name
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    target = dest
                shutil.copy2(local_path, target)
        except OSError as exc:
            logger.error(f"Failed to push {local_path} to {dest}: {exc}")
            return False
        return True

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Remove a file or directory from the sandbox."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(remote)
        if not path.exists():
            logger.warning(f"Remote path not found: {self.device_id}:{path}")
            return False
        logger.info(f"Removing {self.device_id}:{path}")
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            logger.error(f"Failed to remove {path}: {exc}")
            return False
        return True

    def screenshot(self, local: Path | str) -> bool:
        """Capture the host primary screen via PIL ``ImageGrab``."""
        from PIL import ImageGrab

        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Capturing screenshot on {self.device_id} to {local_path}")
        try:
            image = ImageGrab.grab()
            image.save(local_path)
        except OSError as exc:
            logger.error(f"Failed to capture screenshot to {local_path}: {exc}")
            return False
        return local_path.exists()
