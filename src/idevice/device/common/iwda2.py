"""Shared HTTP client for the on-device iwda2 service."""

from __future__ import annotations

import logging
import math
from typing import Optional

import requests

logger = logging.getLogger(__name__)

IWDA2_PORT = 18201
IWDA2_HTTP_TIMEOUT = 30.0
IWDA2_DEFAULT_MONITOR_DURATION = 180


class IWDA2Error(RuntimeError):
    """Raised when an iwda2 request that must succeed fails."""


def validate_normalized_coordinate(value: float, name: str) -> None:
    """Reject a tap coordinate that is not a finite number in ``[0, 1]``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a normalized coordinate in [0, 1]")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a normalized coordinate in [0, 1]")


def request(
    device_ip: str,
    route: str,
    *,
    params: Optional[dict[str, str]] = None,
    log_tag: str = "[IWDA2]",
) -> bool:
    """GET an iwda2 route and return whether it answered HTTP 200.

    Args:
        device_ip: Device address used to build ``http://<ip>:18201``.
        route: Path on the iwda2 service, e.g. ``/api/monitor/start``.
        params: Optional query string.
        log_tag: Prefix for warning logs.

    Returns:
        bool: ``True`` on HTTP 200. ``False`` when ``device_ip`` is empty,
        the request fails, or the status is not 200.
    """
    address = device_ip.strip()
    if not address:
        logger.warning(f"{log_tag} Cannot call iwda2 {route}: device_ip is empty")
        return False
    url = f"http://{address}:{IWDA2_PORT}{route}"
    try:
        response = requests.get(url, params=params, timeout=IWDA2_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning(f"{log_tag} iwda2 request failed: GET {url}: {exc}")
        return False
    if response.status_code != 200:
        logger.warning(
            f"{log_tag} GET {url} returned HTTP {response.status_code}: "
            f"{response.text!r}"
        )
        return False
    return True


def send_tap(
    device_ip: str,
    x: float,
    y: float,
    *,
    bundle_id: str = "",
    log_tag: str = "[IWDA2]",
) -> None:
    """Tap a normalized screen point through iwda2.

    Args:
        device_ip: Device address used to reach iwda2.
        x: Horizontal position in ``[0, 1]``.
        y: Vertical position in ``[0, 1]``.
        bundle_id: Optional foreground bundle id that anchors orientation.
        log_tag: Prefix for error messages.

    Raises:
        ValueError: If ``x`` or ``y`` is not a finite number in ``[0, 1]``.
        IWDA2Error: If ``device_ip`` is empty or iwda2 rejects the tap.
    """
    validate_normalized_coordinate(x, "x")
    validate_normalized_coordinate(y, "y")
    address = device_ip.strip()
    if not address:
        raise IWDA2Error(f"{log_tag} Cannot tap through iwda2: device_ip is empty")
    params: dict[str, float | str] = {"x": float(x), "y": float(y)}
    if bundle_id:
        params["bundleId"] = bundle_id
    url = f"http://{address}:{IWDA2_PORT}/api/tap"
    try:
        response = requests.get(url, params=params, timeout=IWDA2_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise IWDA2Error(
            f"{log_tag} iwda2 tap request failed: GET {url}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise IWDA2Error(
            f"{log_tag} iwda2 tap failed: GET {url} returned HTTP "
            f"{response.status_code}: {response.text!r}"
        )


def start_monitor(
    device_ip: str,
    duration: float = IWDA2_DEFAULT_MONITOR_DURATION,
    *,
    log_tag: str = "[IWDA2]",
) -> bool:
    """Start the iwda2 performance monitor.

    Args:
        device_ip: Device address used to reach iwda2.
        duration: Seconds the monitor should run.
        log_tag: Prefix for warning logs.

    Returns:
        bool: Whether iwda2 accepted the start request.

    Raises:
        ValueError: If ``duration`` is not a positive finite number.
    """
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("duration must be a positive finite number")
    return request(
        device_ip,
        "/api/monitor/start",
        params={"duration": format(float(duration), "g")},
        log_tag=log_tag,
    )


def stop_monitor(device_ip: str, *, log_tag: str = "[IWDA2]") -> bool:
    """Stop the iwda2 performance monitor.

    Args:
        device_ip: Device address used to reach iwda2.
        log_tag: Prefix for warning logs.

    Returns:
        bool: Whether iwda2 accepted the stop request.
    """
    return request(device_ip, "/api/monitor/stop", log_tag=log_tag)


class IWDA2Mixin:
    """Implement iwda2 ``tap`` / ``start_moniter`` / ``stop_moniter``.

    Hosts must expose :attr:`device_ip` and :attr:`package_name` (as
    :class:`DeviceBase` does). The misspelled monitor method names match the
    existing :class:`DeviceBase` API.
    """

    device_ip: str
    package_name: str

    @property
    def _iwda2_log_tag(self) -> str:
        """Return the log prefix for this device class."""
        return f"[{type(self).__name__}]"

    def tap(self, x: float, y: float, *, app_id: Optional[str] = None) -> None:
        """Tap a normalized screen point through iwda2."""
        target = (app_id or self.package_name).strip()
        send_tap(
            self.device_ip,
            x,
            y,
            bundle_id=target,
            log_tag=self._iwda2_log_tag,
        )

    def start_moniter(
        self, duration: float = IWDA2_DEFAULT_MONITOR_DURATION
    ) -> bool:
        """Start on-device performance monitoring through iwda2."""
        return start_monitor(
            self.device_ip, duration, log_tag=self._iwda2_log_tag
        )

    def stop_moniter(self) -> bool:
        """Stop on-device performance monitoring through iwda2."""
        return stop_monitor(self.device_ip, log_tag=self._iwda2_log_tag)
