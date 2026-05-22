"""Strict Event & News Intelligence Module implementation."""

from .repository import (
    AuditRecord,
    EventNewsIntelligenceRepository,
    EventReaction,
    EventRoutingMessage,
    FeatureRecord,
    InMemoryEventNewsIntelligenceRepository,
    InstrumentProfile,
    PostgresEventNewsIntelligenceRepository,
    RawTextItem,
    StructuredEvent,
)
from .service import (
    EventNewsExecutionResult,
    EventNewsInput,
    EventNewsIntelligenceService,
    LlmEnvelope,
)

__all__ = [
    "AuditRecord",
    "EventNewsExecutionResult",
    "EventNewsInput",
    "EventNewsIntelligenceRepository",
    "EventNewsIntelligenceService",
    "EventReaction",
    "EventRoutingMessage",
    "FeatureRecord",
    "InMemoryEventNewsIntelligenceRepository",
    "InstrumentProfile",
    "LlmEnvelope",
    "PostgresEventNewsIntelligenceRepository",
    "RawTextItem",
    "StructuredEvent",
]
