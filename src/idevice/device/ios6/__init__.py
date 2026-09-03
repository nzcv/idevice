"""iOS device backend driving WebDriverAgent, with ios4 for everything else."""

from idevice.device.ios6.device import IOSDevice6, IOSDevice6Error

__all__ = ["IOSDevice6", "IOSDevice6Error"]
