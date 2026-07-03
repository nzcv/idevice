"""Persistent cache for device app_id -> package_path mappings."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from idevice.device.config import user_data_dir

logger = logging.getLogger("[AppCache]")


class InstalledAppCache:
    """Persist ``app_id -> package_path`` mappings per device in user data dir."""

    def __init__(self, device_id: str, *, cache_dir: Path | None = None) -> None:
        if not device_id:
            raise ValueError("device_id is required and must be a non-empty string")
        self._device_id = device_id
        self._cache_dir = cache_dir or user_data_dir()
        self._cache_file = self._cache_dir / f"{device_id}.json"
        logger.debug(f"AppCache initialized for device {device_id} with cache file {self._cache_file}")

    def _load(self) -> dict[str, dict[str, str]]:
        if not self._cache_file.exists():
            return {}
        with open(self._cache_file, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self._cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def add(self, app_id: str, package_path: str) -> None:
        """Record ``app_id`` mapped to ``package_path`` for this device."""
        logger.debug(f"Caching app_id={app_id} -> {package_path} for device {self._device_id}")
        data = self._load()
        data[app_id] = package_path
        self._save(data)

    def remove(self, app_id: str) -> None:
        """Remove the cached mapping for ``app_id`` on this device."""
        logger.debug(f"Removing cached app_id={app_id} for device {self._device_id}")
        data = self._load()
        data.pop(app_id, None)
        self._save(data)

    def get(self, app_id: str) -> Path | None:
        """Return the cached package path for ``app_id``, if present."""
        path_str = self._load().get(app_id, None)
        return Path(path_str) if path_str else None
