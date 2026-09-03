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
AUTO_CLICK_ALERT_SETTING = "autoClickAlertSelector"
ACCEPT_ALERT_BUTTON_LABELS = (
    "无线局域网与蜂窝网络",
    "允许",
    "好",
    "使用App时允许",
    "仅在使用应用期间",
    "始终允许",
    "允许访问",
    "允许访问本地网络",
    "同意",
    "确定",
    "继续",
    "Allow",
    "Allow Once",
    "Allow While Using App",
    "Allow Access",
    "OK",
    "Yes",
    "Continue",
)


class WDACLIError(RuntimeError):
    """Raised when a WebDriverAgent operation cannot be completed."""


def build_accept_alert_selector(
    labels: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Build the class chain WebDriverAgent uses to find an accept button.

    Args:
        labels: Button labels to match. Defaults to
            :data:`ACCEPT_ALERT_BUTTON_LABELS`.

    Returns:
        str: A class chain query such as
        ``**/XCUIElementTypeButton[`label IN {'允许','好'}`]``.

    Raises:
        ValueError: If no usable label is left, or if a label contains a quote
            or a backtick, either of which would break the query.
    """
    source = labels if labels is not None else ACCEPT_ALERT_BUTTON_LABELS
    normalized: list[str] = []
    seen: set[str] = set()
    for label in source:
        if not label or label in seen:
            continue
        if "'" in label or "`" in label:
            raise ValueError(
                f"button label must not contain a quote or a backtick: {label!r}"
            )
        normalized.append(label)
        seen.add(label)
    if not normalized:
        raise ValueError("at least one button label is required")
    joined = ",".join(f"'{label}'" for label in normalized)
    return f"**/XCUIElementTypeButton[`label IN {{{joined}}}`]"


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
        accept_button_labels: tuple[str, ...] | list[str] | None = None,
    ) -> int | None:
        """Launch ``app_id`` through WebDriverAgent and return its PID.

        The WDA session is deliberately left open: deleting it terminates the
        application under test on some WebDriverAgent builds.

        ``alert_action`` is sent as WebDriverAgent's ``defaultAlertAction``
        capability. facebook-wda places that key next to ``alwaysMatch``
        rather than inside it, so WDA's capability parser drops it and the
        session comes up with ``FBAlertsMonitor`` off. The accept-button
        class chain is therefore posted as ``autoClickAlertSelector``, which
        is the settings path that both starts the monitor and names the
        button it should tap.

        The monitor is started by that settings write, not by session
        creation, so it sees every prompt after the settings land. A prompt
        that appears in the round trip between the app starting and the
        settings landing is still missed.

        Args:
            app_id: Bundle identifier to launch.
            args: Ordered command-line arguments passed to the process.
            environment: Environment variables injected before process start.
            alert_action: How WebDriverAgent should answer alerts on its own.
                ``None`` leaves its monitor off, which means alerts stay on
                screen until something else on the device clears them.
            accept_button_labels: Labels that count as accepting a prompt.
                Defaults to :data:`ACCEPT_ALERT_BUTTON_LABELS`. Only used when
                ``alert_action`` is :attr:`AlertAction.ACCEPT`.

        Returns:
            int | None: The positive PID WDA reports for ``app_id``, or
            ``None`` when the client does not expose one (``facebook-wda``
            1.5.4 has no ``session.pid``).

        Raises:
            ValueError: If ``app_id`` is empty, or if a button label contains a
                quote or a backtick.
            WDACLIError: If WebDriverAgent does not accept the launch or does
                not take the accept-button setting once the app is up.
        """
        if not app_id:
            raise ValueError("app_id is required and must be a non-empty string")
        selector = (
            build_accept_alert_selector(accept_button_labels)
            if alert_action is AlertAction.ACCEPT
            else ""
        )
        try:
            session = self.client().session(
                app_id,
                arguments=args or None,
                environment=environment or None,
                alert_action=alert_action,
            )
            pid = getattr(session, "pid", None)
            logger.info(f"{_LOG_TAG} WDA launched {app_id} at {self.url} with pid {pid}")
        except Exception as exc:
            raise WDACLIError(
                f"{_LOG_TAG} WDA failed to launch {app_id} at {self.url}: {exc}"
            ) from exc
        if selector:
            logger.info(f"{_LOG_TAG} Pinning the WDA accept button to {selector}")
            try:
                session.appium_settings({AUTO_CLICK_ALERT_SETTING: selector})
            except Exception as exc:
                raise WDACLIError(
                    f"{_LOG_TAG} WDA launched {app_id} but would not take "
                    f"{AUTO_CLICK_ALERT_SETTING} at {self.url}, so its "
                    f"alerts monitor never starts: {exc}"
                ) from exc
        pid = getattr(session, "pid", None)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            logger.info(
                f"{_LOG_TAG} WDA launch response for {app_id} at {self.url} "
                "did not include a valid PID"
            )
            return None
        return pid

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
    "ACCEPT_ALERT_BUTTON_LABELS",
    "AUTO_CLICK_ALERT_SETTING",
    "WDA_PORT",
    "WDA_READY_TIMEOUT",
    "AlertAction",
    "build_accept_alert_selector",
    "WDACLI",
    "WDACLIError",
]
