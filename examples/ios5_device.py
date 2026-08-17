#!/usr/bin/env python3
"""Install, launch, and snapshot an iOS game through ``xcrun devicectl``.

Prerequisites:
    * macOS with Xcode installed (``xcrun devicectl`` is Apple's CoreDevice CLI).
    * A paired iOS device with Developer Mode enabled.
    * ``ios4`` on PATH when ``--memgraph`` is used or as the first screenshot
      fallback; CoreDevice has no memory-graph service.
    * A preinstalled iwda2 Runner when ``--run-iwda2`` is used or as the final
      screenshot fallback.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from idevice.device.ios5.device import IOSDevice5


def _environment(entries: list[str], malloc_stack_logging: bool) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` entries into a launch environment."""
    environment: dict[str, str] = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        if not separator or not key:
            raise ValueError(f"expected KEY=VALUE, got {entry!r}")
        environment[key] = value
    if malloc_stack_logging:
        environment.setdefault("MallocStackLogging", "1")
    return environment


def main() -> None:
    """Parse command-line options, optionally install, and launch the game."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--udid", help="Device UDID; defaults to the USB device")
    parser.add_argument("--device-ip", default="", help="Device IP, for iwda2 access")
    parser.add_argument("--ipa", type=Path, help="IPA or app directory to install")
    parser.add_argument("--app-id", required=True, help="Game bundle identifier")
    parser.add_argument(
        "--memgraph",
        type=Path,
        help="Capture the launched process to this .memgraph file (needs ios4)",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Capture the screen to this PNG after launching",
    )
    parser.add_argument(
        "--run-iwda2",
        action="store_true",
        help="Start the preinstalled iwda2 Runner before launching the game",
    )
    parser.add_argument(
        "--arg",
        action="append",
        default=[],
        help="One game argv entry; repeat to preserve ordering",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="One launch environment entry; repeat as needed",
    )
    parser.add_argument(
        "--malloc-stack-logging",
        action="store_true",
        help="Launch with MallocStackLogging=1",
    )
    options = parser.parse_args()

    udid = options.udid or IOSDevice5.default_udid()
    device = IOSDevice5(
        udid, device_ip=options.device_ip, package_name=options.app_id
    )

    if options.ipa and not device.install(options.ipa, app_id=options.app_id):
        raise SystemExit(f"Installation failed: {options.ipa}")

    if options.run_iwda2:
        device.run_iwda2().join()
        if device.iwda2_startup_error is not None:
            raise SystemExit(f"iwda2 startup failed: {device.iwda2_startup_error}")
        print(f"iwda2 Runner is ready with device PID {device.iwda2_process_id}")

    environment = _environment(options.env, options.malloc_stack_logging)
    device.launch_app(
        options.app_id,
        args=options.arg or None,
        environment=environment or None,
    )
    print(f"Launched {options.app_id} with PID {device.last_launch_pid}")

    if options.screenshot and not device.screenshot(options.screenshot):
        raise SystemExit(f"Screenshot failed: {options.screenshot}")

    if options.memgraph:
        snapshot = device.capture_memgraph(options.memgraph)
        print(f"Memory graph written to {snapshot}")


if __name__ == "__main__":
    main()
