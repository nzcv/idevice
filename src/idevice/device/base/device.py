"""Abstract ``DeviceBase`` for cross-platform device app lifecycle."""

from __future__ import annotations

import logging
import platform
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from idevice.device.cache import InstalledAppInfo

logger = logging.getLogger(__name__)


class AppDataPath(Enum):
    """Unity-style app data roots used by :meth:`DeviceBase.pull2`.

    Local:
        Roughly ``Application.dataPath`` (Windows: ``*_Data`` next to the
        installed exe). On iOS3 this maps to the full app-container AFC root
        (House Arrest without ``documents_only``).
    Persistent:
        Roughly ``Application.persistentDataPath`` (Windows LocalLow;
        iOS/Android Documents / external ``files``).
    """

    Local = "Local"
    Persistent = "Persistent"


class DeviceBase(ABC):
    """Install packages and launch apps on a device (adb / ios / hdc, etc.).

    A device instance is always bound to a single ``device_id`` (UDID / serial).
    """

    def __init__(
        self,
        device_id: str,
        device_ip: str,
        platform: str,
        *,
        package_name: str = "",
    ):
        """Bind the instance to a single device.

        Args:
            device_id: Device id (UDID / serial). Required and non-empty.
            device_ip: Device IP address, or an empty string when not applicable.
            platform: Platform identifier (e.g. ``ios``, ``ios3``, ``ios4``,
                ``android``, ``windows``).
            package_name: Default app id (bundle id / package name / exe name)
                used when callers omit ``app_id`` (e.g. :meth:`stop_app`).

        Raises:
            ValueError: If ``device_id`` is empty or not a string.
        """
        if not device_id or not isinstance(device_id, str):
            raise ValueError("device_id is required and must be a non-empty string")
        self._device_id = device_id
        self._device_ip = device_ip
        self._platform = platform
        self._package_name = package_name

    @property
    def platform(self) -> str:
        """Platform bound to this instance."""
        return self._platform

    @property
    def device_id(self) -> str:
        """Device id (UDID / serial) bound to this instance."""
        return self._device_id

    @property
    def device_ip(self) -> str:
        """Device ip bound to this instance."""
        return self._device_ip

    @property
    def package_name(self) -> str:
        """Default app id bound to this instance (may be empty)."""
        return self._package_name

    def _resolve_app_id(self, app_id: str | None) -> str:
        """Return ``app_id`` or fall back to the bound ``package_name``.

        Raises:
            ValueError: If both ``app_id`` and ``package_name`` are empty.
        """
        target = app_id or self._package_name
        if not target:
            raise ValueError("app_id is required and must be a non-empty string")
        return target

    def ping(self, ip: str | None = None, *, timeout: float = 1.0) -> bool:
        """Return ``True`` if ``ip`` is reachable via ICMP ping.

        Args:
            ip: Address to probe. Defaults to the bound :attr:`device_ip`.
            timeout: Per-probe wait budget in seconds.

        Returns:
            bool: ``True`` if a reply was received, ``False`` if the address is
            empty, the probe timed out, or ping failed for any other reason.
        """
        target = (ip if ip is not None else self._device_ip).strip()
        if not target:
            logger.debug("[DeviceBase] ping skipped: empty ip")
            return False
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        system = platform.system().lower()
        timeout_ms = max(1, int(timeout * 1000))
        if system == "windows":
            command = ["ping", "-n", "1", "-w", str(timeout_ms), target]
        elif system == "darwin":
            command = ["ping", "-c", "1", "-W", str(timeout_ms), target]
        else:
            command = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), target]

        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout + 1.0,
            "check": False,
        }
        if system == "windows":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            completed = subprocess.run(command, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug(f"[DeviceBase] ping {target} failed: {exc}")
            return False

        ok = completed.returncode == 0
        logger.debug(f"[DeviceBase] ping {target}: {'ok' if ok else 'unreachable'}")
        return ok

    @classmethod
    @abstractmethod
    def default_udid(cls) -> str:
        """Return the default device id for this platform (e.g. first connected device).

        Raises:
            DeviceNotFoundError: If no suitable device is available.
        """
        raise NotImplementedError

    @abstractmethod
    def install(self, package_path: Path, app_id: str | None = None) -> bool:
        """Install a package on the bound device.

        Args:
            package_path: Path to the package to install.
            app_id: Optional app identifier (bundle id / package name) associated
                with ``package_path``. When provided, implementations should
                record the ``app_id -> package file name`` mapping so it can
                later be retrieved via :meth:`get_installed_pkg_name`.

        Returns:
            bool: ``True`` if installation succeeded, ``False`` otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def uninstall(self, app_id: str) -> None:
        """Remove an installed app (package / bundle name per platform).

        Args:
            app_id: ID of the app to uninstall.
        """
        raise NotImplementedError

    @abstractmethod
    def is_installed(self, app_id: str) -> bool:
        """Check if an app is installed on the bound device.

        Args:
            app_id: ID of the app to check.

        Returns:
            bool: True if the app is installed, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def launch_app(self, app_id: str | None = None) -> None:
        """Launch an installed app on the bound device.

        Args:
            app_id: ID of the app to launch (bundle id / package name). When
                omitted or empty, uses the bound :attr:`package_name`.

        Raises:
            ValueError: If both ``app_id`` and :attr:`package_name` are empty.
        """
        raise NotImplementedError

    def launch(self, app_id: str | None = None) -> None:
        """Launch an app with the platform's native device service.

        This is an optional lower-level operation. Use :meth:`launch_app` for
        the portable app-launch API.

        Args:
            app_id: ID of the app to launch (bundle id / package name). When
                omitted or empty, implementations may use the bound
                :attr:`package_name`.

        Raises:
            NotImplementedError: When the platform has no native launch
                implementation.
        """
        del app_id
        raise NotImplementedError(
            f"launch is not supported on {self.platform} devices"
        )

    @abstractmethod
    def stop_app(self, app_id: str | None = None) -> None:
        """Stop (kill) a running app on the bound device.

        Args:
            app_id: ID of the app to stop (bundle id / package name). When
                omitted or empty, uses the bound :attr:`package_name`.

        Raises:
            ValueError: If ``app_id`` is empty and no ``package_name`` is bound.
        """
        raise NotImplementedError

    @abstractmethod
    def get_installed_pkg_name(self, app_id: str) -> InstalledAppInfo | None:
        """Return the installed app info for an app on the bound device.

        Implementations should return an :class:`InstalledAppInfo` describing the
        app (its ``app_id``, package ``version`` and installed ``path``) recorded
        at install time, or ``None`` if the app is not installed or no record is
        found.

        Args:
            app_id: ID of the app to look up (bundle id / package name).

        Returns:
            InstalledAppInfo | None: The installed app info, or ``None`` if not
            found.
        """
        raise NotImplementedError

    def tap(self, x: float, y: float, *, app_id: str | None = None) -> None:
        """Tap a normalized point on the bound device's screen.

        Coordinates are fractions of the screen rather than pixels: ``(0, 0)``
        is the top-left corner and ``(1, 1)`` the bottom-right.

        Args:
            x: Horizontal position in ``[0, 1]``.
            y: Vertical position in ``[0, 1]``.
            app_id: Foreground app id the offset is anchored to, where the
                platform supports it.

        Raises:
            ValueError: If ``x`` or ``y`` is outside ``[0, 1]``.
            NotImplementedError: On platforms without tap support.
        """
        del x, y, app_id
        raise NotImplementedError(f"tap is not supported on {self.platform} devices")

    @abstractmethod
    def host_is_running(self) -> bool:
        """Check if the WDA/UIAutomator2  is running on the bound device.

        Returns:
            bool: True if the WDA/UIAutomator2 is running, False otherwise.
        """
        raise NotImplementedError

    def capture_memgraph(
        self,
        output: Path | str,
        *,
        pid: int | None = None,
    ) -> Path:
        """Capture a process memory snapshot to ``output``.

        Args:
            output: Destination memory-snapshot file on the host.
            pid: Optional process ID. Implementations may use the most recently
                launched process when omitted.

        Returns:
            Path: Resolved destination path containing the snapshot.

        Raises:
            NotImplementedError: When the platform cannot capture memory.
        """
        del output, pid
        raise NotImplementedError(
            f"capture_memgraph is not supported on {self.platform} devices"
        )

    @abstractmethod
    def push(
        self,
        local: Path | str,
        remote: str,
        *,
        app_id: str | None = None,
        documents_only: bool = False,
    ) -> None:
        """Push a local file or directory to the bound device.

        Args:
            local: Path to the local file or directory.
            remote: Destination path on the device.
            app_id: Optional app identifier (bundle id / package name) when
                the transfer targets an app sandbox. Ignored on platforms
                that do not support scoped transfers.
            documents_only: When ``app_id`` is set, restrict the transfer to
                the app's Documents directory where supported. Ignored on
                platforms that do not support this scope.

        Raises:
            ValueError: If ``remote`` is empty.
            FileNotFoundError: If ``local`` does not exist.
        """
        raise NotImplementedError

    @abstractmethod
    def pull(
        self,
        remote: str,
        local: Path | str,
        *,
        app_id: str | None = None,
        documents_only: bool = True,
    ) -> None:
        """Pull a remote file or directory from the bound device.

        Args:
            remote: Source path on the device.
            local: Destination path on the host.
            app_id: Optional app identifier (bundle id / package name) when
                the transfer targets an app sandbox. Ignored on platforms
                that do not support scoped transfers.
            documents_only: When ``app_id`` is set, restrict the transfer to
                the app's Documents directory where supported. Ignored on
                platforms that do not support this scope.

        Raises:
            ValueError: If ``remote`` is empty.
        """
        raise NotImplementedError

    @abstractmethod
    def ls(
        self,
        remote: str,
        *,
        app_id: str | None = None,
        recursive: bool = False,
    ) -> list[str]:
        """List entries at a remote path on the bound device.

        Args:
            remote: Directory path on the device.
            app_id: Optional app identifier (bundle id / package name) when
                listing an app sandbox. Ignored on platforms that do not
                support scoped listing.
            recursive: When ``True``, include entries in subdirectories where
                the platform supports it.

        Returns:
            list[str]: Remote paths or entry names (platform-dependent).

        Raises:
            ValueError: If ``remote`` is empty.
            NotImplementedError: On platforms without remote listing support.
        """
        raise NotImplementedError

    @abstractmethod
    def documents_exists(self, app_id: str, remote: str) -> bool:
        """Check whether ``remote`` exists in an app's Documents sandbox.

        Args:
            app_id: App identifier (bundle id / package name).
            remote: Path relative to the app's Documents directory.

        Returns:
            bool: ``True`` if the path exists, ``False`` otherwise.

        Raises:
            ValueError: If ``app_id`` or ``remote`` is empty.
            NotImplementedError: On platforms without Documents sandbox access.
        """
        raise NotImplementedError

    @abstractmethod
    def documents_ls(self, app_id: str, remote: str) -> list[str]:
        """List entries under ``remote`` in an app's Documents sandbox.

        Args:
            app_id: App identifier (bundle id / package name).
            remote: Directory path relative to the app's Documents directory.

        Returns:
            list[str]: Entry names or paths under ``remote``.

        Raises:
            ValueError: If ``app_id`` or ``remote`` is empty.
            NotImplementedError: On platforms without Documents sandbox access.
        """
        raise NotImplementedError

    @abstractmethod
    def documents_pull(self, app_id: str, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from an app's Documents sandbox.

        Args:
            app_id: App identifier (bundle id / package name).
            remote: Source path relative to the app's Documents directory.
            local: Destination path on the host.

        Returns:
            bool: ``True`` if the pull succeeded, ``False`` if the remote path
                does not exist or the transfer failed.

        Raises:
            ValueError: If ``app_id`` or ``remote`` is empty.
            NotImplementedError: On platforms without Documents sandbox access.
        """
        raise NotImplementedError

    @abstractmethod
    def documents_push(self, app_id: str, local: Path | str, remote: str) -> bool:
        """Push a local file or directory into an app's Documents sandbox.

        Args:
            app_id: App identifier (bundle id / package name).
            local: Path to the local file or directory.
            remote: Destination path relative to the app's Documents directory.

        Returns:
            bool: ``True`` if the push succeeded, ``False`` if ``local`` does
                not exist or the transfer failed.

        Raises:
            ValueError: If ``app_id`` or ``remote`` is empty.
            NotImplementedError: On platforms without Documents sandbox access.
        """
        raise NotImplementedError

    @abstractmethod
    def screenshot(self, local: Path | str) -> bool:
        """Capture the device screen and write it to ``local``.

        Args:
            local: Destination file path on the host (e.g. ``screenshot.png``).
                Parent directories are created as needed.

        Returns:
            bool: ``True`` if the screenshot was captured and written,
                ``False`` otherwise.

        Raises:
            NotImplementedError: On platforms without screen capture support.
        """
        raise NotImplementedError

    @abstractmethod
    def documents_rm(self, app_id: str, remote: str) -> bool:
        """Remove a file or directory from an app's Documents sandbox.

        Args:
            app_id: App identifier (bundle id / package name).
            remote: Target path relative to the app's Documents directory.

        Returns:
            bool: ``True`` if the removal succeeded, ``False`` otherwise.

        Raises:
            ValueError: If ``app_id`` or ``remote`` is empty.
            NotImplementedError: On platforms without Documents sandbox access.
        """
        raise NotImplementedError

    @abstractmethod
    def pull2(self, data_path: AppDataPath, remote: str, local: Path | str) -> bool:
        """Pull a file or directory from Local or Persistent app data.

        Uses the bound :attr:`package_name` as the app id where the platform
        needs one (iOS / Android). See :class:`AppDataPath` for per-platform
        root semantics.

        Args:
            data_path: Which app-data root to read from.
            remote: Path relative to the chosen data root.
            local: Destination path on the host.

        Returns:
            bool: ``True`` if the pull succeeded, ``False`` if the remote path
                does not exist or the transfer failed.

        Raises:
            ValueError: If ``remote`` is empty or ``data_path`` is invalid.
            NotImplementedError: When the platform cannot access that root.
            FileNotFoundError: When a required install/cache entry is missing.
        """
        raise NotImplementedError

    @abstractmethod
    def delete2(self, data_path: AppDataPath, remote: str) -> bool:
        """Delete a file or directory from Local or Persistent app data.

        Uses the bound :attr:`package_name` as the app id where the platform
        needs one (iOS / Android). See :class:`AppDataPath` for per-platform
        root semantics.

        Args:
            data_path: Which app-data root to delete from.
            remote: Path relative to the chosen data root.

        Returns:
            bool: ``True`` if the deletion succeeded, ``False`` if the remote
                path does not exist or the deletion failed.

        Raises:
            ValueError: If ``remote`` is empty or ``data_path`` is invalid.
            NotImplementedError: When the platform cannot access that root.
            FileNotFoundError: When a required install/cache entry is missing.
        """
        raise NotImplementedError

    def launch_wda(self, bundle_id: str) -> None:
        """Launch the WDA/UIAutomator2 service on the bound device.

        Args:
            bundle_id: The bundle identifier (app id / package name) to launch
                WDA/UIAutomator2 for. When omitted, implementations may use the
                bound :attr:`package_name`.
        """
        del bundle_id
        raise NotImplementedError(
            f"launch_wda is not supported on {self.platform} devices"
        )

    def start_moniter(self):
        raise NotImplementedError(
            f"start_moniter is not supported on {self.platform} devices"
        )

    def stop_moniter(self):
        raise NotImplementedError(
            f"stop_moniter is not supported on {self.platform} devices"
        )
