"""iOS backend launching through WebDriverAgent and using ios4 for the rest."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NoReturn

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import AppNotInstalledError
from idevice.device.base.runner import SubprocessRunner
from idevice.device.cache import InstalledAppCache, InstalledAppInfo
from idevice.device.common.ios4cli import IOS4CLI, IOS4CLIError
from idevice.device.common.wdacli import (
    WDA_READY_TIMEOUT,
    WDACLI,
    AlertAction,
    WDACLIError,
)
from idevice.device.config import device_id as env_device_id
from idevice.device.config import device_ip as env_device_ip
from idevice.device.config import ios4_binary
from idevice.device.config import package_name as env_package_name

logger = logging.getLogger(__name__)

_LOG_TAG = "[IOSDevice6]"


class IOSDevice6Error(RuntimeError):
    """Raised when an ``ios6`` device operation fails."""


class IOSDevice6(DeviceBase):
    """Drive app launches and UI input through WebDriverAgent.

    WebDriverAgent owns the interactive surface:

    * :meth:`launch_app` starts the app under test through a WDA session
      whose ``autoClickAlertSelector`` leaves WebDriverAgent answering
      permission prompts on its own.
    * :meth:`tap` sends normalized screen taps.

    The agent has to be running before any of that works: this backend does
    not start the xctest runner. Use :meth:`launch` with the runner bundle id,
    or bring WDA up however it is deployed, and read :meth:`wda_url` for the
    endpoint the requests go to.

    Every other operation goes through the shared :class:`IOS4CLI`, exactly as
    on the ios4 backend: install, uninstall, bundle-id checks, ``pkill``-based
    stop, screenshots, memory graphs, and the ``afc --documents`` sandbox.

    Unlike :class:`~idevice.device.ios5.device.IOSDevice5`, a failing WDA
    request is never retried through ios4: WDA-backed operations raise
    :class:`IOSDevice6Error` instead.
    """

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        package_name: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(
            device_id, device_ip, platform="ios6", package_name=package_name
        )
        configured_binary = ios4_binary()
        if IOS4CLI.resolve_binary(configured_binary) is None:
            logger.error(f"{_LOG_TAG} `{configured_binary}` CLI not found")
            raise IOSDevice6Error(
                f"`{configured_binary}` CLI not found. Build or install the "
                "ios4 binary, or set IDEVICE_IOS4_BINARY."
            )
        self._ios4cli = IOS4CLI(
            device_id,
            binary=configured_binary,
            runner=SubprocessRunner(),
        )
        self._wda = WDACLI(device_ip)
        self._app_cache = InstalledAppCache(device_id, cache_dir=cache_dir)
        self._last_launch_pid: int | None = None
        self._last_launch_app_id = ""

    @property
    def last_launch_pid(self) -> int | None:
        """Return the PID from the most recent successful WDA launch."""
        return self._last_launch_pid

    @classmethod
    def from_env(cls) -> IOSDevice6:
        """Build an :class:`IOSDevice6` from the ``GAUTO_*`` environment."""
        return cls(
            env_device_id(),
            device_ip=env_device_ip(),
            package_name=env_package_name(),
        )

    @classmethod
    def default_udid(cls) -> str:
        """Return the first connected device UDID reported by ios4."""
        return IOS4CLI.default_udid(
            binary=ios4_binary(), runner=SubprocessRunner()
        )

    def wda_url(self) -> str | None:
        """Return the bound WDA base URL, or ``None`` for the client default."""
        return self._wda.url

    def is_wda_ready(self) -> bool:
        """Return whether WebDriverAgent already answers its status probe."""
        return self._wda.is_ready()

    def wait_wda_ready(self, timeout: float = WDA_READY_TIMEOUT) -> bool:
        """Block until WebDriverAgent answers ``status`` or ``timeout`` elapses.

        Args:
            timeout: Seconds to wait for readiness.

        Returns:
            bool: Whether WDA became ready in time.

        Raises:
            ValueError: If ``timeout`` is not a positive number.
        """
        return self._wda.wait_ready(timeout)

    def launch_app(
        self,
        app_id: str | None = None,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        alert_action: AlertAction | None = AlertAction.ACCEPT,
        accept_button_labels: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        """Launch an installed app through WebDriverAgent.

        The WDA session is left open on purpose: deleting it terminates the app
        under test on some WebDriverAgent builds. That session also carries
        WebDriverAgent's built-in alerts monitor, started through
        ``autoClickAlertSelector`` after the session exists, so permission
        prompts the app raises are answered on the device for as long as it
        lives.

        The monitor is pointed at the accept button by label through
        ``accept_button_labels``. facebook-wda's ``defaultAlertAction``
        capability does not reach WDA (it is posted outside ``alwaysMatch``),
        so the selector is what actually starts the monitor.

        A successful launch records the PID in :attr:`last_launch_pid` when
        WDA reports one, so a following :meth:`capture_memgraph` can use it
        without an explicit ``pid`` argument.

        Args:
            app_id: Bundle identifier to launch. When omitted or empty, uses
                the bound :attr:`package_name`.
            args: Ordered command-line arguments passed to the app process.
            environment: Environment variables injected before process start.
            alert_action: How WebDriverAgent should answer alerts on its own.
                ``None`` leaves its monitor off, which means prompts stay up
                until something else on the device clears them.
            accept_button_labels: Labels that count as accepting a prompt.
                Defaults to :data:`ACCEPT_ALERT_BUTTON_LABELS`. Only used when
                ``alert_action`` is :attr:`AlertAction.ACCEPT`.

        Raises:
            ValueError: If both ``app_id`` and :attr:`package_name` are empty,
                or if a button label contains a quote or a backtick.
            AppNotInstalledError: If the resolved bundle id is not installed.
            IOSDevice6Error: If WebDriverAgent does not accept the launch.
        """
        target = self._resolve_app_id(app_id)
        self._last_launch_pid = None
        self._last_launch_app_id = ""
        if not self.is_installed(target):
            raise AppNotInstalledError(f"App not installed: {target}")

        logger.info(f"{_LOG_TAG} Launching {target} on {self.device_id} through WDA")
        try:
            pid = self._wda.launch_app(
                target,
                args=args,
                environment=environment,
                alert_action=alert_action,
                accept_button_labels=accept_button_labels,
            )
        except WDACLIError as exc:
            raise IOSDevice6Error(str(exc)) from exc

        self._last_launch_pid = pid
        self._last_launch_app_id = target

    def launch(
        self,
        app_id: str | None = None,
        *,
        wait_ready: bool = True,
        timeout: float = WDA_READY_TIMEOUT,
    ) -> None:
        """Launch an app through the ios4 ``process_control`` command.

        This is the lower-level native launch used to bring up the WDA xctest
        runner itself, which cannot be started through WDA. It accepts no
        launch arguments and does not read back the launch PID, so
        :attr:`last_launch_pid` keeps whatever value an earlier
        :meth:`launch_app` recorded.

        ``process_control`` returns as soon as the device accepts the launch,
        while the xctest runner needs several more seconds to bind its HTTP
        port. This method therefore waits for WebDriverAgent to answer its
        status probe, so a following :meth:`launch_app` does not race the
        agent's startup. Pass ``wait_ready=False`` when launching an ordinary
        app rather than the runner.

        Args:
            app_id: Bundle identifier to launch. When omitted or empty, uses
                the bound :attr:`package_name`.
            wait_ready: Whether to block until WebDriverAgent is reachable.
            timeout: Seconds to wait for WDA readiness.

        Raises:
            ValueError: If both ``app_id`` and :attr:`package_name` are empty,
                or if ``timeout`` is not a positive number.
            CommandExecutionError: If the ios4 command fails.
            IOSDevice6Error: If WebDriverAgent is not reachable in time.
        """
        target = self._resolve_app_id(app_id)
        logger.info(
            f"{_LOG_TAG} Launching {target} through process_control on "
            f"{self.device_id}"
        )
        self._ios4cli.launch(target)
        if not wait_ready:
            return
        logger.info(
            f"{_LOG_TAG} Waiting up to {timeout}s for WDA at "
            f"{self.wda_url() or 'the client default URL'}"
        )
        if not self.wait_wda_ready(timeout):
            raise IOSDevice6Error(
                f"{_LOG_TAG} WebDriverAgent did not become ready at "
                f"{self.wda_url() or 'the client default URL'} within "
                f"{timeout}s after launching {target}"
            )

    def tap(self, x: float, y: float, *, app_id: str | None = None) -> None:
        """Tap a normalized screen point through WebDriverAgent.

        Args:
            x: Horizontal position in ``[0, 1]``.
            y: Vertical position in ``[0, 1]``.
            app_id: Optional foreground bundle id used for log context. WDA
                taps are screen-relative and do not need the bundle id.

        Raises:
            ValueError: If ``x`` or ``y`` is not a finite number in ``[0, 1]``.
            IOSDevice6Error: If WebDriverAgent does not accept the tap.
        """
        app_context = f" for {app_id}" if app_id else ""
        logger.info(
            f"{_LOG_TAG} Tapping ({x}, {y}) on {self.device_id}{app_context}"
        )
        try:
            self._wda.tap(x, y)
        except WDACLIError as exc:
            raise IOSDevice6Error(str(exc)) from exc

    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        """Install an IPA or app directory through ``ideviceinstaller``.

        Args:
            package_path: IPA file or app directory to install.
            app_id: Bundle identifier cached after a successful install.

        Returns:
            bool: Whether the installation reported success.

        Raises:
            FileNotFoundError: If ``package_path`` does not exist.
        """
        package_path = Path(package_path)
        logger.info(
            f"{_LOG_TAG} Installing package on {self.device_id}: {package_path}"
        )
        installed = self._ios4cli.install(package_path)
        if not installed:
            logger.error(f"{_LOG_TAG} Installation failed on {self.device_id}")
            return False
        if app_id:
            self._app_cache.add(app_id, version=package_path.stem, path=None)
        return True

    def uninstall(self, app_id: str) -> None:
        """Uninstall an application through the ios4 app service."""
        logger.info(f"{_LOG_TAG} Uninstalling {app_id} on {self.device_id}")
        self._ios4cli.uninstall(app_id)
        self._app_cache.remove(app_id)

    def is_installed(self, app_id: str) -> bool:
        """Check an exact bundle id using ios4 ``application_listing``."""
        return self._ios4cli.is_installed(app_id)

    def stop_app(self, app_id: str | None = None) -> None:
        """Stop the app through ios4 ``pkill --bundle``.

        A stopped app is not an error.

        Raises:
            IOSDevice6Error: If ``ios4 pkill`` fails, which includes the app
                not being installed.
        """
        target = self._resolve_app_id(app_id)
        logger.info(f"{_LOG_TAG} Stopping app on {self.device_id}: {target}")
        try:
            self._ios4cli.stop_app(target)
        except IOS4CLIError as exc:
            raise IOSDevice6Error(str(exc)) from exc
        if self._last_launch_app_id == target:
            self._last_launch_pid = None
            self._last_launch_app_id = ""

    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        """Return cached package information when the app is still installed."""
        if not self.is_installed(app_id):
            return None
        return self._app_cache.get(app_id)

    def screenshot(self, local: Path | str) -> bool:
        """Capture a screenshot with the ios4 screenshot service."""
        return self._ios4cli.screenshot(local)

    def capture_memgraph(
        self, output: Path | str, *, pid: int | None = None
    ) -> Path:
        """Capture an Xcode-compatible memory graph through ios4.

        Args:
            output: Destination ``.memgraph`` file.
            pid: Process id to capture. Defaults to :attr:`last_launch_pid`
                from the most recent successful WDA launch.

        Returns:
            Path: The resolved destination path.

        Raises:
            IOSDevice6Error: If there is no PID or the snapshot is empty.
            ValueError: If an explicit PID is not a positive integer.
        """
        target_pid = self._last_launch_pid if pid is None else pid
        try:
            return self._ios4cli.capture_memgraph(output, pid=target_pid)
        except IOS4CLIError as exc:
            raise IOSDevice6Error(str(exc)) from exc

    def documents_exists(self, app_id: str, remote: str) -> bool:
        """Check whether ``remote`` exists in the app's Documents sandbox."""
        return self._ios4cli.documents_exists(app_id, remote)

    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entry names under ``remote`` in the app's Documents sandbox."""
        return self._ios4cli.documents_ls(app_id, remote)

    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from the app's Documents sandbox."""
        return self._ios4cli.documents_pull(app_id, remote, local)

    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        """Push a local file or directory into the app's Documents sandbox."""
        return self._ios4cli.documents_push(app_id, local, remote)

    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Remove a file or directory from the app's Documents sandbox."""
        return self._ios4cli.documents_rm(app_id, remote)

    def _unsupported(self, operation: str) -> NoReturn:
        raise NotImplementedError(
            f"{_LOG_TAG} {operation} is not supported by the ios6 lifecycle backend"
        )

    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        del local, remote, app_id, documents_only
        self._unsupported("push")

    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        del remote, local, app_id, documents_only
        self._unsupported("pull")

    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        del remote, app_id, recursive
        self._unsupported("ls")

    def pull2(
        self, data_path: AppDataPath, remote: str, local: Path | str
    ) -> bool:
        del data_path, remote, local
        self._unsupported("pull2")

    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        del data_path, remote
        self._unsupported("delete2")


__all__ = ["IOSDevice6", "IOSDevice6Error"]
