#!/usr/bin/env python3
"""Full :class:`Host` example: keeper-backed iOS memory-measurement workflow.

The host runs on the **mac host** and drives a run end to end: it launches the
xctest run via the EndlessKeeper control server, waits for the on-device
RemoteControlTest runner to come up, opens/closes a measured window, and
optionally exports the captured memory graphs to a presigned URL.

Prerequisites:
    - macOS host with the EndlessKeeper control server reachable (default :18000)
    - A target iOS device reachable by IP, with the runner installed
    - Either ``--keeper-ip/--device-udid/--device-ip`` flags or the controller's
      ``GAUTO_HOST_*`` / ``GAUTO_DEVICE_*`` environment variables (see ``--from-env``)

Examples:
    # Build from the GAUTO_* environment and run the full measurement workflow
    uv run python examples/host_example.py --from-env \\
        --bundle-id com.rm42.TrashDash --duration 60

    # Build explicitly and drive each step (launch -> measure -> kill)
    uv run python examples/host_example.py \\
        --keeper-ip 192.168.1.7 \\
        --device-udid 00008120-00123D323 \\
        --device-ip 192.168.1.5 \\
        --bundle-id com.rm42.TrashDash --duration 60

    # Measure and export the memgraphs to a presigned PUT URL
    uv run python examples/host_example.py --from-env \\
        --bundle-id com.rm42.TrashDash --duration 60 \\
        --export-url "https://bucket.s3.amazonaws.com/...&X-Amz-Signature=..."

    # Health probe only (keeper + runner reachability), verbose logging
    uv run python examples/host_example.py --from-env --health-only -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from idevice.host import Host, HostBase, config
from idevice.host.base.errors import HostError, HostTimeoutError

logger = logging.getLogger(__name__)

DEFAULT_BUNDLE_ID = "com.rm42.TrashDash"
DEFAULT_DURATION_S = 60.0


def _build_host(args: argparse.Namespace) -> HostBase:
    """Construct a :class:`HostBase` from ``--from-env`` or explicit flags."""
    if args.from_env:
        logger.info("Building host from GAUTO_* environment")
        return Host.from_env()
    return Host.create(
        "macos",
        keeper_ip=args.keeper_ip,
        keeper_port=args.keeper_port,
        device_udid=args.device_udid,
        device_ip=args.device_ip,
        keeper_id=args.keeper_id,
    )


def _demo_health(host: HostBase) -> bool:
    """Probe keeper + on-device runner reachability."""
    logger.info("Probing keeper at %s:%s", host.keeper_ip, host.keeper_port)
    healthy = host.health()
    logger.info("Host health (keeper + runner reachable): %s", healthy)
    return healthy


def _demo_measure(host: HostBase, args: argparse.Namespace) -> dict:
    """Run the full launch -> wait -> measure -> (export) workflow."""
    logger.info(
        "Measuring %s for %.0fs on device %s",
        args.bundle_id,
        args.duration,
        host.device_udid,
    )
    summary = host.measure(
        args.bundle_id,
        duration_s=args.duration,
        export_url=args.export_url,
        content_type=args.content_type,
    )
    logger.info("Measurement summary:\n%s", json.dumps(summary, indent=2, default=str))
    return summary


def _demo_steps(host: HostBase, args: argparse.Namespace) -> None:
    """Drive each step explicitly so failures localize to a single call."""
    logger.info("Launching run for %s", host.device_udid)
    record = host.launch()
    logger.info("Launch record:\n%s", json.dumps(record, indent=2, default=str))

    logger.info("Waiting for the on-device runner to become ready")
    host.wait_until_ready(timeout=args.ready_timeout)

    logger.info("start_measuring %s", args.bundle_id)
    host.start_measuring(args.bundle_id)
    logger.info("Measuring for %.0fs", args.duration)
    time.sleep(args.duration)
    logger.info("stop_measuring")
    host.stop_measuring()

    if args.export_url:
        logger.info("Exporting memgraphs to presigned URL")
        result = host.export(args.export_url, args.content_type)
        logger.info("Export result:\n%s", json.dumps(result, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    source = parser.add_argument_group("host source")
    source.add_argument(
        "--from-env",
        action="store_true",
        help="Build the host from GAUTO_HOST_* / GAUTO_DEVICE_* env vars",
    )
    source.add_argument(
        "--keeper-ip",
        help="EndlessKeeper control-server IP (required unless --from-env)",
    )
    source.add_argument(
        "--keeper-port",
        type=int,
        default=config.DEFAULT_KEEPER_PORT,
        help=f"Keeper control-server port (default: {config.DEFAULT_KEEPER_PORT})",
    )
    source.add_argument(
        "--keeper-id",
        default="",
        help="Optional keeper/controller id (informational)",
    )
    source.add_argument(
        "--device-udid",
        help="Target device UDID (required unless --from-env)",
    )
    source.add_argument(
        "--device-ip",
        help="Target device IP (required unless --from-env)",
    )

    run = parser.add_argument_group("measurement")
    run.add_argument(
        "--bundle-id",
        default=DEFAULT_BUNDLE_ID,
        help=f"App bundle id to measure (default: {DEFAULT_BUNDLE_ID})",
    )
    run.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Measured window duration in seconds (default: {DEFAULT_DURATION_S:.0f})",
    )
    run.add_argument(
        "--ready-timeout",
        type=float,
        default=config.DEFAULT_READY_TIMEOUT,
        help=(
            "Seconds to wait for the runner to become ready "
            f"(default: {config.DEFAULT_READY_TIMEOUT:.0f})"
        ),
    )
    run.add_argument(
        "--export-url",
        help="Optional presigned PUT URL to export the captured memgraphs to",
    )
    run.add_argument(
        "--content-type",
        help="Optional content type the --export-url was signed for",
    )

    mode = parser.add_argument_group("mode")
    mode.add_argument(
        "--health-only",
        action="store_true",
        help="Only probe keeper + runner health, then exit",
    )
    mode.add_argument(
        "--steps",
        action="store_true",
        help="Drive launch/measure/export as explicit steps instead of measure()",
    )
    mode.add_argument(
        "--keep-alive",
        action="store_true",
        help="Skip the final kill() so the run keeps running on the keeper",
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

    if not args.from_env and not (args.keeper_ip and args.device_udid and args.device_ip):
        parser.error(
            "Provide --from-env, or all of --keeper-ip, --device-udid and --device-ip"
        )

    try:
        host = _build_host(args)
    except HostError as exc:
        logger.error("Failed to build host: %s", exc)
        return 1

    logger.info(
        "Host bound: keeper=%s:%s device=%s @ %s",
        host.keeper_ip,
        host.keeper_port,
        host.device_udid,
        host.device_ip,
    )

    if args.health_only:
        return 0 if _demo_health(host) else 1

    try:
        if args.steps:
            _demo_steps(host, args)
        else:
            _demo_measure(host, args)
    except HostTimeoutError as exc:
        logger.error("Runner did not become ready: %s", exc)
        return 1
    except HostError as exc:
        logger.error("Measurement failed: %s", exc)
        return 1
    finally:
        if not args.keep_alive:
            try:
                logger.info("Killing run for %s", host.device_udid)
                host.kill()
            except HostError as exc:
                logger.warning("kill() failed (run may already be gone): %s", exc)

    logger.info("All requested demos completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
