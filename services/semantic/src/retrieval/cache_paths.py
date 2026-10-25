"""Where bulky, regenerable caches live.

The repositories sit inside OneDrive. Thousands of small cache files there would be
synced continuously, competing with the app for disk and CPU. Caches therefore default to
the local, unsynced application-data directory, overridable with ``ARBEX_CACHE_DIR``.
"""
from __future__ import annotations

import os
from pathlib import Path

CACHE_ENV_VAR = "ARBEX_CACHE_DIR"


def cache_root() -> Path:
    """Root for all regenerable caches: env override, else the local app-data folder."""
    override = os.environ.get(CACHE_ENV_VAR)
    if override and override.strip():
        return Path(override.strip())
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "arbex"
    return Path.home() / ".cache" / "arbex"


def embedding_cache_dir() -> Path:
    return cache_root() / "embeddings"
