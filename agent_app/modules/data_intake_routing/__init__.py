"""Strict Data Intake & Routing Module implementation."""

from .repository import (
    InMemoryDataIntakeRoutingRepository,
    PostgresDataIntakeRoutingRepository,
)
from .service import (
    DataIntakeExecutionResult,
    DataIntakeRequest,
    DataIntakeRoutingInput,
    DataIntakeRoutingService,
    ExternalTextSearchRequestRecord,
    RawTextItem,
    RoutingMessage,
    ScheduledDiscoveryItemRecord,
    ScheduledDiscoveryRunRecord,
    SourceCredibilityRecord,
    TextDedupRecord,
    TextSourceConfig,
)

__all__ = [
    "DataIntakeExecutionResult",
    "DataIntakeRequest",
    "DataIntakeRoutingInput",
    "DataIntakeRoutingService",
    "ExternalTextSearchRequestRecord",
    "InMemoryDataIntakeRoutingRepository",
    "PostgresDataIntakeRoutingRepository",
    "RawTextItem",
    "RoutingMessage",
    "ScheduledDiscoveryItemRecord",
    "ScheduledDiscoveryRunRecord",
    "SourceCredibilityRecord",
    "TextDedupRecord",
    "TextSourceConfig",
]
