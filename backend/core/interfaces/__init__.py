"""
Interfaces Package

Contains abstract interfaces for the modular data source architecture.
This enables plugin-based data ingestion from multiple FX data providers.
"""

from backend.core.interfaces.data_source import DataSourceInterface, DataSourceConfig
from backend.core.interfaces.normalized_tick import NormalizedTick, TickNormalizer

__all__ = [
    "DataSourceInterface",
    "DataSourceConfig", 
    "NormalizedTick",
    "TickNormalizer"
]
