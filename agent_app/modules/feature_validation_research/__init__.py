"""Strict Feature Validation & Research Module implementation."""

from .repository import (
    FeatureRecord,
    FeatureValidationResearchRepository,
    InMemoryFeatureValidationResearchRepository,
    MarketStateRecord,
    MetricWeightRuleDraft,
    OrderExecutionRecord,
    PostgresFeatureValidationResearchRepository,
    ResearchReportRecord,
    WeightsProfileDraft,
)
from .service import (
    FeatureQualityRecord,
    FeatureValidationResearchError,
    FeatureValidationResearchExecutionResult,
    FeatureValidationResearchInput,
    FeatureValidationResearchService,
    ValidationReport,
    ValidationSample,
    ValidationTimeRange,
)

__all__ = [
    "FeatureQualityRecord",
    "FeatureRecord",
    "FeatureValidationResearchError",
    "FeatureValidationResearchExecutionResult",
    "FeatureValidationResearchInput",
    "FeatureValidationResearchRepository",
    "FeatureValidationResearchService",
    "InMemoryFeatureValidationResearchRepository",
    "MarketStateRecord",
    "MetricWeightRuleDraft",
    "OrderExecutionRecord",
    "PostgresFeatureValidationResearchRepository",
    "ResearchReportRecord",
    "ValidationReport",
    "ValidationSample",
    "ValidationTimeRange",
    "WeightsProfileDraft",
]
