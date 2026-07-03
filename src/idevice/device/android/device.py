"""Android ``DeviceBase`` implementation via adb."""

from __future__ import annotations

import logging
import os
import posixpath
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from idevice.device.base.device import DeviceBase
from idevice.device.base.errors import AppNotInstalledError, DeviceNotFoundError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.config import adb_binary

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str


class AndroidDeviceError(RuntimeError):
    """Raised when an Android device operation fails."""


class AndroidDevice(DeviceBase):
    """``DeviceBase`` implementation for Android using adb."""

    DEFAULT_BINARY = "adb"

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(device_id, device_ip, platform="android")
        self._binary = self.DEFAULT_BINARY
        self._runner = SubprocessRunner()
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        if shutil.which(self._binary) is None:
            logger.error(f"[AndroidDevice] `{self._binary}` CLI not found on PATH")
            raise AndroidDeviceError(
                f"`{self._binary}` CLI not found on PATH. "
                "Install adb: https://developer.android.com/studio/releases/platform-tools"
            )

    @classmethod
    def default_udid(cls) -> str:
        """Return the serial of the first ``adb devices`` entry in ``device`` state."""
        result = SubprocessRunner().run([adb_binary(), "devices"])
        logger.debug(f"[AndroidDevice] adb devices output: {result.stdout}")
        serials: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        if not serials:
            raise DeviceNotFoundError(
                "[AndroidDevice] No connected Android device found. "
                "Connect a device or pass an explicit serial."
            )
        return serials[0]

    def _base_command(self) -> list[str]:
        return [adb_binary(), "-s", self.device_id]

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        """Install an APK on the bound device via uiautomator2.

        Example::

            from pathlib import Path

            from idevice.device.android.device import AndroidDevice

            device = AndroidDevice("e8b2b043")
            apk = Path("tests/apk/app.apk")
            device.install(apk, app_id="com.example.app")
            assert device.is_installed("com.example.app")
        """
        logger.info(f"[AndroidDevice] Installing package on {self.device_id}: {package_path}")
        if not package_path.exists():
            raise FileNotFoundError(f"Package not found: {package_path}")
        
        # Existing package com.hypergryph.beyondtest signatures do not match newer version
        try:
            self.uninstall(app_id)
        except Exception as exc:
            logger.info(f"Failed to uninstall existing package: {exc}")

        try:
            cmd = self._base_command()
            cmd.extend(["install", "-r", str(package_path)])
            result = self._install_with_uiautomator2(cmd, device_id=self.device_id)
            if not result.ok:
                raise AndroidDeviceError(f"Package install failed on {self.device_id}: {result.stderr}")
        except Exception as exc:
            raise AndroidDeviceError(f"Package install failed on {self.device_id}: {exc}") from exc

        if app_id:
            self._app_cache.add(
                app_id, version=package_path.stem, path=None
            )
            logger.debug(f"[AndroidDevice] Cached package name for app_id={app_id}")
        return True

    def uninstall(self, app_id: str) -> None:
        logger.info(f"[AndroidDevice] Uninstalling {app_id} on {self.device_id}")
        command = self._base_command()
        command.extend(["uninstall", app_id])
        self._runner.run(command)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        command = self._base_command()
        command.extend(["shell", "pm", "list", "packages", app_id])
        result = self._runner.run(command)
        prefix = f"package:{app_id}"
        installed = any(line.strip() == prefix for line in result.stdout.splitlines())
        logger.debug(f"App {app_id} installed on Android device {self.device_id}: {installed}")
        return installed

    def launch_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not self.is_installed(app_id):
            raise AppNotInstalledError(f"App not installed: {app_id}")
        logger.info(f"[AndroidDevice] Launching {app_id} on {self.device_id}")
        command = self._base_command()
        command.extend(
            [
                "shell",
                "monkey",
                "-p",
                app_id,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]
        )
        self._runner.run(command)

    def stop_app(self, app_id: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        logger.info(f"Stopping app on Android device {self.device_id}: {app_id}")
        command = self._base_command()
        command.extend(["shell", "am", "force-stop", app_id])
        self._runner.run(command)

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        if not self.is_installed(app_id):
            return None
        return self._app_cache.get(app_id)

    def host_is_running(self) -> bool:
        return True

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
    ) -> None:
        """Swipe on the device via ``adb shell input swipe``."""
        if duration_ms <= 0:
            raise ValueError("duration_ms must be a positive integer")
        logger.info(
            f"[AndroidDevice] Swiping on {self.device_id} "
            f"from ({x1}, {y1}) to ({x2}, {y2}) over {duration_ms}ms"
        )
        command = self._base_command()
        command.extend(
            [
                "shell",
                "input",
                "swipe",
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                str(duration_ms),
            ]
        )
        self._runner.run(command)

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        """Push a local file or directory to the device via ``adb push``."""
        del app_id, documents_only
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        local_path = Path(local)
        if not local_path.exists():
            raise FileNotFoundError(f"Local path not found: {local_path}")
        logger.info(f"[AndroidDevice] Pushing {local_path} to {self.device_id}:{remote}")
        command = self._base_command()
        command.extend(["push", str(local_path), remote])
        self._runner.run(command)

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        """Pull a remote file or directory from the device via ``adb pull``."""
        del app_id, documents_only
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[AndroidDevice] Pulling {self.device_id}:{remote} to {local_path}")
        command = self._base_command()
        command.extend(["pull", remote, str(local_path)])
        self._runner.run(command)

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        """List a remote directory via ``adb shell ls`` or ``find``."""
        del app_id
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")
        logger.info(f"[AndroidDevice] Listing {self.device_id}:{remote}")
        command = self._base_command()
        if recursive:
            command.extend(["shell", "find", remote])
        else:
            command.extend(["shell", "ls", "-1", remote])
        result = self._runner.run(command)
        return [line for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def documents_root(app_id: str) -> str:
        """Return an app's external files directory on shared storage.

        Android exposes each app's externally readable files under
        ``/sdcard/Android/data/<app_id>/files``. The ``documents_*`` helpers
        treat this directory as the app's Documents sandbox so the API matches
        the iOS file-sharing semantics.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        return f"/sdcard/Android/data/{app_id}/files"

    @classmethod
    def _documents_path(cls, app_id: str, remote: str) -> str:
        """Resolve ``remote`` (relative to the Documents root) to a device path."""
        root = cls.documents_root(app_id)
        rel = remote.strip().lstrip("/")
        if not rel or rel == ".":
            return root
        return f"{root}/{rel}"

    @staticmethod
    def _shell_quote(path: str) -> str:
        """Single-quote a path so the on-device shell treats it as one token."""
        return "'" + path.replace("'", "'\\''") + "'"

    @staticmethod
    def _require_app_and_remote(app_id: str, remote: str) -> None:
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        if not remote:
            raise ValueError("remote is required and must be a non-empty string")

    def documents_exists(self, app_id: str, remote: str) -> bool:
        """Check whether ``remote`` exists under the app's external files dir."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(app_id, remote)
        logger.info(f"[AndroidDevice] Checking existence of {self.device_id}:{path}")
        command = self._base_command()
        command.extend(["shell", "test", "-e", self._shell_quote(path)])
        result = self._runner.run(command, check=False)
        exists = result.returncode == 0
        logger.debug(f"[AndroidDevice] {path} exists: {exists}")
        return exists

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entries under ``remote`` in the app's external files dir."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(app_id, remote)
        logger.info(f"[AndroidDevice] Listing {self.device_id}:{path}")
        command = self._base_command()
        command.extend(["shell", "ls", "-1", self._shell_quote(path)])
        result = self._runner.run(command)
        return [line for line in result.stdout.splitlines() if line.strip()]

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from the app's external files dir."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(app_id, remote)
        if not self.documents_exists(app_id, remote):
            logger.warning(f"[AndroidDevice] Remote path not found: {self.device_id}:{path}")
            return False
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[AndroidDevice] Pulling {self.device_id}:{path} to {local_path}")
        command = self._base_command()
        command.extend(["pull", path, str(local_path)])
        result = self._runner.run(command, check=False)
        return result.returncode == 0

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        """Push a local file or directory into the app's external files dir."""
        self._require_app_and_remote(app_id, remote)
        local_path = Path(local)
        if not local_path.exists():
            logger.warning(f"[AndroidDevice] Local path not found: {local_path}")
            return False
        path = self._documents_path(app_id, remote)
        parent = posixpath.dirname(path)
        if parent:
            mkdir_cmd = self._base_command()
            mkdir_cmd.extend(["shell", "mkdir", "-p", self._shell_quote(parent)])
            self._runner.run(mkdir_cmd, check=False)
        logger.info(f"[AndroidDevice] Pushing {local_path} to {self.device_id}:{path}")
        command = self._base_command()
        command.extend(["push", str(local_path), path])
        result = self._runner.run(command, check=False)
        return result.returncode == 0

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Remove a file or directory from the app's external files dir."""
        self._require_app_and_remote(app_id, remote)
        path = self._documents_path(app_id, remote)
        logger.info(f"[AndroidDevice] Removing {self.device_id}:{path}")
        command = self._base_command()
        command.extend(["shell", "rm", "-rf", self._shell_quote(path)])
        result = self._runner.run(command, check=False)
        return result.returncode == 0

    def _install_with_uiautomator2(self, cmd: list[str], *, device_id: str | None) -> InstallResult:
        """Use uiautomator2 WatchContext (builtin + extra) while adb install runs."""
        import uiautomator2 as u2

        logger.info(f"install with uiautomator2: {cmd}")
        d = u2.connect(device_id) if device_id else u2.connect()
        # autostart=False so we can register rules before the background thread runs.
        with d.watch_context(builtin=True, autostart=False) as ctx:
            # Builtin rules cover common install prompts (继续安装, ALLOW, Agree, …).
            ctx.when("仍要安装").click()
            ctx.when("Install").click()
            ctx.start()
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if p.returncode == 0:
            self._dismiss_post_install_popups(d)

        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        ok = p.returncode == 0
        return InstallResult(ok, p.returncode, out, err)

    def _dismiss_post_install_popups(self, d) -> None:
        """Dismiss OEM dialogs after adb install; watcher already stopped."""
        stable = float(os.environ.get("GAUTO_APK_POST_INSTALL_STABLE_SEC", "2"))
        timeout_sec = float(os.environ.get("GAUTO_APK_POST_INSTALL_TIMEOUT_SEC", "30"))
        with d.watch_context(builtin=True, autostart=False) as ctx:
            # Prefer dismiss over launching the app when both exist.
            ctx.when("完成").click()
            ctx.when("完成安装").click()
            ctx.when("知道了").click()
            ctx.when("我知道了").click()
            ctx.when("以后再说").click()
            ctx.when("稍后").click()
            ctx.when("暂不").click()
            ctx.when("跳过").click()
            ctx.when("关闭").click()
            ctx.when("Done").click()
            ctx.when("OPEN").click()
            ctx.when("打开").click()
            ctx.when("立即打开").click()
            ctx.start()
            try:
                ctx.wait_stable(seconds=stable, timeout=timeout_sec)
            except TimeoutError:
                logger.debug(f"post-install popups did not stabilize within {timeout_sec}s")
