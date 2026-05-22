"""Strict Market Data Metrics Module implementation."""

from .repository import (
    FeatureRecord,
    InMemoryMarketDataMetricsRepository,
    InstrumentProfile,
    PostgresMarketDataMetricsRepository,
    RawCandle,
    RawIndexValue,
    RawTrade,
)
from .service import (
    MarketDataMetricsExecutionResult,
    MarketDataMetricsInput,
    MarketDataMetricsService,
)

__all__ = [
    "FeatureRecord",
    "InMemoryMarketDataMetricsRepository",
    "InstrumentProfile",
    "MarketDataMetricsExecutionResult",
    "MarketDataMetricsInput",
    "MarketDataMetricsService",
    "PostgresMarketDataMetricsRepository",
    "RawCandle",
    "RawIndexValue",
    "RawTrade",
]
