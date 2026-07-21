"""Unit tests for age-based recording cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

from idevice.record.base.cleanup import cleanup_old_recordings


def _make_recording(directory: Path, name: str, *, age_days: float) -> Path:
    """Create a file in ``directory`` with an mtime ``age_days`` in the past."""
    path = directory / name
    path.write_bytes(b"data")
    past = time.time() - age_days * 86400
    os.utime(path, (past, past))
    return path


def test_deletes_files_older_than_retention(tmp_path: Path) -> None:
    old = _make_recording(tmp_path, "dev_old.mp4", age_days=3)
    deleted = cleanup_old_recordings(tmp_path, retention_days=1)
    assert deleted == 1
    assert not old.exists()


def test_keeps_fresh_files(tmp_path: Path) -> None:
    fresh = _make_recording(tmp_path, "dev_fresh.mp4", age_days=0.1)
    deleted = cleanup_old_recordings(tmp_path, retention_days=1)
    assert deleted == 0
    assert fresh.exists()


def test_ignores_non_mp4_files(tmp_path: Path) -> None:
    other = _make_recording(tmp_path, "dev_old.log", age_days=10)
    deleted = cleanup_old_recordings(tmp_path, retention_days=1)
    assert deleted == 0
    assert other.exists()


def test_non_positive_retention_is_noop(tmp_path: Path) -> None:
    old = _make_recording(tmp_path, "dev_old.mp4", age_days=10)
    assert cleanup_old_recordings(tmp_path, retention_days=0) == 0
    assert cleanup_old_recordings(tmp_path, retention_days=-5) == 0
    assert old.exists()


def test_missing_directory_is_safe(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert cleanup_old_recordings(missing, retention_days=1) == 0
