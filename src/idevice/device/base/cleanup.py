"""Age-based cleanup of extracted Windows app package directories.

The Windows device (:class:`~idevice.device.windows.device.WindowsDevice`)
extracts each installed zip into its own subdirectory under a shared app dir
and never overwrites prior versions, so old extractions would otherwise
accumulate indefinitely. :func:`cleanup_old_packages` prunes stale package
directories by filesystem mtime; ``install`` invokes it at the start of every
installation. This mirrors the recordings cleanup in
:mod:`idevice.record.base.cleanup`, but prunes directories instead of files.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_TAG = "[PackageCleanup]"

_SECONDS_PER_DAY = 86400


def cleanup_old_packages(app_dir: Path, retention_days: int) -> int:
    """Delete subdirectories of ``app_dir`` older than ``retention_days``.

    Age is measured by each directory's modification time (mtime). This is
    best-effort: a non-positive ``retention_days`` or a missing directory is a
    no-op, non-directory entries are skipped, and per-directory failures are
    logged and skipped so cleanup never blocks an installation.

    Args:
        app_dir: Directory containing extracted package directories to prune.
        retention_days: Directories older than this many days are deleted;
            ``<= 0`` disables cleanup.

    Returns:
        The number of directories deleted.
    """
    if retention_days <= 0:
        return 0
    if not app_dir.is_dir():
        return 0

    cutoff = time.time() - retention_days * _SECONDS_PER_DAY
    deleted = 0
    for path in app_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(path)
        except OSError as exc:
            logger.debug(f"{_LOG_TAG} could not remove {path}: {exc}")
            continue
        deleted += 1

    if deleted:
        logger.info(
            f"{_LOG_TAG} removed {deleted} package(s) older than "
            f"{retention_days}d from {app_dir}"
        )
    return deleted
