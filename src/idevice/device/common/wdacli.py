"""Shared, device-agnostic wrapper around the ``facebook-wda`` client."""

from __future__ import annotations

import logging
from collections.abc import Callable

import wda
from wda import AlertAction

from idevice.device.common.iwda2 import validate_normalized_coordinate

logger = logging.getLogger(__name__)

_LOG_TAG = "[WDACLI]"
WDA_PORT = 8100
WDA_READY_TIMEOUT = 60.0


class WDACLIError(RuntimeError):
    """Raised when a WebDriverAgent operation cannot be completed."""


class WDACLI:
    """Drive WebDriverAgent through ``facebook-wda`` for one device.

    This class owns only WDA concerns: client construction, session reuse,
    launch/terminate requests, and screen taps.
    """

    def __init__(
        self,
        device_ip: str,
        *,
        port: int = WDA_PORT,
        client_factory: Callable[..., wda.Client] = wda.Client,
    ) -> None:
        """Bind the wrapper to one device's WebDriverAgent endpoint.

        Args:
            device_ip: Device address, or an empty string to fall back to the
                ``wda`` client default (``DEVICE_URL`` / localhost, as used
                with a USB port forward).
            port: Port WebDriverAgent listens on.
            client_factory: Callable building a ``wda.Client`` from a base URL.
                Replaced in tests with a fake client.
        """
        self.device_ip = device_ip
        self.port = port
        self.client_factory = client_factory

    @property
    def url(self) -> str | None:
        """Return the bound WDA base URL, or ``None`` for the client default."""
        address = self.device_ip.strip()
        return f"http://{address}:{self.port}" if address else None

    def client(self) -> wda.Client:
        """Return a client bound to WebDriverAgent's *existing* session.

        Operations on the app under test must never open or close a session of
        their own. ``POST /session`` displaces the app the previous session
        launched and ``DELETE /session/{id}`` terminates it, so a helper
        wrapping its work in ``with client.session() as s:`` kills the app that
        was just launched -- which is what the popup watch used to do to every
        game it was supposed to be babysitting.

        A bare client resolves ``session_id`` lazily from ``GET /status``,
        attaching to whichever session is already live and creating one only
        when the device has none.
        """
        return self.client_factory(self.url)

    def is_ready(self) -> bool:
        """Return whether WebDriverAgent already answers its status probe."""
        try:
            return bool(self.client().is_ready())
        except Exception as exc:
            logger.debug(f"{_LOG_TAG} WDA status probe failed at {self.url}: {exc}")
            return False

    def wait_ready(self, timeout: float = WDA_READY_TIMEOUT) -> bool:
        """Block until WebDriverAgent answers ``status`` or ``timeout`` elapses.

        Args:
            timeout: Seconds to wait for readiness.

        Returns:
            bool: Whether WDA became ready in time.

        Raises:
            ValueError: If ``timeout`` is not a positive number.
        """
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")
        try:
            return bool(self.client().wait_ready(timeout=timeout))
        except Exception as exc:
            logger.warning(
                f"{_LOG_TAG} WDA readiness wait failed at {self.url}: {exc}"
            )
            return False

    def launch_app(
        self,
        app_id: str,
        *,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None,
        alert_action: AlertAction | None = AlertAction.ACCEPT,
    ) -> int | None:
        """Launch ``app_id`` through WebDriverAgent and return its PID.

        The WDA session is deliberately left open: deleting it terminates the
        application under test on some WebDriverAgent builds.

        ``alert_action`` is sent as WebDriverAgent's ``defaultAlertAction``
        capability, which starts its built-in alerts monitor: for the life of
        the session WDA itself taps the accept (or dismiss) button of every
        alert the app raises, so permission prompts during launch or login need
        no polling from this side.

        Args:
            app_id: Bundle identifier to launch.
            args: Ordered command-line arguments passed to the process.
            environment: Environment variables injected before process start.
            alert_action: How WebDriverAgent should answer alerts on its own.
                ``None`` leaves its monitor off, which means alerts stay on
                screen until something else on the device clears them.

        Returns:
            int | None: The PID WDA reports for ``app_id``, or ``None`` when
            its app list does not include one.

        Raises:
            ValueError: If ``app_id`` is empty.
            WDACLIError: If WebDriverAgent does not accept the launch.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        try:
            session = self.client().session(
                app_id,
                arguments=args or None,
                environment=environment or None,
                alert_action=alert_action,
            )
        except Exception as exc:
            raise WDACLIError(
                f"{_LOG_TAG} WDA failed to launch {app_id} at {self.url}: {exc}"
            ) from exc
        return self._session_pid(session, app_id)

    @staticmethod
    def _session_pid(session: wda.Client, app_id: str) -> int | None:
        """Return the PID WDA reports for ``app_id``, when it reports one."""
        try:
            entries = session.app_list()
        except Exception as exc:
            logger.debug(f"{_LOG_TAG} WDA app list unavailable: {exc}")
            return None
        for entry in entries or []:
            if entry.get("bundleId") != app_id:
                continue
            pid = entry.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                return pid
        return None

    def stop_app(self, app_id: str) -> None:
        """Terminate ``app_id`` through WebDriverAgent.

        Raises:
            ValueError: If ``app_id`` is empty.
            WDACLIError: If WebDriverAgent does not accept the termination.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        try:
            self.client().app_terminate(app_id)
        except Exception as exc:
            raise WDACLIError(
                f"{_LOG_TAG} WDA failed to stop {app_id} at {self.url}: {exc}"
            ) from exc

    def tap(self, x: float, y: float) -> None:
        """Tap a normalized screen point through WebDriverAgent.

        Args:
            x: Horizontal position in ``[0, 1]``.
            y: Vertical position in ``[0, 1]``.

        Raises:
            ValueError: If ``x`` or ``y`` is not a finite number in ``[0, 1]``.
            WDACLIError: If WebDriverAgent does not accept the tap.
        """
        validate_normalized_coordinate(x, "x")
        validate_normalized_coordinate(y, "y")
        try:
            self.client().click(float(x), float(y))
        except Exception as exc:
            raise WDACLIError(
                f"{_LOG_TAG} WDA failed to tap ({x}, {y}) at {self.url}: {exc}"
            ) from exc


__all__ = [
    "WDA_PORT",
    "WDA_READY_TIMEOUT",
    "AlertAction",
    "WDACLI",
    "WDACLIError",
]
