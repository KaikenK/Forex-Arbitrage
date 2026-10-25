"""Where bulky, regenerable research caches live.

The repository sits inside OneDrive. An intraday fetch writes one file per pair-hour —
thousands of small files — and OneDrive would sync every one of them, competing with the
fetch and the running app for disk and CPU. Caches therefore default to the local,
unsynced application-data folder, overridable with ``ARBEX_CACHE_DIR``.

Mirrors ``news_stream/src/retrieval/cache_paths.py`` so both projects share one cache root.
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


def intraday_cache_dir() -> Path:
    """Dukascopy hour files: one raw .bi5 per (pair, hour)."""
    return cache_root() / "intraday"


def histdata_cache_dir() -> Path:
    """HistData yearly 1-minute zips: one per (pair, year)."""
    return cache_root() / "histdata"
