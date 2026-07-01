#!/usr/bin/env python3
"""Full :class:`Record` example: iRecord-backed iOS screen-recording workflow.

The recorder runs on the **mac host** and drives a recording of a USB-connected
iOS device end to end via the iRecord control server: it starts a recording,
optionally polls status, then stops it (optionally uploading and/or downscaling
the finished file).

Prerequisites:
    - macOS host running the iRecord server (``irecord server``, default :8080)
    - A target iOS device connected by USB, with "Trust This Computer" accepted
    - Either ``--server-ip/--device-udid`` flags or the controller's ``GAUTO_*``
      environment variables (see ``--from-env``)

Examples:
    # Build from the environment, record 10s, then stop
    uv run python examples/record_example.py --from-env --duration 10

    # Build explicitly and drive start -> status -> stop with upload + downscale
    uv run python examples/record_example.py \\
        --server-ip 127.0.0.1 \\
        --device-udid 00000000-0000000000000000 \\
        --duration 10 --upload --preset 720p

    # Server-side auto-stop after 2h (no local wait), then exit
    uv run python examples/record_example.py --from-env --timeout 2h --no-wait

    # Health probe only (iRecord reachability), verbose logging
    uv run python examples/record_example.py --from-env --health-only -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from idevice.record import Record, RecordBase, RecordError, config

logger = logging.getLogger(__name__)

DEFAULT_DURATION_S = 10.0


def _build_recorder(args: argparse.Namespace) -> RecordBase:
    """Construct a recorder from ``--from-env`` or explicit flags."""
    if args.from_env:
        logger.info("Building recorder from GAUTO_* / IRECORD_* environment")
        return Record.from_env()
    return Record.create(
        record_type="macos",
        server_ip=args.server_ip,
        server_port=args.server_port,
        device_udid=args.device_udid,
    )


def _demo_health(recorder: RecordBase) -> bool:
    """Probe iRecord reachability."""
    logger.info("Probing iRecord at %s:%s", recorder.server_ip, recorder.server_port)
    healthy = recorder.health()
    logger.info("Recorder health (iRecord reachable): %s", healthy)
    return healthy


def _demo_record(recorder: RecordBase, args: argparse.Namespace) -> dict:
    """Run start -> (poll status) -> stop and return a summary."""
    logger.info("Starting recording for device %s", recorder.device_udid)
    start_result = recorder.start(timeout=args.timeout)
    logger.info("Start result:\n%s", json.dumps(start_result, indent=2, default=str))

    summary: dict = {"start": start_result}

    if args.no_wait:
        logger.info("--no-wait set; leaving the recording running on the server")
        return summary

    logger.info("Recording for %.0fs (polling status)...", args.duration)
    deadline = time.monotonic() + args.duration
    while time.monotonic() < deadline:
        status = recorder.status()
        logger.info("Status: %s", status.get("state"))
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    logger.info("Stopping recording (upload=%s, preset=%s)", args.upload, args.preset)
    stop_result = recorder.stop(upload=args.upload, preset=args.preset)
    logger.info("Stop result:\n%s", json.dumps(stop_result, indent=2, default=str))
    summary["stop"] = stop_result
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    source = parser.add_argument_group("recorder source")
    source.add_argument(
        "--from-env",
        action="store_true",
        help="Build the recorder from GAUTO_* / IRECORD_* environment variables",
    )
    source.add_argument(
        "--server-ip",
        help="iRecord control-server IP (required unless --from-env)",
    )
    source.add_argument(
        "--server-port",
        type=int,
        default=config.DEFAULT_RECORD_PORT,
        help=f"iRecord control-server port (default: {config.DEFAULT_RECORD_PORT})",
    )
    source.add_argument(
        "--device-udid",
        help="Target device UDID (required unless --from-env)",
    )

    run = parser.add_argument_group("recording")
    run.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Seconds to record locally before stopping (default: {DEFAULT_DURATION_S:.0f})",
    )
    run.add_argument(
        "--timeout",
        default=None,
        help="Server-side auto-stop duration (e.g. 2h, 30m, 90s, or seconds)",
    )
    run.add_argument(
        "--upload",
        action="store_true",
        help="Ask the server to upload the finished file on stop",
    )
    run.add_argument(
        "--preset",
        choices=["480p", "540p", "720p", "1080p", "2160p"],
        default=None,
        help="Downscale preset applied on stop",
    )

    mode = parser.add_argument_group("mode")
    mode.add_argument(
        "--health-only",
        action="store_true",
        help="Only probe iRecord health, then exit",
    )
    mode.add_argument(
        "--no-wait",
        action="store_true",
        help="Start the recording and exit without stopping (rely on --timeout)",
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

    if not args.from_env and not (args.server_ip and args.device_udid):
        parser.error("Provide --from-env, or both of --server-ip and --device-udid")

    try:
        recorder = _build_recorder(args)
    except RecordError as exc:
        logger.error("Failed to build recorder: %s", exc)
        return 1

    logger.info(
        "Recorder bound: iRecord=%s:%s device=%s",
        recorder.server_ip,
        recorder.server_port,
        recorder.device_udid,
    )

    if args.health_only:
        return 0 if _demo_health(recorder) else 1

    try:
        _demo_record(recorder, args)
    except RecordError as exc:
        logger.error("Recording failed: %s", exc)
        return 1

    logger.info("All requested demos completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
