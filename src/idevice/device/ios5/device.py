"""IOSDevice5 facade with devicectl-first, ios4 fallback routing."""

from __future__ import annotations

import logging
import math
import shutil
import sys
from pathlib import Path

import requests

from idevice.device.base.device import AppDataPath
from idevice.device.base.errors import CommandExecutionError, DeviceNotFoundError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppInfo
from idevice.device.common.ios4cli import IOS4CLI, IOS4CLIError
from idevice.device.config import device_id as env_device_id
from idevice.device.config import device_ip as env_device_ip
from idevice.device.config import ios4_binary, xcrun_binary
from idevice.device.config import package_name as env_package_name
from idevice.device.ios5.devicectl import (
    Devicectl,
    DevicectlOutcome,
    IOSDevice5Error,
    _run_devicectl,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice5]"
_WIRED_TRANSPORT = "wired"
_IWDA2_PORT = 18201
_IWDA2_HTTP_TIMEOUT = 30.0
_IWDA2_DEFAULT_MONITOR_DURATION = 180
_DEVICETCL_FAILURES = (IOSDevice5Error, CommandExecutionError)


class IOSDevice5(Devicectl):
    """Route iOS operations through devicectl first and ios4 second.

    Operations implemented by CoreDevice always try :class:`Devicectl` first.
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
        if self._resolve_binary(configured_xcrun) is None:
            logger.error(f"{_LOG_TAG} `{configured_xcrun}` CLI not found")
            raise IOSDevice5Error(
                f"`{configured_xcrun}` CLI not found. Install Xcode, or set "
                "IDEVICE_XCRUN_BINARY."
            )
        super().__init__(
            device_id,
            device_ip=device_ip,
            package_name=package_name,
            cache_dir=cache_dir,
            validate_host=False,
        )
        self._cache_dir = cache_dir
        self._ios4cli_backend: IOS4CLI | None = None

    @classmethod
    def from_env(cls) -> IOSDevice5:
        """Build an IOSDevice5 from the ``GAUTO_*`` environment."""
        return cls(
            env_device_id(),
            device_ip=env_device_ip(),
            package_name=env_package_name(),
        )

    @staticmethod
    def _resolve_binary(binary: str) -> str | None:
        """Return the usable path for ``binary``, or ``None`` when missing."""
        resolved = shutil.which(binary)
        if resolved is not None:
            return resolved
        return binary if Path(binary).is_file() else None

    @classmethod
    def default_udid(cls) -> str:
        """Return the first wired device, falling back to ios4 listing."""
        outcome = _run_devicectl(
            xcrun_binary(), cls._new_runner(), ["list", "devices"], timeout=30
        )
        if outcome.succeeded:
            for entry in outcome.result.get("devices", []):
                if not isinstance(entry, dict):
                    continue
                connection = entry.get("connectionProperties", {})
                if connection.get("transportType") != _WIRED_TRANSPORT:
                    continue
                udid = entry.get("hardwareProperties", {}).get("udid")
                if udid:
                    return udid
        try:
            return IOS4CLI.default_udid()
        except Exception as exc:
            detail = outcome.error or "no wired CoreDevice device"
            raise DeviceNotFoundError(
                f"{_LOG_TAG} No USB-attached device was found via devicectl "
                f"({detail}) or ios4 ({exc})"
            ) from exc

    @staticmethod
    def _new_runner() -> SubprocessRunner:
        """Create a runner without exposing it as part of the public API."""
        return SubprocessRunner()

    def _ios4cli(self) -> IOS4CLI | None:
        """Return the lazily constructed ios4 layer when its CLI is present."""
        configured = ios4_binary()
        binary = self._resolve_binary(configured)
        if binary is None:
            logger.debug(f"{_LOG_TAG} ios4 fallback unavailable: `{configured}` not found")
            return None
        if self._ios4cli_backend is None or self._ios4cli_backend.binary != binary:
            self._ios4cli_backend = IOS4CLI(
                self.device_id,
                binary=binary,
                runner=self._runner,
                app_cache=self._app_cache,
                cache_dir=self._cache_dir,
            )
        else:
            self._ios4cli_backend.runner = self._runner
            self._ios4cli_backend.app_cache = self._app_cache
        return self._ios4cli_backend

    def _log_fallback(self, operation: str, error: BaseException | None = None) -> None:
        reason = f": {error}" if error is not None else ""
        logger.warning(
            f"{_LOG_TAG} devicectl {operation} failed{reason}; falling back to ios4"
        )

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        try:
            return Devicectl.install(self, package_path, app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("install", exc)
            backend = self._ios4cli()
            return backend.install(package_path, app_id) if backend else False

    def uninstall(self, app_id: str) -> None:
        try:
            Devicectl.uninstall(self, app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("uninstall", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            backend.uninstall(app_id)

    def is_installed(self, app_id: str) -> bool:
        try:
            return Devicectl.is_installed(self, app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("is_installed", exc)
            backend = self._ios4cli()
            return backend.is_installed(app_id) if backend else False

    def launch(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        terminate_existing: bool = True,
        activate: bool = True,
    ) -> None:
        try:
            Devicectl.launch(
                self,
                app_id,
                args=args,
                environment=environment,
                terminate_existing=terminate_existing,
                activate=activate,
            )
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("launch", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            backend.launch_app(
                self._resolve_app_id(app_id), args=args, environment=environment
            )
            self._sync_ios4_launch(backend)

    def launch_app(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        terminate_existing: bool = True,
        activate: bool = True,
    ) -> None:
        try:
            Devicectl.launch_app(
                self,
                app_id,
                args=args,
                environment=environment,
                terminate_existing=terminate_existing,
                activate=activate,
            )
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("launch_app", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            backend.launch_app(
                self._resolve_app_id(app_id), args=args, environment=environment
            )
            self._sync_ios4_launch(backend)

    def _sync_ios4_launch(self, backend: IOS4CLI) -> None:
        self._last_launch_pid = backend.last_launch_pid
        self._last_launch_app_id = backend.last_launch_app_id

    def stop_app(self, app_id: str | None = None) -> None:
        try:
            Devicectl.stop_app(self, app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("stop_app", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            backend.stop_app(self._resolve_app_id(app_id))
            self._sync_ios4_launch(backend)

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        try:
            return Devicectl.get_installed_pkg_name(self, app_id)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("get_installed_pkg_name", exc)
            backend = self._ios4cli()
            return backend.get_installed_pkg_name(app_id) if backend else None

    def host_is_running(self) -> bool:
        try:
            return Devicectl.host_is_running(self)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("host_is_running", exc)
            backend = self._ios4cli()
            return backend.host_is_running() if backend else False

    def screenshot(self, local: Path | str) -> bool:
        try:
            if Devicectl.screenshot(self, local):
                return True
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("screenshot", exc)
        else:
            self._log_fallback("screenshot")
        return self._screenshot_via_ios4(Path(local))

    def _screenshot_via_ios4(self, local_path: Path) -> bool:
        """Compatibility helper routed to the ios4 layer."""
        backend = self._ios4cli()
        return backend.screenshot(local_path) if backend else False

    def capture_memgraph(
        self, output: Path | str, *, pid: int | None = None
    ) -> Path:
        """Route the CoreDevice-unsupported memory graph directly to ios4."""
        backend = self._ios4cli()
        if backend is None:
            raise IOSDevice5Error(
                f"{_LOG_TAG} capture_memgraph needs the `{ios4_binary()}` CLI, "
                "which CoreDevice does not replace. Install it or set "
                "IDEVICE_IOS4_BINARY."
            )
        target_pid = self._last_launch_pid if pid is None else pid
        try:
            return backend.capture_memgraph(output, pid=target_pid)
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
            Devicectl.push(
                self,
                local,
                remote,
                app_id=app_id,
                documents_only=documents_only,
                remove_existing_content=remove_existing_content,
            )
            return
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("push", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            if not documents_only:
                backend.push(local, remote, app_id=app_id, documents_only=False)
                return
            target = self._resolve_app_id(app_id)
            if not backend.documents_push(target, local, remote):
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
            Devicectl.pull(
                self,
                remote,
                local,
                app_id=app_id,
                documents_only=documents_only,
            )
            return
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("pull", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            if not documents_only:
                backend.pull(remote, local, app_id=app_id, documents_only=False)
                return
            target = self._resolve_app_id(app_id)
            if not backend.documents_pull(target, remote, local):
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
            return Devicectl.ls(
                self,
                remote,
                app_id=app_id,
                recursive=recursive,
                documents_only=documents_only,
            )
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("ls", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            if not documents_only or recursive:
                return backend.ls(remote, app_id=app_id, recursive=recursive)
            return backend.documents_ls(self._resolve_app_id(app_id), remote)

    def documents_exists(self, app_id: str, remote: str) -> bool:
        try:
            return Devicectl.documents_exists(self, app_id, remote)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("documents_exists", exc)
            backend = self._ios4cli()
            return backend.documents_exists(app_id, remote) if backend else False

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        try:
            return Devicectl.documents_ls(self, app_id, remote)
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("documents_ls", exc)
            backend = self._ios4cli()
            if backend is None:
                raise
            return backend.documents_ls(app_id, remote)

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        try:
            Devicectl.pull(
                self, remote, local, app_id=app_id, documents_only=True
            )
            return True
        except (FileNotFoundError, *_DEVICETCL_FAILURES) as exc:
            self._log_fallback("documents_pull", exc)
            backend = self._ios4cli()
            return backend.documents_pull(app_id, remote, local) if backend else False

    def documents_push(
        self,
        app_id: str,
        local: Path | str,
        remote: str,
        *,
        remove_existing_content: bool = False,
    ) -> bool:
        try:
            Devicectl.push(
                self,
                local,
                remote,
                app_id=app_id,
                documents_only=True,
                remove_existing_content=remove_existing_content,
            )
            return True
        except (FileNotFoundError, *_DEVICETCL_FAILURES) as exc:
            self._log_fallback("documents_push", exc)
            backend = self._ios4cli()
            return backend.documents_push(app_id, local, remote) if backend else False

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Route CoreDevice-unsupported removal directly to ios4."""
        backend = self._ios4cli()
        return backend.documents_rm(app_id, remote) if backend else False

    def pull2(self, data_path: AppDataPath, remote: str, local: Path | str) -> bool:
        if not isinstance(data_path, AppDataPath):
            raise ValueError(f"Invalid data_path: {data_path!r}")
        try:
            Devicectl.pull(
                self,
                remote,
                local,
                documents_only=data_path is AppDataPath.Persistent,
            )
            return True
        except _DEVICETCL_FAILURES as exc:
            self._log_fallback("pull2", exc)
            backend = self._ios4cli()
            if backend is None:
                return False
            return backend.pull2(data_path, remote, local)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
    ) -> None:
        backend = self._ios4cli()
        if backend is None:
            self._unsupported("swipe")
        backend.swipe(x1, y1, x2, y2, duration_ms=duration_ms)

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        backend = self._ios4cli()
        if backend is None:
            self._unsupported("delete2")
        return backend.delete2(data_path, remote)

    @staticmethod
    def _validate_normalized_coordinate(value: float, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a normalized coordinate in [0, 1]")
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be a normalized coordinate in [0, 1]")

    def tap(self, x: float, y: float, *, app_id: str | None = None) -> None:
        """Tap via iwda2; CoreDevice has no touch-injection service."""
        self._validate_normalized_coordinate(x, "x")
        self._validate_normalized_coordinate(y, "y")
        device_ip = self.device_ip.strip()
        if not device_ip:
            raise IOSDevice5Error(f"{_LOG_TAG} Cannot tap through iwda2: device_ip is empty")
        target = (app_id or self.package_name).strip()
        params: dict[str, float | str] = {"x": float(x), "y": float(y)}
        if target:
            params["bundleId"] = target
        url = f"http://{device_ip}:{_IWDA2_PORT}/api/tap"
        try:
            response = requests.get(url, params=params, timeout=_IWDA2_HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise IOSDevice5Error(
                f"{_LOG_TAG} iwda2 tap request failed: GET {url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise IOSDevice5Error(
                f"{_LOG_TAG} iwda2 tap failed: GET {url} returned HTTP "
                f"{response.status_code}: {response.text!r}"
            )

    def start_moniter(self, duration: int = _IWDA2_DEFAULT_MONITOR_DURATION) -> bool:
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError("duration must be a positive finite number")
        return self._request_iwda2_monitor(
            "/api/monitor/start", params={"duration": format(float(duration), "g")}
        )

    def stop_moniter(self) -> bool:
        return self._request_iwda2_monitor("/api/monitor/stop")

    def _request_iwda2_monitor(
        self, route: str, *, params: dict[str, str] | None = None
    ) -> bool:
        device_ip = self.device_ip.strip()
        if not device_ip:
            logger.warning(f"{_LOG_TAG} Cannot call iwda2 {route}: device_ip is empty")
            return False
        url = f"http://{device_ip}:{_IWDA2_PORT}{route}"
        try:
            response = requests.get(url, params=params, timeout=_IWDA2_HTTP_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning(f"{_LOG_TAG} iwda2 request failed: GET {url}: {exc}")
            return False
        if response.status_code != 200:
            logger.warning(
                f"{_LOG_TAG} GET {url} returned HTTP {response.status_code}: "
                f"{response.text!r}"
            )
            return False
        return True


__all__ = ["DevicectlOutcome", "IOSDevice5", "IOSDevice5Error"]
