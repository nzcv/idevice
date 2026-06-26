#!/usr/bin/env python3
"""Full :class:`Host` example: keeper-backed iOS memory-measurement workflow.

The host runs on the **mac host** and drives a run end to end: it launches the
xctest run via the EndlessKeeper control server, waits for the on-device
RemoteControlTest runner to come up, launches the target app, captures a
memgraph, and optionally exports the captured memory graphs to a presigned URL.

Prerequisites:
    - macOS host with the EndlessKeeper control server reachable (default :18000)
    - A target iOS device reachable by IP, with the runner installed
    - Either ``--keeper-ip/--device-udid/--device-ip`` flags or the controller's
      ``GAUTO_*`` environment variables (see ``--from-env``)

Examples:
    # Build from the GAUTO_* environment and capture a memgraph
    uv run python examples/host_example.py --from-env \\
        --bundle-id com.rm42.TrashDash

    # Build explicitly and drive each step (launch app -> capture -> kill)
    uv run python examples/host_example.py \\
        --keeper-ip 192.168.1.7 \\
        --device-udid 00008120-00123D323 \\
        --device-ip 192.168.1.5 \\
        --bundle-id com.rm42.TrashDash --steps

    # Capture and export the memgraphs to a presigned PUT URL
    uv run python examples/host_example.py --from-env \\
        --bundle-id com.rm42.TrashDash \\
        --export-url "https://bucket.s3.amazonaws.com/...&X-Amz-Signature=..."

    # Health probe only (keeper reachability), verbose logging
    uv run python examples/host_example.py --from-env --health-only -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from idevice.host import Host, HostBase, HostError, HostTimeoutError, config

logger = logging.getLogger(__name__)

DEFAULT_BUNDLE_ID = "com.rm42.TrashDash"
DEFAULT_CAPTURE_TIMEOUT_S = 60.0


def _build_host(args: argparse.Namespace) -> HostBase:
    """Construct a host from ``--from-env`` or explicit flags."""
    if args.from_env:
        logger.info("Building host from GAUTO_* environment")
        return Host.from_env()
    return Host.create(
        platform="macos",
        keeper_ip=args.keeper_ip,
        keeper_port=args.keeper_port,
        device_udid=args.device_udid,
        device_ip=args.device_ip,
        keeper_id=args.keeper_id,
        bundle_id=args.bundle_id,
    )


def _kill_host(host: HostBase) -> dict:
    """Tear down the keeper run for ``host``."""
    return host.kill()


def _demo_health(host: HostBase) -> bool:
    """Probe keeper reachability."""
    logger.info("Probing keeper at %s:%s", host.keeper_ip, host.keeper_port)
    healthy = host.health()
    logger.info("Host health (keeper reachable): %s", healthy)
    return healthy


def _demo_capture(host: HostBase, args: argparse.Namespace) -> dict:
    """Run launch_app -> capture_memgraph -> (export) in one shot."""
    logger.info(
        "Capturing memgraph for %s on device %s",
        args.bundle_id,
        host.device_udid,
    )
    host.launch_app(timeout=args.ready_timeout)
    result = host.capture_memgraph(timeout=args.capture_timeout)
    summary = {"capture": result}
    if args.export_url:
        logger.info("Exporting memgraphs to presigned URL")
        summary["export"] = host.export(args.export_url, args.content_type)
    logger.info("Measurement summary:\n%s", json.dumps(summary, indent=2, default=str))
    return summary


def _demo_steps(host: HostBase, args: argparse.Namespace) -> None:
    """Drive each step explicitly so failures localize to a single call."""
    logger.info("Launching app %s on %s", args.bundle_id, host.device_udid)
    launch_result = host.launch_app(timeout=args.ready_timeout)
    logger.info("Launch result:\n%s", json.dumps(launch_result, indent=2, default=str))

    logger.info("Capturing memgraph (timeout=%.0fs)", args.capture_timeout)
    capture_result = host.capture_memgraph(timeout=args.capture_timeout)
    logger.info("Capture result:\n%s", json.dumps(capture_result, indent=2, default=str))

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
        help="Build the host from GAUTO_* environment variables",
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
        "--capture-timeout",
        type=float,
        default=DEFAULT_CAPTURE_TIMEOUT_S,
        help=(
            "Seconds to wait for memgraph capture "
            f"(default: {DEFAULT_CAPTURE_TIMEOUT_S:.0f})"
        ),
    )
    run.add_argument(
        "--ready-timeout",
        type=float,
        default=config.DEFAULT_READY_TIMEOUT,
        help=(
            "Seconds to wait for the runner/app to become ready "
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
        help="Only probe keeper health, then exit",
    )
    mode.add_argument(
        "--steps",
        action="store_true",
        help="Drive launch/capture/export as explicit steps instead of one shot",
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
        "Host bound: keeper=%s:%s device=%s @ %s bundle=%s",
        host.keeper_ip,
        host.keeper_port,
        host.device_udid,
        host.device_ip,
        getattr(host, "bundle_id", args.bundle_id),
    )

    if args.health_only:
        return 0 if _demo_health(host) else 1

    try:
        if args.steps:
            _demo_steps(host, args)
        else:
            _demo_capture(host, args)
    except HostTimeoutError as exc:
        logger.error("Timed out: %s", exc)
        return 1
    except HostError as exc:
        logger.error("Measurement failed: %s", exc)
        return 1
    finally:
        if not args.keep_alive:
            try:
                logger.info("Killing run for %s", host.device_udid)
                _kill_host(host)
            except HostError as exc:
                logger.warning("kill failed (run may already be gone): %s", exc)

    logger.info("All requested demos completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
