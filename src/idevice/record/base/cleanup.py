"""Age-based cleanup of local screen recordings.

The Windows (``ffmpeg``) and Android (``scrcpy``) recorders write MP4 files to a
shared output directory and never overwrite prior runs, so old recordings would
otherwise accumulate indefinitely. :func:`cleanup_old_recordings` prunes stale
files by filesystem mtime; the concrete recorders invoke it at the start of
every recording. The macOS recorder stores files on a remote iRecord server and
is intentionally excluded.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_TAG = "[RecordCleanup]"

_SECONDS_PER_DAY = 86400


def cleanup_old_recordings(output_dir: Path, retention_days: int) -> int:
    """Delete ``*.mp4`` files in ``output_dir`` older than ``retention_days``.

    Age is measured by each file's modification time (mtime), which matches the
    ``{udid}_{YYYYMMDD_HHMMSS}.mp4`` naming without parsing timestamps. This is
    best-effort: a non-positive ``retention_days`` or a missing directory is a
    no-op, and per-file failures are logged and skipped so cleanup never blocks a
    recording.

    Args:
        output_dir: Directory containing recordings to prune.
        retention_days: Files older than this many days are deleted; ``<= 0``
            disables cleanup.

    Returns:
        The number of files deleted.
    """
    if retention_days <= 0:
        return 0
    if not output_dir.is_dir():
        return 0

    cutoff = time.time() - retention_days * _SECONDS_PER_DAY
    deleted = 0
    for path in output_dir.glob("*.mp4"):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError as exc:
            logger.debug(f"{_LOG_TAG} could not remove {path}: {exc}")
            continue
        deleted += 1

    if deleted:
        logger.info(
            f"{_LOG_TAG} removed {deleted} recording(s) older than "
            f"{retention_days}d from {output_dir}"
        )
    return deleted
