"""IOSDevice5 facade with devicectl-first, ios4 fallback routing."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import CommandExecutionError, DeviceNotFoundError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.common.ios4cli import IOS4CLI, IOS4CLIError
from idevice.device.common.iwda2 import IWDA2Mixin
from idevice.device.common.xcruncli import (
    DevicectlOutcome,
    IOSDevice5Error,
    XcrunCLI,
)
from idevice.device.config import device_id as env_device_id
from idevice.device.config import device_ip as env_device_ip
from idevice.device.config import ios4_binary, xcrun_binary
from idevice.device.config import package_name as env_package_name

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice5]"
_DEVICETCL_FAILURES = (IOSDevice5Error, CommandExecutionError)


class IOSDevice5(IWDA2Mixin, DeviceBase):
    """Route iOS operations through devicectl first and ios4 second.

    Operations implemented by CoreDevice always try :class:`XcrunCLI` first.
    A devicectl transport/command failure is retried through :class:`IOS4CLI`.
    Operations CoreDevice does not implement are sent directly to IOS4CLI.
    """

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        package_name: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        if sys.platform != "darwin":
            raise IOSDevice5Error(
                f"{_LOG_TAG} devicectl is only available on macOS with Xcode "
                f"installed; this host is {sys.platform}"
            )
        configured_xcrun = xcrun_binary()
        if XcrunCLI.resolve_binary(configured_xcrun) is None:
            logger.error(f"{_LOG_TAG} `{configured_xcrun}` CLI not found")
            raise IOSDevice5Error(
                f"`{configured_xcrun}` CLI not found. Install Xcode, or set "
                "IDEVICE_XCRUN_BINARY."
            )
        super().__init__(
            device_id,
            device_ip,
            platform="ios5",
            package_name=package_name,
        )
        runner = SubprocessRunner()
        self._xcruncli = XcrunCLI(
            device_id,
            binary=configured_xcrun,
            runner=runner,
            package_name=package_name,
        )
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        self._ios4cli = IOS4CLI(
            device_id,
            binary=ios4_binary(),
            runner=runner,
        )

    @property
    def last_launch_pid(self) -> int | None:
        """Return the PID from the most recent successful launch."""
        return self._xcruncli.last_launch_pid

    @classmethod
    def from_env(cls) -> IOSDevice5:
        """Build an IOSDevice5 from the ``GAUTO_*`` environment."""
        return cls(
            env_device_id(),
            device_ip=env_device_ip(),
            package_name=env_package_name(),
        )

    @classmethod
    def default_udid(cls) -> str:
        """Return the first wired device, falling back to ios4 listing."""
        try:
            return XcrunCLI.default_udid(
                binary=xcrun_binary(), runner=SubprocessRunner()
            )
        except DeviceNotFoundError as xcrun_error:
            try:
                return IOS4CLI.default_udid()
            except Exception as ios4_error:
                raise DeviceNotFoundError(
                    f"{_LOG_TAG} No USB-attached device was found via devicectl "
                    f"({xcrun_error}) or ios4 ({ios4_error})"
                ) from ios4_error

    def _log_fallback(self, operation: str, error: BaseException) -> None:
        logger.warning(
            f"{_LOG_TAG} devicectl {operation} failed: {error}; "
            "falling back to ios4"
        )

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        package_path = Path(package_path)
        resolved_app_id = app_id or ""
        installation_path: str | None = None
        try:
            record = self._xcruncli.install(package_path)
            installed = True
            resolved_app_id = resolved_app_id or str(record.get("bundleID") or "")
            reported_path = record.get("installationURL")
            installation_path = (
                reported_path if isinstance(reported_path, str) else None
            )
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("install", exc)
            installed = self._ios4cli.install(package_path)

        if installed and resolved_app_id:
            self._app_cache.add(
                resolved_app_id,
                version=package_path.stem,
                path=installation_path,
            )
        return installed

    def uninstall(self, app_id: str) -> None:
        try:
            self._xcruncli.uninstall(app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("uninstall", exc)
            self._ios4cli.uninstall(app_id)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        try:
            return self._xcruncli.is_installed(app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("is_installed", exc)
            return self._ios4cli.is_installed(app_id)

    def launch(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        terminate_existing: bool = True,
        activate: bool = True,
    ) -> None:
        self._launch_with_fallback(
            app_id,
            args=args,
            environment=environment,
            terminate_existing=terminate_existing,
            activate=activate,
            check_installed=False,
        )

    def launch_app(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        terminate_existing: bool = True,
        activate: bool = True,
    ) -> None:
        self._launch_with_fallback(
            app_id,
            args=args,
            environment=environment or None,
            terminate_existing=terminate_existing,
            activate=activate,
            check_installed=True,
        )

    def _launch_with_fallback(
        self,
        app_id: str | None,
        *,
        args: list[str] | None,
        environment: dict[str, str] | None,
        terminate_existing: bool,
        activate: bool,
        check_installed: bool,
    ) -> None:
        operation = "launch_app" if check_installed else "launch"
        launch = (
            self._xcruncli.launch_app
            if check_installed
            else self._xcruncli.launch
        )
        try:
            launch(
                app_id,
                args=args,
                environment=environment,
                terminate_existing=terminate_existing,
                activate=activate,
            )
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback(operation, exc)
            self._ios4cli.launch_app(
                self._resolve_app_id(app_id), args=args, environment=environment
            )
            self._sync_ios4_launch()

    def _sync_ios4_launch(self) -> None:
        self._xcruncli.last_launch_pid = self._ios4cli.last_launch_pid
        self._xcruncli.last_launch_app_id = self._ios4cli.last_launch_app_id

    def stop_app(self, app_id: str | None = None) -> None:
        try:
            self._xcruncli.stop_app(app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("stop_app", exc)
            self._ios4cli.stop_app(self._resolve_app_id(app_id))
            self._sync_ios4_launch()

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        try:
            installed = self._xcruncli.is_installed(app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("get_installed_pkg_name", exc)
            installed = self._ios4cli.is_installed(app_id)
        return self._app_cache.get(app_id) if installed else None

    def screenshot(self, local: Path | str) -> bool:
        local_path = Path(local)
        if self._xcruncli.screenshot(local_path):
            return True
        return self._ios4cli.screenshot(local_path)

    def capture_memgraph(
        self, output: Path | str, *, pid: int | None = None
    ) -> Path:
        """Route the CoreDevice-unsupported memory graph directly to ios4."""
        target_pid = self.last_launch_pid if pid is None else pid
        try:
            return self._ios4cli.capture_memgraph(output, pid=target_pid)
        except IOS4CLIError as exc:
            raise IOSDevice5Error(str(exc)) from exc

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
        remove_existing_content: bool = False,
    ) -> None:
        try:
            self._xcruncli.push(
                local,
                remote,
                app_id=app_id,
                documents_only=documents_only,
                remove_existing_content=remove_existing_content,
            )
            return
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("push", exc)
            if not documents_only:
                self._ios4cli.push(
                    local, remote, app_id=app_id, documents_only=False
                )
                return
            target = self._resolve_app_id(app_id)
            if not self._ios4cli.documents_push(target, local, remote):
                raise IOS4CLIError(f"ios4 push failed for {target}:{remote}")

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        try:
            self._xcruncli.pull(
                remote,
                local,
                app_id=app_id,
                documents_only=documents_only,
            )
            return
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("pull", exc)
            if not documents_only:
                self._ios4cli.pull(
                    remote, local, app_id=app_id, documents_only=False
                )
                return
            target = self._resolve_app_id(app_id)
            if not self._ios4cli.documents_pull(target, remote, local):
                raise IOS4CLIError(f"ios4 pull failed for {target}:{remote}")

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
        documents_only: bool = True,
    ) -> list[str]:
        try:
            return self._xcruncli.ls(
                remote,
                app_id=app_id,
                recursive=recursive,
                documents_only=documents_only,
            )
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("ls", exc)
            if not documents_only or recursive:
                return self._ios4cli.ls(
                    remote, app_id=app_id, recursive=recursive
                )
            return self._ios4cli.documents_ls(
                self._resolve_app_id(app_id), remote
            )

    def documents_exists(self, app_id: str, remote: str) -> bool:
        try:
            return self._xcruncli.documents_exists(app_id, remote)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("documents_exists", exc)
            return self._ios4cli.documents_exists(app_id, remote)

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        try:
            return self._xcruncli.documents_ls(app_id, remote)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("documents_ls", exc)
            return self._ios4cli.documents_ls(app_id, remote)

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        try:
            self._xcruncli.pull(
                remote, local, app_id=app_id, documents_only=True
            )
            return True
        except (FileNotFoundError, *_DEVICETCL_FAILURES) as exc:
            self._log_fallback("documents_pull", exc)
            return self._ios4cli.documents_pull(app_id, remote, local)

    def documents_push(
        self,
        app_id: str,
        local: Path | str,
        remote: str,
        *,
        remove_existing_content: bool = False,
    ) -> bool:
        try:
            self._xcruncli.push(
                local,
                remote,
                app_id=app_id,
                documents_only=True,
                remove_existing_content=remove_existing_content,
            )
            return True
        except (FileNotFoundError, *_DEVICETCL_FAILURES) as exc:
            self._log_fallback("documents_push", exc)
            return self._ios4cli.documents_push(app_id, local, remote)

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Route CoreDevice-unsupported removal directly to ios4."""
        return self._ios4cli.documents_rm(app_id, remote)

    def pull2(self, data_path: AppDataPath, remote: str, local: Path | str) -> bool:
        if not isinstance(data_path, AppDataPath):
            raise ValueError(f"Invalid data_path: {data_path!r}")
        try:
            self._xcruncli.pull(
                remote,
                local,
                documents_only=data_path is AppDataPath.Persistent,
            )
            return True
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("pull2", exc)
            return self._ios4cli.pull2(data_path, remote, local)

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        return self._ios4cli.delete2(data_path, remote)


__all__ = ["DevicectlOutcome", "IOSDevice5", "IOSDevice5Error"]
