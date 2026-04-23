"""
Data Sources Package

Contains all data source plugins implementing the DataSourceInterface.
Each data source converts its native format to RawTick for normalization.

Available Sources:
- MT5DataSource: MetaTrader 5 tick streaming
- SyntheticDataSource: Simulated feeds for testing/research
- SessionAwareSyntheticSource: Session/provider-aware USD/INR synthetic feeds
- RESTDataSource: REST API polling (for INR/USD, etc.)
- PlaybackDataSource: Recorded data replay for backtesting
"""

try:
    from backend.core.data_sources.mt5_data_source import MT5DataSource
except ModuleNotFoundError:
    MT5DataSource = None
from backend.core.data_sources.synthetic_data_source import SyntheticDataSource, SyntheticConfig
from backend.core.data_sources.rest_data_source import RESTDataSource, RESTSourceConfig
from backend.core.data_sources.playback_data_source import PlaybackDataSource, PlaybackConfig
from backend.core.data_sources.session_synthetic_source import (
    SessionAwareSyntheticSource,
    SharedReferencePrice,
    create_synthetic_sources,
    reset_synthetic_state,
)

__all__ = [
    "SyntheticDataSource",
    "SyntheticConfig",
    "SessionAwareSyntheticSource",
    "SharedReferencePrice",
    "create_synthetic_sources",
    "reset_synthetic_state",
    "RESTDataSource",
    "RESTSourceConfig",
    "PlaybackDataSource",
    "PlaybackConfig",
]

if MT5DataSource is not None:
    __all__.insert(0, "MT5DataSource")
