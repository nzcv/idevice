"""Abstract ``DeviceBase`` for cross-platform device app lifecycle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from idevice.device.cache import InstalledAppInfo


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
            platform: Platform identifier (e.g. ``ios``, ``ios3``, ``android``,
                ``windows``).
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
    def launch_app(self, app_id: str) -> None:
        """Launch an installed app on the bound device.

        Args:
            app_id: ID of the app to launch (bundle id / package name).

        Raises:
            ValueError: If ``app_id`` is empty.
        """
        raise NotImplementedError

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

    @abstractmethod
    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
    ) -> None:
        """Swipe on the bound device from ``(x1, y1)`` to ``(x2, y2)``.

        Args:
            x1: Start X coordinate in pixels.
            y1: Start Y coordinate in pixels.
            x2: End X coordinate in pixels.
            y2: End Y coordinate in pixels.
            duration_ms: Gesture duration in milliseconds.

        Raises:
            ValueError: If ``duration_ms`` is not positive.
            NotImplementedError: On platforms without touch input support.
        """
        raise NotImplementedError

    @abstractmethod
    def host_is_running(self) -> bool:
        """Check if the WDA/UIAutomator2  is running on the bound device.

        Returns:
            bool: True if the WDA/UIAutomator2 is running, False otherwise.
        """
        raise NotImplementedError

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
