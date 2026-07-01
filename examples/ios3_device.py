#!/usr/bin/env python3
"""Full :class:`IOSDevice3` example: app lifecycle, AFC, and Documents sandbox.

Prerequisites:
    - ``pymobiledevice3`` on PATH (or set ``IDEVICE_IOS3_BINARY``)
    - USB-connected, paired iOS device
    - Developer Mode enabled for ``launch_app`` / ``stop_app`` / ``host_is_running``
    - iOS 17+: active tunnel (``pymobiledevice3 remote start-tunnel``)

Examples:
    # Smoke test with defaults (Settings launch + AFC round-trip)
    uv run python examples/ios3_device.py

    # Install an IPA and exercise app-sandbox file transfer
    uv run python examples/ios3_device.py \\
        --ipa path/to/app.ipa \\
        --app-id com.example.app \\
        --sandbox-app-id com.example.app

    # Explicit UDID and verbose logging
    uv run python examples/ios3_device.py \\
        --udid 00000000-0000000000000000 -v
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from idevice.device.base.errors import AppNotInstalledError, CommandExecutionError
from idevice.device.config import ios3_binary
from idevice.device.device import Device
from idevice.device.ios3.device import IOSDevice3, IOSDevice3Error

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAUNCH_APP_ID = "com.apple.Preferences"
DEFAULT_AFC_REMOTE = "idevice_ios3_example_afc.txt"
DEFAULT_SANDBOX_REMOTE = "idevice_ios3_example_sandbox.txt"

logger = logging.getLogger(__name__)


def _resolve_udid(explicit: str | None) -> str:
    """Return ``explicit`` or the first UDID from ``pymobiledevice3 usbmux list``."""
    if explicit:
        return explicit
    binary = ios3_binary()
    try:
        completed = subprocess.run(
            [binary, "usbmux", "list"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"Failed to list iOS devices: {exc}") from exc

    try:
        devices = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Unexpected usbmux list output: {completed.stdout[:200]!r}"
        ) from exc

    if not devices:
        raise SystemExit(
            "No connected iOS device found. Pass --udid or connect a device via USB."
        )
    udid = devices[0].get("UniqueDeviceID") or devices[0].get("Identifier")
    if not udid:
        raise SystemExit(f"Could not read UDID from device entry: {devices[0]!r}")
    return str(udid)


def _skip_if_sandbox_unavailable(exc: CommandExecutionError) -> None:
    """Log and continue when House Arrest / AFC sandbox access is unavailable."""
    detail = f"{exc} {exc.stderr} {exc.stdout}"
    markers = (
        "InstallationLookupFailed",
        "ApplicationLookupFailed",
        "FILE_OPEN failed",
        "not found during afc",
    )
    if any(marker in detail for marker in markers):
        logger.warning(
            "App sandbox transfer skipped (House Arrest / AFC unavailable). "
            "Ensure the app is installed, supports file sharing, and the device "
            "trusts this host."
        )
        return
    raise exc


def _demo_launch_stop(device: IOSDevice3, app_id: str) -> None:
    """Launch and stop an already-installed app."""
    if not device.is_installed(app_id):
        logger.warning("Skip launch/stop: %s is not installed", app_id)
        return
    logger.info("Launching %s", app_id)
    device.launch_app(app_id)
    logger.info("Stopping %s", app_id)
    device.stop_app(app_id)


def _demo_wda(device: IOSDevice3) -> None:
    """Report whether WebDriverAgent / XCTest runner processes are up."""
    running = device.host_is_running()
    logger.info("WDA host running: %s", running)


def _demo_afc(device: IOSDevice3, remote_name: str) -> None:
    """Push/pull a small file via the public AFC service (/var/mobile/Media)."""
    with tempfile.TemporaryDirectory(prefix="idevice_ios3_afc_") as tmp:
        tmp_path = Path(tmp)
        local_push = tmp_path / "push.txt"
        local_pull = tmp_path / "pull.txt"
        payload = b"idevice ios3 afc example\n"
        local_push.write_bytes(payload)

        logger.info("AFC push %s -> device:%s", local_push.name, remote_name)
        device.push(local_push, remote_name)

        logger.info("AFC ls /Documents (first 10 entries)")
        entries = device.ls("/Documents")
        for entry in entries[:10]:
            logger.info("  %s", entry)
        if len(entries) > 10:
            logger.info("  ... (%d more)", len(entries) - 10)

        logger.info("AFC pull device:%s -> %s", remote_name, local_pull.name)
        device.pull(remote_name, local_pull)
        assert local_pull.read_bytes() == payload, "AFC round-trip payload mismatch"
        logger.info("AFC round-trip OK")


def _demo_apps_sandbox(
    device: IOSDevice3,
    app_id: str,
    remote_name: str,
    *,
    documents_only: bool,
) -> None:
    """Push/pull via ``apps push/pull`` (app container / Documents)."""
    if not device.is_installed(app_id):
        logger.warning("Skip apps sandbox: %s is not installed", app_id)
        return

    with tempfile.TemporaryDirectory(prefix="idevice_ios3_sandbox_") as tmp:
        tmp_path = Path(tmp)
        local_push = tmp_path / "sandbox_push.txt"
        local_pull = tmp_path / "sandbox_pull.txt"
        payload = b"idevice ios3 sandbox example\n"
        local_push.write_bytes(payload)

        logger.info(
            "Apps push %s -> %s:%s (documents_only=%s)",
            local_push.name,
            app_id,
            remote_name,
            documents_only,
        )
        try:
            device.push(
                local_push,
                remote_name,
                app_id=app_id,
                documents_only=documents_only,
            )
            device.pull(
                remote_name,
                local_pull,
                app_id=app_id,
                documents_only=documents_only,
            )
        except CommandExecutionError as exc:
            _skip_if_sandbox_unavailable(exc)
            return

        if not local_pull.is_file():
            logger.warning(
                "Apps sandbox pull did not create a local file; check file-sharing "
                "entitlements and device trust."
            )
            return
        assert local_pull.read_bytes() == payload, "Sandbox round-trip payload mismatch"
        logger.info("Apps sandbox round-trip OK")


def _demo_documents_api(device: IOSDevice3, app_id: str, remote_name: str) -> None:
    """Exercise async House Arrest helpers: exists, ls, push, pull."""
    if not device.is_installed(app_id):
        logger.warning("Skip documents API: %s is not installed", app_id)
        return

    with tempfile.TemporaryDirectory(prefix="idevice_ios3_docs_") as tmp:
        tmp_path = Path(tmp)
        local_push = tmp_path / "docs_push.txt"
        local_pull = tmp_path / "docs_pull.txt"
        payload = b"idevice ios3 documents example\n"
        local_push.write_bytes(payload)

        logger.info("documents_push %s -> %s:%s", local_push.name, app_id, remote_name)
        pushed = device.documents_push(app_id, local_push, remote_name)
        if not pushed:
            logger.warning("documents_push returned False (local missing or AFC error)")
            return

        exists = device.documents_exists(app_id, remote_name)
        logger.info("documents_exists(%s): %s", remote_name, exists)

        entries = device.documents_ls(app_id, ".")
        logger.info("documents_ls('.') (%d entries)", len(entries))
        for entry in entries[:10]:
            logger.info("  %s", entry)

        pulled = device.documents_pull(app_id, remote_name, local_pull)
        if not pulled:
            logger.warning("documents_pull returned False")
            return
        assert local_pull.read_bytes() == payload, "Documents round-trip payload mismatch"
        logger.info("Documents API round-trip OK")


def _demo_install_uninstall(
    device: IOSDevice3,
    ipa_path: Path,
    app_id: str,
) -> None:
    """Install an IPA, verify cache metadata, then uninstall."""
    if device.is_installed(app_id):
        logger.info("Uninstalling existing %s before reinstall", app_id)
        device.uninstall(app_id)

    logger.info("Installing %s (%s)", ipa_path.name, app_id)
    ok = device.install(ipa_path, app_id=app_id)
    if not ok:
        raise SystemExit(f"install() returned False for {ipa_path}")

    if not device.is_installed(app_id):
        raise SystemExit(f"{app_id} not reported as installed after install")

    pkg_name = device.get_installed_pkg_name(app_id)
    logger.info("Installed %s (cached package file: %s)", app_id, pkg_name or ipa_path.name)

    logger.info("Uninstalling %s", app_id)
    device.uninstall(app_id)
    if device.is_installed(app_id):
        raise SystemExit(f"{app_id} still installed after uninstall")
    logger.info("Install/uninstall round-trip OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Demonstrate IOSDevice3 app lifecycle and file transfer.",
    )
    parser.add_argument(
        "--udid",
        help="Device UDID (default: first device from `pymobiledevice3 usbmux list`)",
    )
    parser.add_argument(
        "--launch-app-id",
        default=DEFAULT_LAUNCH_APP_ID,
        help=f"Bundle id for launch/stop demo (default: {DEFAULT_LAUNCH_APP_ID})",
    )
    parser.add_argument(
        "--ipa",
        type=Path,
        help="Optional .ipa for install/uninstall round-trip",
    )
    parser.add_argument(
        "--app-id",
        help="Bundle id inside --ipa (required when --ipa is set)",
    )
    parser.add_argument(
        "--sandbox-app-id",
        help="Bundle id for app-sandbox and Documents API demos",
    )
    parser.add_argument(
        "--sandbox-remote",
        default=DEFAULT_SANDBOX_REMOTE,
        help=f"Remote file name under app Documents (default: {DEFAULT_SANDBOX_REMOTE})",
    )
    parser.add_argument(
        "--no-documents-only",
        action="store_true",
        help="Use full app container for apps push/pull (omit --documents)",
    )
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="Skip launch/stop demo",
    )
    parser.add_argument(
        "--skip-afc",
        action="store_true",
        help="Skip AFC push/pull demo",
    )
    parser.add_argument(
        "--skip-sandbox",
        action="store_true",
        help="Skip apps push/pull and documents_* demos",
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

    if args.ipa and not args.app_id:
        parser.error("--app-id is required when --ipa is set")

    udid = _resolve_udid(args.udid)
    logger.info("Using device UDID: %s", udid)

    try:
        device = Device.create("ios", device_id=udid, device_ip="")
    except IOSDevice3Error as exc:
        logger.error("%s", exc)
        return 1

    assert isinstance(device, IOSDevice3)

    if not args.skip_launch:
        try:
            _demo_launch_stop(device, args.launch_app_id)
        except AppNotInstalledError as exc:
            logger.error("Launch failed: %s", exc)
            return 1
        except CommandExecutionError as exc:
            logger.error(
                "Launch/stop failed (Developer Mode / tunnel / DDI?): %s", exc
            )
            return 1

    _demo_wda(device)

    if not args.skip_afc:
        try:
            _demo_afc(device, DEFAULT_AFC_REMOTE)
        except CommandExecutionError as exc:
            logger.error("AFC demo failed: %s", exc)
            return 1

    sandbox_app_id = args.sandbox_app_id or args.app_id
    if not args.skip_sandbox and sandbox_app_id:
        documents_only = not args.no_documents_only
        try:
            _demo_apps_sandbox(
                device,
                sandbox_app_id,
                args.sandbox_remote,
                documents_only=documents_only,
            )
            _demo_documents_api(device, sandbox_app_id, args.sandbox_remote)
        except CommandExecutionError as exc:
            logger.error("Sandbox demo failed: %s", exc)
            return 1

    if args.ipa:
        ipa_path = args.ipa.expanduser().resolve()
        if not ipa_path.is_file():
            logger.error("IPA not found: %s", ipa_path)
            return 1
        try:
            _demo_install_uninstall(device, ipa_path, args.app_id)
        except (CommandExecutionError, FileNotFoundError) as exc:
            logger.error("Install demo failed: %s", exc)
            return 1

    logger.info("All requested demos completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
