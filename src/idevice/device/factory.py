"""Deprecated device factory.

.. deprecated::
    Use :meth:`idevice.device.device.Device.create` instead. ``create_device``
    and importing :class:`Platform` from this module are kept only for backward
    compatibility and will be removed in a future release.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

from idevice.device.android.device import AndroidDevice
from idevice.device.base.device import DeviceBase
from idevice.device.device import Platform
from idevice.device.ios.device import IOSDevice
from idevice.device.ios3.device import IOSDevice3
from idevice.device.windows.device import WindowsDevice

logger = logging.getLogger(__name__)

__all__ = ["Platform", "create_device"]


def create_device(platform: Platform, **kwargs: Any) -> DeviceBase:
    """Create a platform-specific ``DeviceBase`` subclass instance.

    .. deprecated::
        Use :meth:`idevice.device.device.Device.create` instead.
    """
    warnings.warn(
        "create_device() is deprecated; use Device.create() from "
        "idevice.device.device instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    logger.debug(f"Creating device for platform={platform} kwargs={kwargs}")
    if platform is Platform.IOS:
        device: DeviceBase = IOSDevice(**kwargs)
    elif platform is Platform.IOS3:
        device = IOSDevice3(**kwargs)
    elif platform is Platform.ANDROID:
        device = AndroidDevice(**kwargs)
    elif platform is Platform.WINDOWS:
        device = WindowsDevice(**kwargs)
    else:
        raise ValueError(f"Unsupported platform: {platform}")
    logger.info(f"Created {type(device).__name__} for device_id={device.device_id}")
    return device
