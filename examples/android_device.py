#!/usr/bin/env python3
"""Simple :class:`AndroidDevice` example: launch, file transfer, and swipe.

Prerequisites:
    - ``adb`` on PATH (or set ``IDEVICE_ADB_BINARY``)
    - USB-connected Android device with USB debugging enabled

Examples:
    # Smoke test with defaults (Settings launch + /sdcard push/pull)
    uv run python examples/android_device.py

    # Install an APK
    uv run python examples/android_device.py \\
        --apk path/to/app.apk \\
        --package com.example.app

    # Explicit serial and verbose logging
    uv run python examples/android_device.py --serial e8b2b043 -v
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from idevice.device.android.device import AndroidDevice, AndroidDeviceError
from idevice.device.base.errors import AppNotInstalledError
from idevice.device.config import adb_binary
from idevice.device.factory import Platform, create_device

DEFAULT_LAUNCH_PACKAGE = "com.android.settings"
DEFAULT_REMOTE = "/sdcard/Download/idevice_android_example.txt"
DEFAULT_DOCUMENTS_PACKAGE = "com.hypergryph.endfield.beyondtest2"

logger = logging.getLogger(__name__)


def _resolve_serial(explicit: str | None) -> str:
    """Return ``explicit`` or the first serial from ``adb devices``."""
    if explicit:
        return explicit
    binary = adb_binary()
    try:
        completed = subprocess.run(
            [binary, "devices"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"Failed to list Android devices: {exc}") from exc

    serials: list[str] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])

    if not serials:
        raise SystemExit(
            "No connected Android device found. Pass --serial or connect a device."
        )
    return serials[0]


def _demo_launch_stop(device: AndroidDevice, package: str) -> None:
    """Launch and stop an already-installed package."""
    if not device.is_installed(package):
        logger.warning("Skip launch/stop: %s is not installed", package)
        return
    logger.info("Launching %s", package)
    device.launch_app(package)
    logger.info("Stopping %s", package)
    device.stop_app(package)


def _demo_push_pull(device: AndroidDevice, remote: str) -> None:
    """Push/pull a small file via ``adb push`` / ``adb pull``."""
    with tempfile.TemporaryDirectory(prefix="idevice_android_") as tmp:
        tmp_path = Path(tmp)
        local_push = tmp_path / "push.txt"
        local_pull = tmp_path / "pull.txt"
        payload = b"idevice android example\n"
        local_push.write_bytes(payload)

        logger.info("Push %s -> %s", local_push.name, remote)
        device.push(local_push, remote)

        parent = str(Path(remote).parent)
        logger.info("List %s (first 10 entries)", parent)
        try:
            entries = device.ls(parent)
            for entry in entries[:10]:
                logger.info("  %s", entry)
            if len(entries) > 10:
                logger.info("  ... (%d more)", len(entries) - 10)
        except Exception as exc:
            logger.warning("ls skipped: %s", exc)

        logger.info("Pull %s -> %s", remote, local_pull.name)
        device.pull(remote, local_pull)
        assert local_pull.read_bytes() == payload, "Push/pull payload mismatch"
        logger.info("Push/pull round-trip OK")


def _demo_documents(device: AndroidDevice, package: str) -> None:
    """Inspect and pull from an app's external files dir (Documents sandbox).

    Targets ``/sdcard/Android/data/<package>/files``.
    """
    root = device.documents_root(package)
    logger.info("Documents root for %s: %s", package, root)

    if not device.documents_exists(package, "."):
        logger.warning(
            "Documents dir does not exist (app not installed or no files yet): %s",
            root,
        )
        return

    try:
        entries = device.documents_ls(package, ".")
    except AndroidDeviceError as exc:
        logger.warning("documents_ls failed: %s", exc)
        return

    logger.info("Found %d entr(ies) under files/", len(entries))
    for entry in entries[:10]:
        logger.info("  %s", entry)
    if len(entries) > 10:
        logger.info("  ... (%d more)", len(entries) - 10)

    if not entries:
        logger.info("Nothing to pull; files/ is empty")
        return

    first = entries[0]
    with tempfile.TemporaryDirectory(prefix="idevice_android_docs_") as tmp:
        dest = Path(tmp) / Path(first).name
        logger.info("Pulling %s -> %s", first, dest)
        if device.documents_pull(package, first, dest):
            logger.info("Pulled %s (%d bytes)", dest.name, dest.stat().st_size if dest.is_file() else -1)
        else:
            logger.warning("Pull failed or path missing: %s", first)


def _demo_swipe(device: AndroidDevice) -> None:
    """Swipe upward on the screen (home screen or current app)."""
    logger.info("Swipe up (100, 800) -> (100, 200)")
    device.swipe(100, 800, 100, 200, duration_ms=300)


def _demo_install_uninstall(
    device: AndroidDevice, apk_path: Path, package: str
) -> None:
    """Install an APK, verify, then uninstall."""
    logger.info("Installing %s", apk_path.name)
    device.install(apk_path, app_id=package)
    if not device.is_installed(package):
        raise RuntimeError(f"Package not reported as installed: {package}")
    logger.info("Installed %s", package)
    logger.info("Uninstalling %s", package)
    device.uninstall(package)
    if device.is_installed(package):
        raise RuntimeError(f"Package still installed after uninstall: {package}")
    logger.info("Install/uninstall OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serial",
        help="ADB device serial (default: first device from `adb devices`)",
    )
    parser.add_argument(
        "--launch-package",
        default=DEFAULT_LAUNCH_PACKAGE,
        help=f"Package to launch/stop (default: {DEFAULT_LAUNCH_PACKAGE})",
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help=f"Remote path for push/pull demo (default: {DEFAULT_REMOTE})",
    )
    parser.add_argument("--apk", type=Path, help="APK to install and uninstall")
    parser.add_argument(
        "--package",
        help="Package name for --apk (required when --apk is set)",
    )
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="Skip launch/stop demo",
    )
    parser.add_argument(
        "--skip-transfer",
        action="store_true",
        help="Skip push/pull demo",
    )
    parser.add_argument(
        "--skip-swipe",
        action="store_true",
        help="Skip swipe demo",
    )
    parser.add_argument(
        "--documents-package",
        default=DEFAULT_DOCUMENTS_PACKAGE,
        help=(
            "Package whose external files dir to inspect/pull "
            f"(default: {DEFAULT_DOCUMENTS_PACKAGE})"
        ),
    )
    parser.add_argument(
        "--skip-documents",
        action="store_true",
        help="Skip documents (external files) inspect/pull demo",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.apk and not args.package:
        parser.error("--package is required when --apk is set")

    serial = _resolve_serial(args.serial)
    logger.info("Using device serial: %s", serial)

    try:
        device = create_device(Platform.ANDROID, device_id=serial)
    except AndroidDeviceError as exc:
        logger.error("%s", exc)
        return 1

    assert isinstance(device, AndroidDevice)

    if not args.skip_launch:
        try:
            _demo_launch_stop(device, args.launch_package)
        except AppNotInstalledError as exc:
            logger.error("Launch failed: %s", exc)
            return 1

    if not args.skip_transfer:
        try:
            _demo_push_pull(device, args.remote)
        except (FileNotFoundError, AndroidDeviceError) as exc:
            logger.error("Push/pull failed: %s", exc)
            return 1

    if not args.skip_swipe:
        _demo_swipe(device)

    if not args.skip_documents:
        try:
            _demo_documents(device, args.documents_package)
        except AndroidDeviceError as exc:
            logger.error("Documents demo failed: %s", exc)
            return 1

    if args.apk:
        apk_path = args.apk.expanduser().resolve()
        if not apk_path.is_file():
            logger.error("APK not found: %s", apk_path)
            return 1
        try:
            _demo_install_uninstall(device, apk_path, args.package)
        except (AndroidDeviceError, RuntimeError) as exc:
            logger.error("Install demo failed: %s", exc)
            return 1

    logger.info("All requested demos completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
