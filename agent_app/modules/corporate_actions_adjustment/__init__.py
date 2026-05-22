"""Strict Corporate Actions Adjustment Module implementation."""

from .repository import (
    CorporateActionRecord,
    CorporateActionsAdjustmentRepository,
    FeatureRecord,
    InMemoryCorporateActionsAdjustmentRepository,
    InstrumentMappingUpdate,
    InstrumentProfile,
    InstrumentStatusUpdate,
    PostgresCorporateActionsAdjustmentRepository,
    RawCandle,
    StructuredEvent,
)
from .service import (
    CorporateActionsAdjustmentError,
    CorporateActionsAdjustmentInput,
    CorporateActionsAdjustmentService,
    CorporateActionsExecutionResult,
    CorporateActionsInput,
    MetricValue,
    RecomputeTrigger,
)

__all__ = [
    "CorporateActionRecord",
    "CorporateActionsAdjustmentError",
    "CorporateActionsAdjustmentInput",
    "CorporateActionsAdjustmentRepository",
    "CorporateActionsAdjustmentService",
    "CorporateActionsExecutionResult",
    "CorporateActionsInput",
    "FeatureRecord",
    "InMemoryCorporateActionsAdjustmentRepository",
    "InstrumentMappingUpdate",
    "InstrumentProfile",
    "InstrumentStatusUpdate",
    "MetricValue",
    "PostgresCorporateActionsAdjustmentRepository",
    "RawCandle",
    "RecomputeTrigger",
    "StructuredEvent",
]
