"""Strict Normalization & Feature Vector Module implementation."""

from .repository import (
    DataQualityRecord,
    FeatureRecord,
    FeatureVector,
    InMemoryNormalizationFeatureVectorRepository,
    InstrumentProfile,
    NormalizationFeatureVectorRepository,
    PostgresNormalizationFeatureVectorRepository,
)
from .service import (
    NormalizationFeatureVectorError,
    NormalizationFeatureVectorExecutionResult,
    NormalizationFeatureVectorInput,
    NormalizationFeatureVectorService,
    NormalizationProfile,
    NormalizedCandidate,
    VectorDiagnostics,
    check_ttl_status,
)

__all__ = [
    "DataQualityRecord",
    "FeatureRecord",
    "FeatureVector",
    "InMemoryNormalizationFeatureVectorRepository",
    "InstrumentProfile",
    "NormalizationFeatureVectorError",
    "NormalizationFeatureVectorExecutionResult",
    "NormalizationFeatureVectorInput",
    "NormalizationFeatureVectorRepository",
    "NormalizationFeatureVectorService",
    "NormalizationProfile",
    "NormalizedCandidate",
    "PostgresNormalizationFeatureVectorRepository",
    "VectorDiagnostics",
    "check_ttl_status",
]
