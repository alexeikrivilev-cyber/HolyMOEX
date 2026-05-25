from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from agent_app.contracts.unified_objects import (
    CachePolicy,
    ExternalRequest,
    ExternalResponse,
    ModuleJob,
    ModuleJobResult,
    RetryPolicy,
)
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)

from .metrics import (
    content_hash,
    detect_language as detect_text_language,
    duplicate_ratio,
    instrument_mapping_confidence,
    normalize_text,
    relevance_score,
    routing_latency_ms,
    scheduled_discovery_coverage_ratio,
    source_credibility_score,
    text_items_fetched,
    text_search_requests_created,
    token_similarity,
)
from .repository import (
    AuditRecord,
    DataIntakeRoutingRepository,
    InMemoryDataIntakeRoutingRepository,
)


MODULE_NAME = "Data Intake & Routing Module"
CALCULATION_VERSION = "data_intake_routing_v1"

VALID_CONTOURS = {"intraday_contour", "event_contour", "daily_contour"}
FAST_NEWS_SOURCE_TYPES = {
    "news_api",
    "rbc_news",
    "tass_news",
    "interfax_news",
    "prime_news",
    "finam_news",
    "smartlab_news",
}
OFFICIAL_DISCLOSURE_SOURCE_TYPES = {
    "issuer_disclosure",
    "prime_disclosure",
    "akm_disclosure",
    "corporate_site",
}
MACRO_SOURCE_TYPES = {
    "macro_text",
    "cbr_macro",
    "moex_macro",
    "fred_eia_macro",
    "rosstat_macro",
}
REGULATORY_SOURCE_TYPES = {"regulatory_text", "cbr_macro", "moex_macro"}
VALID_SOURCE_TYPES = FAST_NEWS_SOURCE_TYPES | OFFICIAL_DISCLOSURE_SOURCE_TYPES | MACRO_SOURCE_TYPES | REGULATORY_SOURCE_TYPES
VALID_TEXT_CATEGORIES = {
    "news",
    "earnings",
    "dividend",
    "corporate_action",
    "macro",
    "regulation",
    "other",
}
VALID_DISCOVERY_MODES = {"scheduled", "event_driven", "replay"}
DOCUMENTED_ROUTING_TARGETS = (
    "Event & News Intelligence Module",
    "Earnings & Dividend Intelligence Module",
    "Corporate Actions Adjustment Module",
    "Market Context Module",
)
CATEGORY_TARGETS = {
    "news": ("Event & News Intelligence Module",),
    "earnings": ("Event & News Intelligence Module", "Earnings & Dividend Intelligence Module"),
    "dividend": ("Event & News Intelligence Module", "Earnings & Dividend Intelligence Module"),
    "corporate_action": ("Event & News Intelligence Module", "Corporate Actions Adjustment Module"),
    "macro": ("Event & News Intelligence Module", "Market Context Module"),
    "regulation": ("Event & News Intelligence Module", "Market Context Module"),
    "other": ("Event & News Intelligence Module",),
}
SOURCE_PROVIDER_MAP = {
    **{source_type: "news_api" for source_type in FAST_NEWS_SOURCE_TYPES},
    **{source_type: "issuer_disclosure" for source_type in OFFICIAL_DISCLOSURE_SOURCE_TYPES},
    **{source_type: "macro_api" for source_type in MACRO_SOURCE_TYPES | REGULATORY_SOURCE_TYPES},
}
SOURCE_CREDIBILITY_REGISTRY = {
    "issuer_disclosure": 0.95,
    "prime_disclosure": 0.95,
    "akm_disclosure": 0.92,
    "corporate_site": 0.80,
    "macro_text": 0.85,
    "cbr_macro": 0.95,
    "moex_macro": 0.92,
    "fred_eia_macro": 0.75,
    "rosstat_macro": 0.75,
    "regulatory_text": 0.85,
    "news_api": 0.65,
    "rbc_news": 0.75,
    "tass_news": 0.75,
    "interfax_news": 0.75,
    "prime_news": 0.75,
    "finam_news": 0.70,
    "smartlab_news": 0.55,
    "default": 0.50,
}
TRUST_LEVEL_BY_SOURCE_TYPE = {
    "issuer_disclosure": "high",
    "prime_disclosure": "high",
    "akm_disclosure": "high",
    "corporate_site": "high",
    "regulatory_text": "high",
    "cbr_macro": "high",
    "moex_macro": "high",
    "macro_text": "high",
    "rbc_news": "normal_high",
    "tass_news": "normal_high",
    "interfax_news": "normal_high",
    "prime_news": "normal_high",
    "finam_news": "normal",
    "news_api": "normal",
    "smartlab_news": "medium_weak",
    "fred_eia_macro": "normal",
    "rosstat_macro": "normal",
}
OFFICIAL_CONFIRMATION_SOURCE_TYPES = OFFICIAL_DISCLOSURE_SOURCE_TYPES | REGULATORY_SOURCE_TYPES | {"cbr_macro", "moex_macro", "macro_text"}
MARKET_WIDE_CATEGORIES = {"macro", "regulation"}


class DataIntakeRoutingError(ValueError):
    """Raised when intake execution would violate module documentation."""


@dataclass(frozen=True)
class DataIntakeRequest:
    universe_id: str
    instrument_ids: tuple[str, ...]
    source_types: tuple[str, ...]
    discovery_mode: str
    per_instrument_discovery: bool
    time_range: Mapping[str, Any]
    routing_targets: tuple[str, ...]
    text_source_config: Mapping[str, Any] = field(default_factory=dict)
    raw_text_items: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    external_responses: tuple[ExternalResponse | Mapping[str, Any], ...] = field(default_factory=tuple)
    routing_ttl_seconds: int = 3600

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "DataIntakeRequest":
        request_payload = payload.get("intake_request")
        if not isinstance(request_payload, Mapping):
            raise DataIntakeRoutingError("payload must contain intake_request")
        base = dict(request_payload)

        missing_fields = [
            field_name
            for field_name in (
                "universe_id",
                "instrument_ids",
                "source_types",
                "discovery_mode",
                "per_instrument_discovery",
                "time_range",
                "routing_targets",
            )
            if field_name not in base
        ]
        if missing_fields:
            raise DataIntakeRoutingError(f"intake_request missing required fields: {missing_fields}")

        universe_id = str(base.get("universe_id") or "").strip()
        instrument_ids = tuple(str(item) for item in (base.get("instrument_ids") or ()))
        source_types = tuple(str(item) for item in (base.get("source_types") or ()))
        discovery_mode = str(base.get("discovery_mode") or "").strip()
        if discovery_mode not in VALID_DISCOVERY_MODES:
            raise DataIntakeRoutingError(f"invalid discovery_mode: {discovery_mode}")
        per_instrument_discovery = _strict_bool(base.get("per_instrument_discovery"))
        if not per_instrument_discovery:
            raise DataIntakeRoutingError("intake_request.per_instrument_discovery must be true")
        if not source_types:
            raise DataIntakeRoutingError("intake_request.source_types must be non-empty")
        invalid_sources = sorted(set(source_types) - VALID_SOURCE_TYPES)
        if invalid_sources:
            raise DataIntakeRoutingError(f"invalid source_types: {invalid_sources}")
        if not instrument_ids and discovery_mode != "scheduled" and not _is_market_wide_source_set(source_types):
            raise DataIntakeRoutingError("intake_request.instrument_ids is required for non-market-wide sources")

        routing_targets = tuple(str(item) for item in (base.get("routing_targets") or ()))
        if not routing_targets:
            raise DataIntakeRoutingError("intake_request.routing_targets must be non-empty")
        invalid_targets = sorted(set(routing_targets) - set(DOCUMENTED_ROUTING_TARGETS))
        if invalid_targets:
            raise DataIntakeRoutingError(f"invalid routing_targets: {invalid_targets}")

        time_range = base.get("time_range")
        if not isinstance(time_range, Mapping):
            raise DataIntakeRoutingError("intake_request.time_range must be an object")
        if not time_range.get("from_ts") or not time_range.get("to_ts"):
            raise DataIntakeRoutingError("intake_request.time_range requires from_ts and to_ts")
        parse_utc_iso(str(time_range["from_ts"]))
        parse_utc_iso(str(time_range["to_ts"]))
        if not universe_id:
            raise DataIntakeRoutingError("intake_request.universe_id is required")
        if universe_id != job.universe_id:
            raise DataIntakeRoutingError("intake_request.universe_id must match module_job.universe_id")
        if int(base.get("routing_ttl_seconds") or 3600) <= 0:
            raise DataIntakeRoutingError("routing_ttl_seconds must be positive")

        raw_items = _as_mapping_tuple(
            payload.get("raw_text_items")
            or base.get("raw_text_items")
            or ([payload["raw_text_item"]] if isinstance(payload.get("raw_text_item"), Mapping) else ())
        )
        external_responses = tuple(
            payload.get("external_responses")
            or base.get("external_responses")
            or ([payload["external_response"]] if payload.get("external_response") is not None else ())
        )
        return cls(
            universe_id=universe_id,
            instrument_ids=instrument_ids,
            source_types=source_types,
            discovery_mode=discovery_mode,
            per_instrument_discovery=per_instrument_discovery,
            time_range=dict(time_range),
            routing_targets=tuple(dict.fromkeys(routing_targets)),
            text_source_config=dict(base.get("text_source_config") or payload.get("text_source_config") or {}),
            raw_text_items=raw_items,
            external_responses=external_responses,
            routing_ttl_seconds=int(base.get("routing_ttl_seconds") or 3600),
        )


DataIntakeRoutingInput = DataIntakeRequest


@dataclass(frozen=True)
class RawTextItem:
    raw_text_item_id: str
    universe_id: str
    instrument_ids: tuple[str, ...]
    source_type: str
    source_ref: str
    source_url: str | None
    title: str
    body: str
    language: str
    published_at: str | None
    fetched_at: str | None
    content_hash: str
    source: str | None = None
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    trust_level: str | None = None
    confidence_score: float = 0.0
    instrument_candidates: tuple[Mapping[str, Any], ...] = ()
    issuer_candidates: tuple[Mapping[str, Any], ...] = ()
    quality_flags: tuple[str, ...] = ()
    discovery_run_id: str | None = None
    discovery_item_id: str | None = None
    discovery_mode: str | None = None
    external_request_id: str | None = None
    text_category: str = "other"
    instrument_mapping_confidence: float = 0.0
    source_credibility_score: float = 0.0
    relevance_score: float = 0.0
    is_duplicate: bool = False
    duplicate_of: str | None = None

    def __post_init__(self) -> None:
        required = {
            "raw_text_item_id": self.raw_text_item_id,
            "universe_id": self.universe_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "language": self.language,
            "content_hash": self.content_hash,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise DataIntakeRoutingError(f"raw_text_item missing required fields: {missing}")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise DataIntakeRoutingError(f"invalid raw_text_item.source_type: {self.source_type}")
        if self.discovery_mode is not None and self.discovery_mode not in VALID_DISCOVERY_MODES:
            raise DataIntakeRoutingError(f"invalid raw_text_item.discovery_mode: {self.discovery_mode}")
        if self.text_category not in VALID_TEXT_CATEGORIES:
            raise DataIntakeRoutingError(f"invalid text_category: {self.text_category}")

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        universe_id: str,
        default_source_type: str = "",
        default_source_ref: str | None = None,
        default_instrument_ids: tuple[str, ...] = (),
    ) -> "RawTextItem":
        if "raw_text_item" in payload and isinstance(payload["raw_text_item"], Mapping):
            payload = payload["raw_text_item"]  # type: ignore[assignment]
        title = str(payload.get("title") or payload.get("headline") or "").strip()
        body = str(payload.get("body") or payload.get("text") or payload.get("content") or "").strip()
        source_type = str(payload.get("source_type") or default_source_type or payload.get("source") or "").strip()
        source = _optional_text(payload.get("source_name") or payload.get("source_ref_name") or payload.get("source"))
        source_url = _optional_text(payload.get("source_url") or payload.get("url"))
        item_hash = str(payload.get("content_hash") or content_hash(title, body or source_url or ""))
        source_ref = str(
            payload.get("source_ref")
            or payload.get("data_ref")
            or default_source_ref
            or source_url
            or f"{source_type}:{item_hash[:16]}"
        )
        language = str(payload.get("language") or detect_text_language(title, body))
        fetched_at = _optional_text(payload.get("fetched_at")) or to_utc_iso(utc_now())
        instrument_ids = tuple(
            str(item)
            for item in (payload.get("instrument_ids") or default_instrument_ids or ())
        )
        raw_text_item_id = str(payload.get("raw_text_item_id") or f"raw_text_{item_hash[:24]}")
        return cls(
            raw_text_item_id=raw_text_item_id,
            universe_id=str(payload.get("universe_id") or universe_id),
            instrument_ids=instrument_ids,
            source_type=source_type,
            source=source or source_type,
            source_ref=source_ref,
            source_url=source_url,
            title=title,
            body=body,
            language=language,
            published_at=_optional_text(payload.get("published_at")),
            fetched_at=fetched_at,
            content_hash=item_hash,
            source_payload={**dict(payload), "source_ref": source_ref, "source": source or source_type},
            trust_level=_optional_text(payload.get("trust_level")) or TRUST_LEVEL_BY_SOURCE_TYPE.get(source_type),
            confidence_score=_float_or_zero(payload.get("confidence_score")),
            instrument_candidates=_mapping_tuple(payload.get("instrument_candidates")),
            issuer_candidates=_mapping_tuple(payload.get("issuer_candidates")),
            quality_flags=_string_tuple(payload.get("quality_flags")),
            discovery_run_id=_optional_text(payload.get("discovery_run_id")),
            discovery_item_id=_optional_text(payload.get("discovery_item_id") or _ref_id(payload.get("discovery_item_ref"))),
            discovery_mode=_optional_text(payload.get("discovery_mode")),
            external_request_id=_optional_text(payload.get("external_request_id")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text_item_id": self.raw_text_item_id,
            "universe_id": self.universe_id,
            "instrument_ids": list(self.instrument_ids),
            "source_type": self.source_type,
            "source": self.source or self.source_type,
            "source_ref": self.source_ref,
            "source_url": self.source_url,
            "title": self.title,
            "body": self.body,
            "language": self.language,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "content_hash": self.content_hash,
            "trust_level": self.trust_level,
            "confidence_score": self.confidence_score,
            "instrument_candidates": [dict(item) for item in self.instrument_candidates],
            "issuer_candidates": [dict(item) for item in self.issuer_candidates],
            "quality_flags": list(self.quality_flags),
            "discovery_run_id": self.discovery_run_id,
            "discovery_item_id": self.discovery_item_id,
            "discovery_mode": self.discovery_mode,
            "external_request_id": self.external_request_id,
            "text_category": self.text_category,
            "instrument_mapping_confidence": self.instrument_mapping_confidence,
            "source_credibility_score": self.source_credibility_score,
            "relevance_score": self.relevance_score,
            "is_duplicate": self.is_duplicate,
            "duplicate_of": self.duplicate_of,
            "source_payload": dict(self.source_payload),
        }


@dataclass(frozen=True)
class TextSourceConfig:
    text_source_config_id: str
    source_type: str
    provider: str
    request_type: str = "text_search"
    enabled: bool = True
    discovery_frequency: str | None = None
    max_age_seconds: int | None = None
    query_template: Mapping[str, Any] = field(default_factory=dict)
    source_policy: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_type not in VALID_SOURCE_TYPES:
            raise DataIntakeRoutingError(f"invalid text_source_config.source_type: {self.source_type}")
        if self.request_type not in {"text_search", "text_fetch"}:
            raise DataIntakeRoutingError(f"invalid text_source_config.request_type: {self.request_type}")
        if not self.provider:
            raise DataIntakeRoutingError("text_source_config.provider is required")

    @classmethod
    def from_any(cls, payload: Any) -> "TextSourceConfig":
        data = _to_mapping(payload)
        return cls(
            text_source_config_id=str(data.get("text_source_config_id") or data.get("config_id") or ""),
            source_type=str(data.get("source_type") or ""),
            provider=str(data.get("provider") or ""),
            request_type=str(data.get("request_type") or "text_search"),
            enabled=_strict_bool(data.get("enabled", True)),
            discovery_frequency=_optional_text(data.get("discovery_frequency")),
            max_age_seconds=int(data["max_age_seconds"]) if data.get("max_age_seconds") is not None else None,
            query_template=data.get("query_template") or {},
            source_policy=data.get("source_policy") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text_source_config_id": self.text_source_config_id,
            "source_type": self.source_type,
            "provider": self.provider,
            "request_type": self.request_type,
            "enabled": self.enabled,
            "discovery_frequency": self.discovery_frequency,
            "max_age_seconds": self.max_age_seconds,
            "query_template": dict(self.query_template),
            "source_policy": dict(self.source_policy),
        }


@dataclass(frozen=True)
class ScheduledDiscoveryRunRecord:
    discovery_run_id: str
    module_job_id: str
    universe_id: str
    discovery_mode: str
    per_instrument_discovery: bool
    source_types: tuple[str, ...]
    time_range: Mapping[str, Any]
    active_instruments_total: int
    active_instruments_with_discovery_request: int
    scheduled_discovery_coverage_ratio: float
    status: str
    started_at: str | None
    finished_at: str | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_run_id": self.discovery_run_id,
            "module_job_id": self.module_job_id,
            "universe_id": self.universe_id,
            "discovery_mode": self.discovery_mode,
            "per_instrument_discovery": self.per_instrument_discovery,
            "source_types": list(self.source_types),
            "time_range": dict(self.time_range),
            "active_instruments_total": self.active_instruments_total,
            "active_instruments_with_discovery_request": self.active_instruments_with_discovery_request,
            "scheduled_discovery_coverage_ratio": self.scheduled_discovery_coverage_ratio,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "source_module": MODULE_NAME,
            "calculation_version": CALCULATION_VERSION,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ScheduledDiscoveryItemRecord:
    discovery_run_id: str
    instrument_id: str
    source_type: str
    text_source_config_id: str | None
    query_terms: tuple[str, ...]
    query_payload: Mapping[str, Any]
    status: str
    skip_reason_code: str | None = None
    external_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_run_id": self.discovery_run_id,
            "instrument_id": self.instrument_id,
            "source_type": self.source_type,
            "text_source_config_id": self.text_source_config_id,
            "query_terms": list(self.query_terms),
            "query_payload": dict(self.query_payload),
            "status": self.status,
            "skip_reason_code": self.skip_reason_code,
            "external_request_id": self.external_request_id,
        }


@dataclass(frozen=True)
class ExternalTextSearchRequestRecord:
    discovery_item_ref: str
    request_id: str
    instrument_id: str
    source_type: str
    provider: str
    request_type: str
    query_terms: tuple[str, ...]
    query_payload: Mapping[str, Any]
    external_text_search_request_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_item_ref": self.discovery_item_ref,
            "request_id": self.request_id,
            "instrument_id": self.instrument_id,
            "source_type": self.source_type,
            "provider": self.provider,
            "request_type": self.request_type,
            "query_terms": list(self.query_terms),
            "query_payload": dict(self.query_payload),
            "external_text_search_request_ref": self.external_text_search_request_ref,
        }


@dataclass(frozen=True)
class TextDedupRecord:
    content_hash: str
    raw_text_ref: str
    is_duplicate: bool
    duplicate_of: str | None
    similarity_score: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "raw_text_ref": self.raw_text_ref,
            "is_duplicate": self.is_duplicate,
            "duplicate_of": self.duplicate_of,
            "similarity_score": self.similarity_score,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SourceCredibilityRecord:
    source_ref: str
    source_type: str
    source_credibility_score: float
    calculation_version: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "source_type": self.source_type,
            "source_credibility_score": self.source_credibility_score,
            "calculation_version": self.calculation_version,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class RoutingMessage:
    routing_message_id: str
    raw_text_ref: str
    universe_id: str
    discovery_mode: str
    instrument_ids: tuple[str, ...]
    text_category: str
    source_type: str
    source_credibility_score: float
    relevance_score: float
    target_modules: tuple[str, ...]
    created_at: str
    routing_ttl_seconds: int = 3600
    status: str = "pending"
    routing_reason: str = "category_rule"
    discovery_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.discovery_mode not in VALID_DISCOVERY_MODES:
            raise DataIntakeRoutingError(f"invalid routing_message.discovery_mode: {self.discovery_mode}")
        if self.text_category not in VALID_TEXT_CATEGORIES:
            raise DataIntakeRoutingError(f"invalid routing_message.text_category: {self.text_category}")
        if not self.target_modules:
            raise DataIntakeRoutingError("routing_message.target_modules must be explicit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing_message_id": self.routing_message_id,
            "raw_text_ref": self.raw_text_ref,
            "universe_id": self.universe_id,
            "discovery_mode": self.discovery_mode,
            "instrument_ids": list(self.instrument_ids),
            "text_category": self.text_category,
            "source_type": self.source_type,
            "source_credibility_score": self.source_credibility_score,
            "relevance_score": self.relevance_score,
            "target_modules": list(self.target_modules),
            "created_at": self.created_at,
            "routing_ttl_seconds": self.routing_ttl_seconds,
            "status": self.status,
            "routing_reason": self.routing_reason,
            "discovery_run_id": self.discovery_run_id,
        }


@dataclass(frozen=True)
class DataIntakeExecutionResult:
    module_job_result: ModuleJobResult
    raw_text_items: tuple[RawTextItem, ...]
    routing_messages: tuple[RoutingMessage, ...]
    text_dedup_records: tuple[TextDedupRecord, ...]
    source_credibility_records: tuple[SourceCredibilityRecord, ...]
    external_requests: tuple[ExternalRequest, ...]
    audit_ref: str
    metrics: Mapping[str, float | int] = field(default_factory=dict)
    external_text_search_requests: tuple[ExternalTextSearchRequestRecord, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "raw_text_items": [item.to_dict() for item in self.raw_text_items],
            "routing_messages": [message.to_dict() for message in self.routing_messages],
            "text_dedup_records": [record.to_dict() for record in self.text_dedup_records],
            "source_credibility_records": [record.to_dict() for record in self.source_credibility_records],
            "external_requests": [request.to_dict() for request in self.external_requests],
            "external_text_search_requests": [
                record.to_dict() for record in self.external_text_search_requests
            ],
            "audit_ref": self.audit_ref,
            "metrics": dict(self.metrics),
        }


class DataIntakeRoutingService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: DataIntakeRoutingRepository | None = None,
        gateway: Any | None = None,
    ) -> None:
        self.repository = repository or InMemoryDataIntakeRoutingRepository()
        self.gateway = gateway

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> DataIntakeExecutionResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> DataIntakeExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> DataIntakeExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    severity="error",
                    event_type="module_job_missing",
                    message="Data Intake & Routing Module requires module_job",
                    object_type="module_job",
                    reason_codes=("module_job_required",),
                    payload={"source_module": self.module_name},
                )
            )
            raise DataIntakeRoutingError("Data Intake & Routing Module requires module_job")

        try:
            self.validate_module_job(job)
            intake_request = self._coerce_input(payload, job)
            active_profiles = self.load_active_instruments(intake_request, job)
            active_ids = tuple(str(_profile_value(profile, "instrument_id")) for profile in active_profiles)
            dropped_instruments = tuple(
                instrument_id
                for instrument_id in intake_request.instrument_ids
                if instrument_id not in active_ids
            )
            warnings: list[str] = []
            if intake_request.discovery_mode != "scheduled":
                warnings.extend(f"instrument_not_in_active_universe:{instrument_id}" for instrument_id in dropped_instruments)

            if not active_profiles and not self._allows_market_wide_only(intake_request):
                warnings.append("no_active_instruments_for_intake_request")
                return self._empty_result(
                    job=job,
                    started_at=started_at,
                    status="skipped",
                    warnings=tuple(warnings),
                    errors=(),
                    audit_event_type="intake_skipped_no_active_instruments",
                )

            text_source_configs = self.load_text_source_configs(intake_request)
            external_requests, gateway_responses, gateway_warnings, external_text_search_records = self.fetch_text_via_gateway(
                intake_request,
                job,
                active_profiles,
                text_source_configs,
                started_at,
            )
            warnings.extend(gateway_warnings)

            external_responses = list(gateway_responses)
            external_responses.extend(self._coerce_external_response(item) for item in intake_request.external_responses)
            external_responses.extend(self._load_external_responses_from_refs(job.input_refs))

            raw_payloads = list(intake_request.raw_text_items)
            for response in external_responses:
                extracted, response_warnings = self._raw_payloads_from_external_response(response, intake_request)
                raw_payloads.extend(extracted)
                warnings.extend(response_warnings)
            raw_payloads.extend(self._raw_payloads_from_refs(job.input_refs))

            raw_text_items: list[RawTextItem] = []
            routing_messages: list[RoutingMessage] = []
            dedup_records: list[TextDedupRecord] = []
            source_records: list[SourceCredibilityRecord] = []
            output_refs: list[str] = [
                str(record.external_text_search_request_ref)
                for record in external_text_search_records
                if record.external_text_search_request_ref
            ]
            duplicate_count = 0
            new_raw_count = 0
            mapping_scores: list[float] = []
            relevance_scores: list[float] = []
            routing_latencies: list[int] = []

            for raw_payload in raw_payloads:
                raw_payload, source_type_warnings = self._raw_payload_with_valid_source(raw_payload, intake_request)
                warnings.extend(source_type_warnings)
                if raw_payload is None:
                    continue
                raw_item = RawTextItem.from_dict(
                    raw_payload,
                    universe_id=intake_request.universe_id,
                    default_source_type=str(raw_payload.get("source_type") or raw_payload.get("source") or ""),
                    default_source_ref=str(raw_payload.get("source_ref") or raw_payload.get("data_ref") or ""),
                    default_instrument_ids=tuple(raw_payload.get("instrument_ids") or ()),
                )
                raw_item = self._classify_and_map(raw_item, active_profiles)
                is_duplicate, duplicate_of, similarity = self.deduplicate_text(raw_item)
                raw_item = replace(raw_item, is_duplicate=is_duplicate, duplicate_of=duplicate_of)
                raw_ref = self.write_raw_text_item(raw_item)
                output_refs.append(raw_ref)
                if not is_duplicate:
                    new_raw_count += 1
                else:
                    duplicate_count += 1

                dedup_record = TextDedupRecord(
                    content_hash=raw_item.content_hash,
                    raw_text_ref=raw_ref,
                    is_duplicate=is_duplicate,
                    duplicate_of=duplicate_of,
                    similarity_score=similarity,
                    created_at=to_utc_iso(utc_now()),
                )
                self.repository.save_text_dedup_record(dedup_record)
                dedup_records.append(dedup_record)

                source_record = SourceCredibilityRecord(
                    source_ref=raw_item.source_ref,
                    source_type=raw_item.source_type,
                    source_credibility_score=raw_item.source_credibility_score,
                    calculation_version=CALCULATION_VERSION,
                    created_at=to_utc_iso(utc_now()),
                )
                self.repository.save_source_credibility_record(source_record)
                source_records.append(source_record)
                raw_text_items.append(raw_item)
                mapping_scores.append(raw_item.instrument_mapping_confidence)
                relevance_scores.append(raw_item.relevance_score)

                if is_duplicate:
                    warnings.append(f"duplicate_text_skipped:{raw_ref}")
                    continue
                if not raw_item.instrument_ids and raw_item.text_category not in MARKET_WIDE_CATEGORIES:
                    warnings.append(f"raw_text_unmapped_not_routed:{raw_ref}")
                    continue

                routing_message = self.create_routing_message(
                    raw_item=raw_item,
                    raw_text_ref=raw_ref,
                    intake_request=intake_request,
                )
                if routing_message is None:
                    warnings.append(f"routing_target_not_available:{raw_ref}")
                    continue
                routing_ref = self.repository.save_routing_message(routing_message)
                output_refs.append(routing_ref)
                routing_messages.append(routing_message)
                routing_latencies.append(
                    routing_latency_ms(
                        _to_epoch_ms(raw_item.fetched_at),
                        _to_epoch_ms(routing_message.created_at),
                    )
                )

            metrics = {
                "text_items_fetched": text_items_fetched(new_raw_count),
                "scheduled_discovery_coverage_ratio": self._scheduled_discovery_coverage_ratio(
                    intake_request,
                    active_profiles,
                    external_requests,
                ),
                "duplicate_ratio": duplicate_ratio(duplicate_count, len(raw_payloads)),
                "text_search_requests_created": text_search_requests_created(
                    request.request_type for request in external_requests
                ),
                "instrument_mapping_confidence": max(mapping_scores) if mapping_scores else 0.0,
                "source_credibility_score": _average(
                    [record.source_credibility_score for record in source_records]
                ),
                "relevance_score": _average(relevance_scores),
                "routing_latency_ms": _average(routing_latencies),
            }
            status = self._status(raw_text_items, routing_messages, warnings)
            if status == "skipped" and external_text_search_records:
                status = "success" if not warnings else "partial_success"
            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="info" if status == "success" else "warning",
                    event_type="intake_routing_completed",
                    message="Data intake and routing completed",
                    object_type="routing_message",
                    object_ref=",".join(output_refs),
                    reason_codes=self._audit_reason_codes(intake_request, warnings),
                    payload={
                        "metrics": metrics,
                        "warnings": warnings,
                        "output_refs": output_refs,
                        "external_text_search_requests": [
                            record.to_dict() for record in external_text_search_records
                        ],
                    },
                )
            )
            return DataIntakeExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=tuple(output_refs),
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics=metrics,
                    events_written=0,
                ),
                raw_text_items=tuple(raw_text_items),
                routing_messages=tuple(routing_messages),
                text_dedup_records=tuple(dedup_records),
                source_credibility_records=tuple(source_records),
                external_requests=tuple(external_requests),
                audit_ref=audit_ref,
                metrics=metrics,
                external_text_search_requests=tuple(external_text_search_records),
            )
        except (DataIntakeRoutingError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise DataIntakeRoutingError("Data Intake & Routing Module requires module_job")
        if job.module_name != self.module_name:
            raise DataIntakeRoutingError("module_job.module_name must be Data Intake & Routing Module")
        if job.contour not in VALID_CONTOURS:
            raise DataIntakeRoutingError("module_job.contour must be intraday_contour, event_contour or daily_contour")
        if not job.universe_id:
            raise DataIntakeRoutingError("module_job.universe_id is required")
        if not job.run_mode:
            raise DataIntakeRoutingError("module_job.run_mode is required")
        if not job.idempotency_key:
            raise DataIntakeRoutingError("module_job.idempotency_key is required")

    def load_active_instruments(self, intake_request: DataIntakeRequest, job: ModuleJob) -> tuple[Any, ...]:
        if intake_request.discovery_mode == "scheduled":
            return self.repository.list_active_instrument_profiles(intake_request.universe_id, ())
        requested_ids = intake_request.instrument_ids or job.instrument_ids
        return self.repository.list_active_instrument_profiles(intake_request.universe_id, tuple(requested_ids))

    def load_text_source_configs(self, intake_request: DataIntakeRequest) -> Mapping[str, TextSourceConfig]:
        configs: dict[str, TextSourceConfig] = {}
        for config_payload in self.repository.list_text_source_configs(intake_request.source_types):
            config = self._coerce_text_source_config(config_payload)
            if config.source_type in intake_request.source_types:
                configs[config.source_type] = config
        return configs

    def build_alias_queries(self, active_profiles: tuple[Any, ...]) -> tuple[Mapping[str, Any], ...]:
        queries = []
        for profile in active_profiles:
            aliases = tuple(str(alias) for alias in (_profile_value(profile, "aliases", ()) or ()))
            related_entities = tuple(str(entity) for entity in (_profile_value(profile, "related_entities", ()) or ()))
            issuer_name = str(_profile_value(profile, "issuer_name", "") or "")
            ticker = str(_profile_value(profile, "ticker", "") or "")
            sector = str(_profile_value(profile, "sector", "") or "")
            metadata = _profile_value(profile, "metadata", {}) or {}
            query_terms = tuple(
                dict.fromkeys(
                    term
                    for term in (ticker, issuer_name, *aliases, *related_entities)
                    if str(term or "").strip()
                )
            )
            queries.append(
                {
                    "instrument_id": str(_profile_value(profile, "instrument_id")),
                    "ticker": ticker,
                    "issuer_name": issuer_name,
                    "sector": sector,
                    "aliases": list(dict.fromkeys(alias for alias in aliases if alias)),
                    "related_entities": list(dict.fromkeys(entity for entity in related_entities if entity)),
                    "query_terms": list(query_terms),
                    "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
                }
            )
        return tuple(queries)

    def schedule_per_instrument_news_discovery(
        self,
        intake_request: DataIntakeRequest,
        active_profiles: tuple[Any, ...],
        text_source_configs: Mapping[str, TextSourceConfig],
    ) -> tuple[list[Mapping[str, Any]], list[str]]:
        warnings: list[str] = []
        schedule: list[Mapping[str, Any]] = []
        queries_by_instrument = {
            str(query["instrument_id"]): query for query in self.build_alias_queries(active_profiles)
        }
        if not active_profiles and self._allows_market_wide_only(intake_request):
            for source_type in intake_request.source_types:
                config = text_source_configs.get(source_type)
                query = {
                    "instrument_id": "",
                    "ticker": "",
                    "issuer_name": "",
                    "sector": "",
                    "aliases": [],
                    "related_entities": [],
                    "query_terms": [],
                    "market_wide": True,
                }
                if not self._source_enabled(source_type, intake_request.text_source_config, config):
                    warnings.append(f"source_disabled:{source_type}:market_wide")
                    schedule.append(
                        {
                            "instrument_id": "",
                            "source_type": source_type,
                            "config": config,
                            "query": self._query_payload_for_source(query, config),
                            "query_terms": (),
                            "status": "skipped",
                            "skip_reason_code": "source_disabled",
                        }
                    )
                    continue
                if not self._market_wide_source_allowed(config):
                    warnings.append(f"instrument_not_eligible:market_wide:{source_type}")
                    schedule.append(
                        {
                            "instrument_id": "",
                            "source_type": source_type,
                            "config": config,
                            "query": self._query_payload_for_source(query, config),
                            "query_terms": (),
                            "status": "skipped",
                            "skip_reason_code": "instrument_not_eligible",
                        }
                    )
                    continue
                schedule.append(
                    {
                        "instrument_id": "",
                        "source_type": source_type,
                        "config": config,
                        "query": self._query_payload_for_source(query, config),
                        "query_terms": (),
                        "status": "planned",
                        "skip_reason_code": None,
                    }
                )
            return schedule, warnings

        per_instrument_source_types: list[str] = []
        for source_type in intake_request.source_types:
            config = text_source_configs.get(source_type)
            if self._market_wide_once_source(config):
                query = {
                    "instrument_id": "",
                    "ticker": "",
                    "issuer_name": "",
                    "sector": "",
                    "aliases": [],
                    "related_entities": [],
                    "query_terms": [],
                    "market_wide": True,
                }
                query_payload = self._query_payload_for_source(query, config)
                if not self._source_enabled(source_type, intake_request.text_source_config, config):
                    warnings.append(f"source_disabled:{source_type}:market_wide")
                    schedule.append(
                        {
                            "instrument_id": "",
                            "source_type": source_type,
                            "config": config,
                            "query": query_payload,
                            "query_terms": (),
                            "status": "skipped",
                            "skip_reason_code": "source_disabled",
                        }
                    )
                    continue
                schedule.append(
                    {
                        "instrument_id": "",
                        "source_type": source_type,
                        "config": config,
                        "query": query_payload,
                        "query_terms": (),
                        "status": "planned",
                        "skip_reason_code": None,
                    }
                )
                continue
            per_instrument_source_types.append(source_type)

        for profile in active_profiles:
            instrument_id = str(_profile_value(profile, "instrument_id"))
            for source_type in per_instrument_source_types:
                config = text_source_configs.get(source_type)
                base_query = queries_by_instrument[instrument_id]
                query_payload = self._query_payload_for_source(base_query, config)
                query_terms = tuple(str(term) for term in (base_query.get("query_terms") or ()) if str(term).strip())
                if not self._source_enabled(source_type, intake_request.text_source_config, config):
                    warnings.append(f"source_disabled:{source_type}:{instrument_id}")
                    schedule.append(
                        {
                            "instrument_id": instrument_id,
                            "source_type": source_type,
                            "config": config,
                            "query": query_payload,
                            "query_terms": query_terms,
                            "status": "skipped",
                            "skip_reason_code": "source_disabled",
                        }
                    )
                    continue
                if not self._source_has_required_endpoint(config, query_payload):
                    warnings.append(f"source_missing_endpoint:{source_type}:{instrument_id}")
                    schedule.append(
                        {
                            "instrument_id": instrument_id,
                            "source_type": source_type,
                            "config": config,
                            "query": query_payload,
                            "query_terms": query_terms,
                            "status": "skipped",
                            "skip_reason_code": "source_missing_endpoint",
                        }
                    )
                    continue
                if not self._instrument_eligible(instrument_id, source_type, intake_request.text_source_config):
                    warnings.append(f"instrument_not_eligible:{instrument_id}:{source_type}")
                    schedule.append(
                        {
                            "instrument_id": instrument_id,
                            "source_type": source_type,
                            "config": config,
                            "query": query_payload,
                            "query_terms": query_terms,
                            "status": "skipped",
                            "skip_reason_code": "instrument_not_eligible",
                        }
                    )
                    continue
                schedule.append(
                    {
                        "instrument_id": instrument_id,
                        "source_type": source_type,
                        "config": config,
                        "query": query_payload,
                        "query_terms": query_terms,
                        "status": "planned",
                        "skip_reason_code": None,
                    }
                )
        return schedule, warnings

    def create_text_search_external_requests(
        self,
        intake_request: DataIntakeRequest,
        job: ModuleJob,
        discovery_run_id: str,
        discovery_schedule: list[Mapping[str, Any]],
    ) -> tuple[list[ExternalRequest], list[ScheduledDiscoveryItemRecord], dict[str, Mapping[str, Any]]]:
        requests: list[ExternalRequest] = []
        discovery_items: list[ScheduledDiscoveryItemRecord] = []
        request_contexts: dict[str, Mapping[str, Any]] = {}
        for scheduled_item in discovery_schedule:
            source_type = str(scheduled_item["source_type"])
            instrument_id = str(scheduled_item.get("instrument_id") or "")
            config = scheduled_item.get("config")
            if config is not None and not isinstance(config, TextSourceConfig):
                config = self._coerce_text_source_config(config)
            query_payload = dict(scheduled_item["query"])
            query_terms = tuple(str(term) for term in (scheduled_item.get("query_terms") or ()) if str(term).strip())
            text_source_config_id = config.text_source_config_id if isinstance(config, TextSourceConfig) else None
            if scheduled_item.get("status") == "skipped":
                discovery_items.append(
                    ScheduledDiscoveryItemRecord(
                        discovery_run_id=discovery_run_id,
                        instrument_id=instrument_id,
                        source_type=source_type,
                        text_source_config_id=text_source_config_id,
                        query_terms=query_terms,
                        query_payload=query_payload,
                        status="skipped",
                        skip_reason_code=str(scheduled_item.get("skip_reason_code") or "source_disabled"),
                    )
                )
                continue
            provider = config.provider if isinstance(config, TextSourceConfig) else SOURCE_PROVIDER_MAP[source_type]
            request_type = config.request_type if isinstance(config, TextSourceConfig) else "text_search"
            if request_type != "text_search":
                discovery_items.append(
                    ScheduledDiscoveryItemRecord(
                        discovery_run_id=discovery_run_id,
                        instrument_id=instrument_id,
                        source_type=source_type,
                        text_source_config_id=text_source_config_id,
                        query_terms=query_terms,
                        query_payload=query_payload,
                        status="skipped",
                        skip_reason_code="source_disabled",
                    )
                )
                continue
            idempotency_key = f"{job.idempotency_key}:text_search:{instrument_id or 'market_wide'}:{source_type}"
            request_payload = {
                "operation": "scheduled_external_news_discovery"
                if intake_request.discovery_mode == "scheduled"
                else "text_search",
                "discovery_run_id": discovery_run_id,
                "discovery_mode": intake_request.discovery_mode,
                "per_instrument_discovery": True,
                "source_type": source_type,
                "text_source_config_id": text_source_config_id,
                "time_range": dict(intake_request.time_range),
                "query_terms": list(query_terms),
                "query": query_payload,
                "routing_targets": list(intake_request.routing_targets),
            }
            request_payload.update(self._transport_payload_for_source(config, query_payload))
            request = ExternalRequest(
                request_id=f"request_{_stable_hash({'idempotency_key': idempotency_key})[:24]}",
                caller_module=self.module_name,
                provider=provider,
                request_type=request_type,
                universe_id=intake_request.universe_id,
                instrument_ids=(instrument_id,) if instrument_id else (),
                payload=request_payload,
                cache_policy=CachePolicy(
                    use_cache=True,
                    max_age_seconds=self._cache_ttl(source_type, config),
                    write_cache=True,
                ),
                timeout_ms=5000,
                retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
                idempotency_key=idempotency_key,
            )
            requests.append(request)
            discovery_items.append(
                ScheduledDiscoveryItemRecord(
                    discovery_run_id=discovery_run_id,
                    instrument_id=instrument_id,
                    source_type=source_type,
                    text_source_config_id=text_source_config_id,
                    query_terms=query_terms,
                    query_payload=query_payload,
                    status="request_created",
                    external_request_id=request.request_id,
                )
            )
            request_contexts[request.request_id] = {
                "discovery_run_id": discovery_run_id,
                "discovery_mode": intake_request.discovery_mode,
                "instrument_id": instrument_id,
                "source_type": source_type,
                "provider": provider,
                "text_source_config_id": text_source_config_id,
                "query_terms": list(query_terms),
                "query": query_payload,
                "external_request_id": request.request_id,
                **self._source_context(config, source_type, query_payload),
            }
        return requests, discovery_items, request_contexts

    def fetch_text_via_gateway(
        self,
        intake_request: DataIntakeRequest,
        job: ModuleJob,
        active_profiles: tuple[Any, ...],
        text_source_configs: Mapping[str, TextSourceConfig],
        started_at: str,
    ) -> tuple[list[ExternalRequest], list[ExternalResponse], list[str], list[ExternalTextSearchRequestRecord]]:
        responses: list[ExternalResponse] = []
        external_text_search_records: list[ExternalTextSearchRequestRecord] = []
        discovery_run_id = self._discovery_run_id(job, intake_request)
        self.repository.save_discovery_run(
            self._discovery_run_record(
                discovery_run_id=discovery_run_id,
                job=job,
                intake_request=intake_request,
                active_profiles=active_profiles,
                active_instruments_with_discovery_request=0,
                coverage_ratio=0.0,
                status="running",
                started_at=started_at,
                finished_at=None,
            )
        )
        discovery_schedule, warnings = self.schedule_per_instrument_news_discovery(
            intake_request,
            active_profiles,
            text_source_configs,
        )
        requests, discovery_items, request_contexts = self.create_text_search_external_requests(
            intake_request,
            job,
            discovery_run_id,
            discovery_schedule,
        )
        for item_record in discovery_items:
            discovery_item_ref = self.repository.save_discovery_item(item_record)
            if not item_record.external_request_id:
                continue
            context = {
                **dict(request_contexts[item_record.external_request_id]),
                "discovery_item_ref": discovery_item_ref,
                "discovery_item_id": _ref_id(discovery_item_ref),
            }
            request_contexts[item_record.external_request_id] = context
            external_record = ExternalTextSearchRequestRecord(
                discovery_item_ref=discovery_item_ref,
                request_id=item_record.external_request_id,
                instrument_id=item_record.instrument_id,
                source_type=item_record.source_type,
                provider=str(context.get("provider") or self._provider_for_source(item_record.source_type, text_source_configs)),
                request_type="text_search",
                query_terms=item_record.query_terms,
                query_payload=item_record.query_payload,
            )
            external_ref = self.repository.save_external_text_search_request(external_record)
            external_record = replace(
                external_record,
                external_text_search_request_ref=external_ref,
            )
            external_text_search_records.append(external_record)

        for request in requests:
            if self.gateway is None:
                continue
            try:
                response = self.gateway.process(request)
                responses.append(
                    self._with_response_context(
                        self._coerce_external_response(response),
                        request_contexts.get(request.request_id, {}),
                    )
                )
            except Exception as error:
                source_type = str(request.payload.get("source_type") or "")
                instrument_id = request.instrument_ids[0] if request.instrument_ids else "market_wide"
                warnings.append(f"gateway_text_request_failed:{instrument_id}:{source_type}:{error}")

        active_with_requests = self._active_instruments_with_discovery_request(active_profiles, requests)
        coverage_ratio = scheduled_discovery_coverage_ratio(active_with_requests, len(active_profiles))
        self.repository.save_discovery_run(
            self._discovery_run_record(
                discovery_run_id=discovery_run_id,
                job=job,
                intake_request=intake_request,
                active_profiles=active_profiles,
                active_instruments_with_discovery_request=active_with_requests,
                coverage_ratio=coverage_ratio,
                status="completed" if not warnings else "partial_success",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                payload={
                    "external_request_ids": [request.request_id for request in requests],
                    "warnings": list(warnings),
                },
            )
        )
        return requests, responses, warnings, external_text_search_records

    def deduplicate_text(self, raw_item: RawTextItem) -> tuple[bool, str | None, float]:
        existing = self.repository.find_raw_text_item_by_hash(raw_item.content_hash)
        if existing is not None:
            return True, self._raw_text_ref(existing), 1.0
        new_text = f"{raw_item.title}\n{raw_item.body}"
        best_ref: str | None = None
        best_similarity = 0.0
        for existing_item in self.repository.list_recent_raw_text_items(raw_item.universe_id):
            existing_text = f"{_profile_value(existing_item, 'title', '')}\n{_profile_value(existing_item, 'body', '')}"
            similarity = token_similarity(new_text, existing_text)
            if similarity > best_similarity:
                best_similarity = similarity
                best_ref = self._raw_text_ref(existing_item)
        if best_similarity >= 0.92:
            return True, best_ref, best_similarity
        return False, None, best_similarity

    def detect_language(self, title: str, body: str) -> str:
        return detect_text_language(title, body)

    def map_text_to_instrument_ids(
        self,
        raw_item: RawTextItem,
        active_profiles: tuple[Any, ...],
    ) -> tuple[tuple[str, ...], float]:
        text = f"{raw_item.title}\n{raw_item.body}"
        explicit_ids = set(raw_item.instrument_ids)
        scored: list[tuple[str, float]] = []
        for profile in active_profiles:
            instrument_id = str(_profile_value(profile, "instrument_id"))
            score = 0.0
            for alias, alias_type in self._profile_aliases(profile):
                if self._contains_alias(text, alias):
                    score = max(score, self._alias_score(alias_type))
            if instrument_id in explicit_ids:
                score = max(score, 0.60)
            if score > 0:
                scored.append((instrument_id, score))
        if not scored:
            return (), 0.0
        scored.sort(key=lambda item: (-item[1], item[0]))
        return tuple(instrument_id for instrument_id, _score in scored), max(score for _instrument_id, score in scored)

    def score_source_credibility(self, source_type: str) -> float:
        return source_credibility_score(source_type, SOURCE_CREDIBILITY_REGISTRY)

    def classify_text_category(self, raw_item: RawTextItem) -> str:
        source_type = raw_item.source_type
        if source_type in MACRO_SOURCE_TYPES:
            return "macro"
        if source_type in REGULATORY_SOURCE_TYPES:
            return "regulation"
        text = normalize_text(f"{raw_item.title} {raw_item.body}")
        if _contains_any(text, DIVIDEND_KEYWORDS):
            return "dividend"
        if _contains_any(text, EARNINGS_KEYWORDS):
            return "earnings"
        if _contains_any(text, CORPORATE_ACTION_KEYWORDS):
            return "corporate_action"
        if _contains_any(text, REGULATION_KEYWORDS):
            return "regulation"
        if _contains_any(text, MACRO_KEYWORDS):
            return "macro"
        if text.strip():
            return "news"
        return "other"

    def write_raw_text_item(self, raw_item: RawTextItem) -> str:
        return self.repository.save_raw_text_item(raw_item)

    def create_routing_message(
        self,
        raw_item: RawTextItem,
        raw_text_ref: str,
        intake_request: DataIntakeRequest,
    ) -> RoutingMessage | None:
        category_targets = CATEGORY_TARGETS.get(raw_item.text_category, CATEGORY_TARGETS["other"])
        target_modules = tuple(target for target in category_targets if target in intake_request.routing_targets)
        if not target_modules:
            target_modules = intake_request.routing_targets
        if not target_modules:
            return None
        created_at = to_utc_iso(utc_now())
        payload = {
            "raw_text_ref": raw_text_ref,
            "discovery_mode": intake_request.discovery_mode,
            "instrument_ids": raw_item.instrument_ids,
            "text_category": raw_item.text_category,
            "target_modules": target_modules,
            "created_at": created_at,
            "discovery_run_id": raw_item.discovery_run_id,
        }
        return RoutingMessage(
            routing_message_id=f"routing_{_stable_hash(payload)[:24]}",
            raw_text_ref=raw_text_ref,
            universe_id=raw_item.universe_id,
            discovery_mode=intake_request.discovery_mode,
            instrument_ids=raw_item.instrument_ids,
            text_category=raw_item.text_category,
            source_type=raw_item.source_type,
            source_credibility_score=raw_item.source_credibility_score,
            relevance_score=raw_item.relevance_score,
            target_modules=target_modules,
            created_at=created_at,
            routing_ttl_seconds=intake_request.routing_ttl_seconds,
            routing_reason=f"category:{raw_item.text_category}",
            discovery_run_id=raw_item.discovery_run_id,
        )

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

    def _coerce_input(self, payload: Mapping[str, Any], job: ModuleJob) -> DataIntakeRequest:
        return DataIntakeRequest.from_dict(payload, job)

    def _classify_and_map(
        self,
        raw_item: RawTextItem,
        active_profiles: tuple[Any, ...],
    ) -> RawTextItem:
        instrument_candidates = self._instrument_candidates(raw_item, active_profiles)
        issuer_candidates = self._issuer_candidates(instrument_candidates, active_profiles)
        mapped_ids = tuple(
            str(candidate["instrument_id"])
            for candidate in instrument_candidates
            if float(candidate.get("score") or 0.0) >= 0.70
        )
        entity_match_score = max((float(candidate.get("score") or 0.0) for candidate in instrument_candidates), default=0.0)
        source_score = self.score_source_credibility(raw_item.source_type)
        category = self.classify_text_category(raw_item)
        topic_match = self._topic_match_score(category)
        trust_level = raw_item.trust_level or TRUST_LEVEL_BY_SOURCE_TYPE.get(raw_item.source_type, "unknown")
        quality_flags = self._quality_flags_for_raw_item(
            raw_item=raw_item,
            category=category,
            trust_level=trust_level,
            instrument_candidates=instrument_candidates,
        )
        relevance = relevance_score(entity_match_score, topic_match, source_score)
        raw_confidence = self._raw_confidence_score(
            source_score=source_score,
            entity_match_score=entity_match_score,
            relevance=relevance,
            quality_flags=quality_flags,
        )
        enriched_payload = {
            **dict(raw_item.source_payload),
            "source": raw_item.source or raw_item.source_type,
            "source_type": raw_item.source_type,
            "trust_level": trust_level,
            "confidence_score": raw_confidence,
            "instrument_candidates": [dict(item) for item in instrument_candidates],
            "issuer_candidates": [dict(item) for item in issuer_candidates],
            "quality_flags": list(quality_flags),
            "confirmation_status": self._confirmation_status(raw_item.source_type),
        }
        return replace(
            raw_item,
            instrument_ids=mapped_ids,
            language=raw_item.language or self.detect_language(raw_item.title, raw_item.body),
            text_category=category,
            instrument_mapping_confidence=instrument_mapping_confidence(entity_match_score),
            source_credibility_score=source_score,
            relevance_score=relevance,
            trust_level=trust_level,
            confidence_score=raw_confidence,
            instrument_candidates=instrument_candidates,
            issuer_candidates=issuer_candidates,
            quality_flags=quality_flags,
            source_payload=enriched_payload,
        )

    def _raw_payloads_from_external_response(
        self,
        response: ExternalResponse,
        intake_request: DataIntakeRequest,
    ) -> tuple[list[Mapping[str, Any]], list[str]]:
        warnings: list[str] = []
        if response.provider == "polza_ai":
            warnings.append(f"llm_response_not_raw_text_source:{response.request_id}")
            return [], warnings
        if response.status not in {"success", "partial_success"}:
            warnings.append(f"external_response_not_success:{response.request_id}:{response.status}")
            return [], warnings
        data = dict(response.data)
        response_context = data.get("_intake_request_context")
        if not isinstance(response_context, Mapping):
            response_context = {}
        source_type = str(
            response_context.get("source_type")
            or self._source_type_from_provider(response.provider, intake_request.source_types)
        )
        context_instrument_id = _optional_text(response_context.get("instrument_id"))
        default_instrument_ids = (context_instrument_id,) if context_instrument_id else ()
        context_payload = {
            "discovery_run_id": response_context.get("discovery_run_id"),
            "discovery_item_id": response_context.get("discovery_item_id"),
            "discovery_item_ref": response_context.get("discovery_item_ref"),
            "discovery_mode": response_context.get("discovery_mode"),
            "external_request_id": response_context.get("external_request_id") or response.request_id,
            "source_name": response_context.get("source_name"),
            "source_layer": response_context.get("source_layer"),
            "trust_level": response_context.get("trust_level"),
            "quality_flags": response_context.get("quality_flags"),
        }
        items = data.get("items")
        if not isinstance(items, list):
            nested_data = data.get("data")
            if isinstance(nested_data, Mapping) and isinstance(nested_data.get("items"), list):
                items = nested_data["items"]
        if isinstance(items, list):
            payloads = []
            for item in items:
                if isinstance(item, Mapping):
                    item_source_type = str(item.get("source_type") or source_type)
                    if item_source_type not in VALID_SOURCE_TYPES:
                        warnings.append(
                            f"external_item_invalid_source_type_coerced:{response.request_id}:{item_source_type}"
                        )
                        item_source_type = source_type if source_type in VALID_SOURCE_TYPES else "news_api"
                    payloads.append(
                        {
                            **dict(item),
                            "source_type": item_source_type,
                            "source_ref": item.get("source_ref") or response.data_ref or response.request_id,
                            "universe_id": item.get("universe_id") or intake_request.universe_id,
                            "instrument_ids": item.get("instrument_ids") or default_instrument_ids,
                            **{key: value for key, value in context_payload.items() if value},
                        }
                    )
            return payloads, warnings
        if any(key in data for key in ("title", "headline", "body", "text", "content", "url", "source_url")):
            data_source_type = str(data.get("source_type") or source_type)
            if data_source_type not in VALID_SOURCE_TYPES:
                warnings.append(f"external_item_invalid_source_type_coerced:{response.request_id}:{data_source_type}")
                data_source_type = source_type if source_type in VALID_SOURCE_TYPES else "news_api"
            return [
                {
                    **data,
                    "source_type": data_source_type,
                    "source_ref": data.get("source_ref") or response.data_ref or response.request_id,
                    "universe_id": data.get("universe_id") or intake_request.universe_id,
                    "instrument_ids": data.get("instrument_ids") or default_instrument_ids,
                    **{key: value for key, value in context_payload.items() if value},
                }
            ], warnings
        warnings.append(f"external_response_has_no_text_items:{response.request_id}")
        return [], warnings

    def _raw_payload_with_valid_source(
        self,
        raw_payload: Mapping[str, Any],
        intake_request: DataIntakeRequest,
    ) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
        payload = dict(raw_payload)
        source_type = str(payload.get("source_type") or payload.get("source") or "").strip()
        if source_type in VALID_SOURCE_TYPES:
            return payload, ()
        if source_type == "polza_ai" or str(payload.get("provider") or "").strip() == "polza_ai" or payload.get("model_id"):
            return None, (f"llm_raw_text_payload_skipped:{source_type or 'polza_ai'}",)
        fallback_source_type = next((item for item in intake_request.source_types if item in VALID_SOURCE_TYPES), "news_api")
        payload["source_type"] = fallback_source_type
        payload.setdefault("source", source_type or fallback_source_type)
        return payload, (f"raw_text_payload_invalid_source_type_coerced:{source_type or 'missing'}:{fallback_source_type}",)

    def _raw_payloads_from_refs(self, input_refs: tuple[str, ...]) -> list[Mapping[str, Any]]:
        payloads: list[Mapping[str, Any]] = []
        for ref in input_refs:
            if not ref.startswith("raw_text.raw_text_item:"):
                continue
            raw_item = self.repository.load_raw_text_item(ref)
            if raw_item is None:
                continue
            payloads.append(_to_mapping(raw_item))
        return payloads

    def _load_external_responses_from_refs(self, input_refs: tuple[str, ...]) -> list[ExternalResponse]:
        responses = []
        for ref in input_refs:
            if not ref.startswith("request_logs.external_response:"):
                continue
            response = self.repository.load_external_response(ref)
            if response is not None:
                responses.append(response)
        return responses

    def _coerce_external_response(self, payload: ExternalResponse | Mapping[str, Any]) -> ExternalResponse:
        if isinstance(payload, ExternalResponse):
            return payload
        response = getattr(payload, "response", None)
        if isinstance(response, ExternalResponse):
            return response
        return ExternalResponse.from_dict(payload)

    def _source_type_from_provider(self, provider: str, requested_source_types: tuple[str, ...]) -> str:
        if provider == "issuer_disclosure":
            issuer_sources = tuple(
                source_type
                for source_type in requested_source_types
                if source_type in OFFICIAL_DISCLOSURE_SOURCE_TYPES | {"regulatory_text"}
            )
            if len(issuer_sources) == 1:
                return issuer_sources[0]
            return "issuer_disclosure"
        if provider == "macro_api":
            macro_sources = tuple(
                source_type
                for source_type in requested_source_types
                if source_type in MACRO_SOURCE_TYPES | REGULATORY_SOURCE_TYPES
            )
            if len(macro_sources) == 1:
                return macro_sources[0]
            return "macro_text"
        if provider == "news_api":
            news_sources = tuple(source_type for source_type in requested_source_types if source_type in FAST_NEWS_SOURCE_TYPES)
            if len(news_sources) == 1:
                return news_sources[0]
            return "news_api"
        raise DataIntakeRoutingError(f"unsupported text provider for intake routing: {provider}")

    def _allows_market_wide_only(self, intake_request: DataIntakeRequest) -> bool:
        return _is_market_wide_source_set(intake_request.source_types)

    def _cache_ttl(self, source_type: str, config: Any | None = None) -> int:
        if isinstance(config, TextSourceConfig) and config.max_age_seconds is not None:
            return max(0, int(config.max_age_seconds))
        if source_type in FAST_NEWS_SOURCE_TYPES:
            return 300
        if source_type in MACRO_SOURCE_TYPES | REGULATORY_SOURCE_TYPES:
            return 1800
        return 3600

    def _source_enabled(
        self,
        source_type: str,
        text_source_config: Mapping[str, Any],
        config: TextSourceConfig | None = None,
    ) -> bool:
        if config is None or not config.enabled or config.request_type != "text_search":
            return False
        if not _strict_bool(config.source_policy.get("gateway_only", True)):
            return False
        disabled_sources = set(str(item) for item in (text_source_config.get("disabled_source_types") or ()))
        if source_type in disabled_sources:
            return False
        enabled_sources = text_source_config.get("enabled_source_types")
        if enabled_sources is not None and source_type not in {str(item) for item in enabled_sources}:
            return False
        source_enabled = text_source_config.get("source_enabled")
        if isinstance(source_enabled, Mapping) and source_type in source_enabled:
            return _strict_bool(source_enabled[source_type])
        sources = text_source_config.get("sources")
        if isinstance(sources, Mapping):
            source_config = sources.get(source_type)
            if isinstance(source_config, Mapping) and "enabled" in source_config:
                return _strict_bool(source_config["enabled"])
        return True

    def _market_wide_source_allowed(self, config: TextSourceConfig | None) -> bool:
        if config is None:
            return False
        return (
            _strict_bool(config.source_policy.get("market_wide_allowed", False))
            and not _strict_bool(config.source_policy.get("requires_active_instrument", True))
        )

    def _instrument_eligible(
        self,
        instrument_id: str,
        source_type: str,
        text_source_config: Mapping[str, Any],
    ) -> bool:
        disabled_instruments = set(str(item) for item in (text_source_config.get("disabled_instrument_ids") or ()))
        if instrument_id in disabled_instruments:
            return False
        instrument_eligibility = text_source_config.get("instrument_eligibility")
        if isinstance(instrument_eligibility, Mapping) and instrument_id in instrument_eligibility:
            return _strict_bool(instrument_eligibility[instrument_id])
        source_eligibility = text_source_config.get("source_instrument_eligibility")
        if isinstance(source_eligibility, Mapping):
            by_instrument = source_eligibility.get(source_type)
            if isinstance(by_instrument, Mapping) and instrument_id in by_instrument:
                return _strict_bool(by_instrument[instrument_id])
        return True

    def _coerce_text_source_config(self, payload: Any) -> TextSourceConfig:
        return TextSourceConfig.from_any(payload)

    def _provider_for_source(
        self,
        source_type: str,
        text_source_configs: Mapping[str, TextSourceConfig],
    ) -> str:
        config = text_source_configs.get(source_type)
        if config is not None:
            return config.provider
        return SOURCE_PROVIDER_MAP[source_type]

    def _query_payload_for_source(
        self,
        query: Mapping[str, Any],
        config: TextSourceConfig | None,
    ) -> Mapping[str, Any]:
        query_fields = ("ticker", "issuer_name", "aliases", "related_entities")
        if config is not None and isinstance(config.query_template, Mapping):
            configured_fields = tuple(str(field) for field in (config.query_template.get("query_fields") or ()))
            if configured_fields:
                query_fields = configured_fields
        payload = {
            "instrument_id": str(query.get("instrument_id") or ""),
            "ticker": str(query.get("ticker") or ""),
            "issuer_name": str(query.get("issuer_name") or ""),
            "aliases": list(query.get("aliases") or ()),
            "related_entities": list(query.get("related_entities") or ()),
            "query_terms": list(query.get("query_terms") or ()),
            "query_fields": list(query_fields),
        }
        if query.get("sector") or "sector" in query_fields:
            payload["sector"] = str(query.get("sector") or "")
        if query.get("market_wide"):
            payload["market_wide"] = True
        if config is not None:
            source_name = config.source_policy.get("source_name")
            trust_level = config.source_policy.get("trust_level")
            source_layer = config.source_policy.get("source_layer")
            if source_name:
                payload["source_name"] = str(source_name)
            if trust_level:
                payload["trust_level"] = str(trust_level)
            if source_layer:
                payload["source_layer"] = str(source_layer)
            endpoint_key = config.source_policy.get("endpoint_metadata_key")
            metadata = query.get("metadata")
            if endpoint_key and isinstance(metadata, Mapping):
                endpoint = metadata.get(str(endpoint_key))
                if isinstance(endpoint, str) and endpoint.strip():
                    payload["endpoint"] = endpoint.strip()
        return payload

    def _transport_payload_for_source(
        self,
        config: TextSourceConfig | None,
        query_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        transport: dict[str, Any] = {}
        if config is not None and isinstance(config.query_template, Mapping):
            for key in ("path", "endpoint", "method", "query_params"):
                value = config.query_template.get(key)
                if value not in (None, "", {}):
                    transport[key] = value
        endpoint = query_payload.get("endpoint")
        if isinstance(endpoint, str) and endpoint.strip():
            transport["endpoint"] = endpoint.strip()
        return transport

    def _source_context(
        self,
        config: TextSourceConfig | None,
        source_type: str,
        query_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        policy = dict(config.source_policy) if config is not None else {}
        trust_level = str(policy.get("trust_level") or query_payload.get("trust_level") or TRUST_LEVEL_BY_SOURCE_TYPE.get(source_type, "unknown"))
        flags: list[str] = []
        if source_type in FAST_NEWS_SOURCE_TYPES:
            flags.append("candidate_early_signal")
        if source_type == "smartlab_news":
            flags.append("weak_source")
        if str(policy.get("fetch_scope") or "") == "market_wide_once":
            flags.append("market_wide_feed")
        if source_type in OFFICIAL_CONFIRMATION_SOURCE_TYPES:
            flags.append("official_confirmation_layer")
        return {
            "source_name": policy.get("source_name") or query_payload.get("source_name") or source_type,
            "source_layer": policy.get("source_layer") or query_payload.get("source_layer"),
            "trust_level": trust_level,
            "quality_flags": tuple(dict.fromkeys(flags)),
        }

    def _market_wide_once_source(self, config: TextSourceConfig | None) -> bool:
        if config is None:
            return False
        return str(config.source_policy.get("fetch_scope") or "") == "market_wide_once"

    def _source_has_required_endpoint(
        self,
        config: TextSourceConfig | None,
        query_payload: Mapping[str, Any],
    ) -> bool:
        if config is None:
            return False
        if not _strict_bool(config.source_policy.get("requires_endpoint", False)):
            return True
        if query_payload.get("endpoint"):
            return True
        return bool(config.query_template.get("endpoint") or config.query_template.get("path"))

    def _discovery_run_id(self, job: ModuleJob, intake_request: DataIntakeRequest) -> str:
        payload = {
            "job_id": job.job_id,
            "idempotency_key": job.idempotency_key,
            "universe_id": intake_request.universe_id,
            "source_types": intake_request.source_types,
            "time_range": dict(intake_request.time_range),
            "discovery_mode": intake_request.discovery_mode,
        }
        return f"discovery_run_{_stable_hash(payload)[:24]}"

    def _discovery_run_record(
        self,
        discovery_run_id: str,
        job: ModuleJob,
        intake_request: DataIntakeRequest,
        active_profiles: tuple[Any, ...],
        active_instruments_with_discovery_request: int,
        coverage_ratio: float,
        status: str,
        started_at: str | None,
        finished_at: str | None,
        payload: Mapping[str, Any] | None = None,
    ) -> ScheduledDiscoveryRunRecord:
        return ScheduledDiscoveryRunRecord(
            discovery_run_id=discovery_run_id,
            module_job_id=job.job_id,
            universe_id=intake_request.universe_id,
            discovery_mode=intake_request.discovery_mode,
            per_instrument_discovery=intake_request.per_instrument_discovery,
            source_types=intake_request.source_types,
            time_range=dict(intake_request.time_range),
            active_instruments_total=len(active_profiles),
            active_instruments_with_discovery_request=active_instruments_with_discovery_request,
            scheduled_discovery_coverage_ratio=coverage_ratio,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            payload={
                "trigger_type": job.trigger_type,
                "run_mode": job.run_mode,
                "requested_instrument_ids": list(intake_request.instrument_ids),
                **dict(payload or {}),
            },
        )

    def _active_instruments_with_discovery_request(
        self,
        active_profiles: tuple[Any, ...],
        external_requests: list[ExternalRequest],
    ) -> int:
        active_ids = {str(_profile_value(profile, "instrument_id")) for profile in active_profiles}
        request_ids = {
            request.instrument_ids[0]
            for request in external_requests
            if request.request_type == "text_search" and request.instrument_ids
        }
        return len(active_ids & request_ids)

    def _with_response_context(
        self,
        response: ExternalResponse,
        context: Mapping[str, Any],
    ) -> ExternalResponse:
        if not context:
            return response
        return replace(
            response,
            data={**dict(response.data), "_intake_request_context": dict(context)},
        )

    def _scheduled_discovery_coverage_ratio(
        self,
        intake_request: DataIntakeRequest,
        active_profiles: tuple[Any, ...],
        external_requests: list[ExternalRequest],
    ) -> float:
        if intake_request.discovery_mode != "scheduled":
            return 0.0
        return scheduled_discovery_coverage_ratio(
            self._active_instruments_with_discovery_request(active_profiles, external_requests),
            len(active_profiles),
        )

    def _audit_reason_codes(
        self,
        intake_request: DataIntakeRequest,
        warnings: list[str],
    ) -> tuple[str, ...]:
        reason_codes = [
            "only_active_universe_processed",
            "all_external_text_requests_via_gateway",
            "duplicates_detected",
            "routing_targets_explicit",
            "raw_text_items_have_source_ref",
            "no_trading_features_written_by_intake_module",
        ]
        if intake_request.discovery_mode == "scheduled":
            reason_codes.extend(
                [
                    "scheduled_discovery_runs_for_each_active_instrument",
                    "scheduled_discovery_uses_alias_issuer_and_related_entity_queries",
                ]
            )
        for warning in warnings:
            reason_code = warning.split(":", 1)[0]
            if reason_code in {"source_disabled", "instrument_not_eligible"}:
                reason_codes.append(reason_code)
        return tuple(dict.fromkeys(reason_codes))

    def _instrument_candidates(
        self,
        raw_item: RawTextItem,
        active_profiles: tuple[Any, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        text = f"{raw_item.title}\n{raw_item.body}"
        explicit_ids = set(raw_item.instrument_ids)
        candidates: list[Mapping[str, Any]] = []
        for profile in active_profiles:
            instrument_id = str(_profile_value(profile, "instrument_id"))
            matched_aliases: list[str] = []
            score = 0.0
            for alias, alias_type in self._profile_aliases(profile):
                if self._contains_alias(text, alias):
                    matched_aliases.append(alias)
                    score = max(score, self._alias_score(alias_type))
            if instrument_id in explicit_ids:
                score = max(score, 0.60)
            if score > 0:
                candidates.append(
                    {
                        "instrument_id": instrument_id,
                        "ticker": str(_profile_value(profile, "ticker", "") or ""),
                        "issuer_name": str(_profile_value(profile, "issuer_name", "") or ""),
                        "score": round(score, 6),
                        "matched_aliases": list(dict.fromkeys(matched_aliases))[:8],
                    }
                )
        candidates.sort(key=lambda item: (-float(item["score"]), str(item["instrument_id"])))
        return tuple(candidates[:10])

    def _issuer_candidates(
        self,
        instrument_candidates: tuple[Mapping[str, Any], ...],
        active_profiles: tuple[Any, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        profiles_by_id = {str(_profile_value(profile, "instrument_id")): profile for profile in active_profiles}
        candidates: list[Mapping[str, Any]] = []
        for candidate in instrument_candidates:
            profile = profiles_by_id.get(str(candidate.get("instrument_id") or ""))
            if profile is None:
                continue
            issuer_name = str(_profile_value(profile, "issuer_name", "") or candidate.get("issuer_name") or "")
            if not issuer_name:
                continue
            candidates.append(
                {
                    "issuer_name": issuer_name,
                    "instrument_id": str(candidate.get("instrument_id") or ""),
                    "ticker": str(candidate.get("ticker") or ""),
                    "score": float(candidate.get("score") or 0.0),
                }
            )
        return tuple(candidates[:10])

    def _quality_flags_for_raw_item(
        self,
        *,
        raw_item: RawTextItem,
        category: str,
        trust_level: str,
        instrument_candidates: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, ...]:
        flags: list[str] = list(raw_item.quality_flags)
        if raw_item.source_type in FAST_NEWS_SOURCE_TYPES:
            flags.append("candidate_early_signal")
        if raw_item.source_type == "smartlab_news":
            flags.append("weak_source")
        if raw_item.source_type in OFFICIAL_CONFIRMATION_SOURCE_TYPES:
            flags.append("official_confirmation_layer")
        if trust_level not in {"high", "normal_high", "normal"}:
            flags.append("low_source_trust")
        if not raw_item.published_at:
            flags.append("missing_published_at")
        if not raw_item.body:
            flags.append("missing_body")
        if not instrument_candidates and category not in MARKET_WIDE_CATEGORIES:
            flags.append("no_instrument_match")
        if category in {"corporate_action", "dividend", "earnings"} and raw_item.source_type not in OFFICIAL_CONFIRMATION_SOURCE_TYPES:
            flags.append("requires_official_confirmation")
        return tuple(dict.fromkeys(flags))

    def _raw_confidence_score(
        self,
        *,
        source_score: float,
        entity_match_score: float,
        relevance: float,
        quality_flags: tuple[str, ...],
    ) -> float:
        confidence = 0.40 * source_score + 0.35 * entity_match_score + 0.25 * relevance
        if "requires_official_confirmation" in quality_flags:
            confidence = min(confidence, 0.72)
        if "weak_source" in quality_flags:
            confidence = min(confidence, 0.55)
        if "missing_body" in quality_flags:
            confidence *= 0.80
        if "no_instrument_match" in quality_flags:
            confidence *= 0.65
        return _clip01(confidence)

    def _confirmation_status(self, source_type: str) -> str:
        if source_type in OFFICIAL_CONFIRMATION_SOURCE_TYPES:
            return "official_confirmed"
        if source_type in OFFICIAL_CONFIRMATION_SOURCE_TYPES | REGULATORY_SOURCE_TYPES:
            return "official_context"
        return "candidate_requires_official_confirmation"

    def _profile_aliases(self, profile: Any) -> tuple[tuple[str, str], ...]:
        aliases: list[tuple[str, str]] = []
        for field_name, alias_type in (
            ("ticker", "ticker"),
            ("issuer_name", "issuer"),
            ("isin", "identifier"),
            ("figi", "identifier"),
            ("arena_go_secid", "ticker"),
        ):
            value = _optional_text(_profile_value(profile, field_name))
            if value:
                aliases.append((value, alias_type))
        for alias in _profile_value(profile, "aliases", ()) or ():
            value = _optional_text(alias)
            if value:
                aliases.append((value, "alias"))
        for entity in _profile_value(profile, "related_entities", ()) or ():
            value = _optional_text(entity)
            if value:
                aliases.append((value, "related_entity"))
        return tuple(dict.fromkeys(aliases))

    def _contains_alias(self, text: str, alias: str) -> bool:
        alias = str(alias or "").strip()
        if not alias:
            return False
        if len(alias) <= 2 or alias.isupper():
            tokens = set(re.findall(r"\b[A-Z0-9]{1,16}\b", text.upper()))
            return alias.upper() in tokens
        normalized_alias = normalize_text(alias)
        normalized_text = f" {normalize_text(text)} "
        return f" {normalized_alias} " in normalized_text

    def _alias_score(self, alias_type: str) -> float:
        return {
            "ticker": 1.0,
            "identifier": 0.95,
            "issuer": 0.90,
            "alias": 0.85,
            "related_entity": 0.70,
        }.get(alias_type, 0.50)

    def _topic_match_score(self, category: str) -> float:
        if category in {"earnings", "dividend", "corporate_action", "macro", "regulation"}:
            return 1.0
        if category == "news":
            return 0.80
        return 0.25

    def _raw_text_ref(self, raw_item: Any) -> str:
        return f"raw_text.raw_text_item:{_profile_value(raw_item, 'raw_text_item_id')}"

    def _status(
        self,
        raw_text_items: list[RawTextItem],
        routing_messages: list[RoutingMessage],
        warnings: list[str],
    ) -> str:
        if routing_messages and not warnings:
            return "success"
        if raw_text_items or routing_messages:
            return "partial_success"
        return "skipped"

    def _module_job_result(
        self,
        job: ModuleJob,
        started_at: str,
        status: str,
        output_refs: tuple[str, ...],
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        metrics: Mapping[str, float | int],
        events_written: int,
    ) -> ModuleJobResult:
        return ModuleJobResult(
            job_id=job.job_id,
            module_name=self.module_name,
            status=status,
            started_at=started_at,
            finished_at=to_utc_iso(utc_now()),
            output_refs=output_refs,
            warnings=warnings,
            errors=errors,
            metrics_written=len(metrics),
            events_written=events_written,
            data_quality_score=float(metrics.get("relevance_score") or 0.0),
        )

    def _empty_result(
        self,
        job: ModuleJob,
        started_at: str,
        status: str,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        audit_event_type: str,
    ) -> DataIntakeExecutionResult:
        metrics = {
            "text_items_fetched": 0,
            "scheduled_discovery_coverage_ratio": 0.0,
            "duplicate_ratio": 0.0,
            "text_search_requests_created": 0,
            "instrument_mapping_confidence": 0.0,
            "source_credibility_score": 0.0,
            "relevance_score": 0.0,
            "routing_latency_ms": 0.0,
        }
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="warning" if status != "success" else "info",
                event_type=audit_event_type,
                message="Data intake produced no routable text",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=tuple(warnings or errors or ("no_routable_text",)),
                payload={"warnings": list(warnings), "errors": list(errors), "metrics": metrics},
            )
        )
        return DataIntakeExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status=status,
                output_refs=(),
                warnings=warnings,
                errors=errors,
                metrics=metrics,
                events_written=0,
            ),
            raw_text_items=(),
            routing_messages=(),
            text_dedup_records=(),
            source_credibility_records=(),
            external_requests=(),
            audit_ref=audit_ref,
            metrics=metrics,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> DataIntakeExecutionResult:
        metrics = {
            "text_items_fetched": 0,
            "scheduled_discovery_coverage_ratio": 0.0,
            "duplicate_ratio": 0.0,
            "text_search_requests_created": 0,
            "instrument_mapping_confidence": 0.0,
            "source_credibility_score": 0.0,
            "relevance_score": 0.0,
            "routing_latency_ms": 0.0,
        }
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="intake_routing_failed",
                message="Data intake and routing failed",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=("intake_routing_failed",),
                payload={"error": str(error), "metrics": metrics},
            )
        )
        return DataIntakeExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics=metrics,
                events_written=0,
            ),
            raw_text_items=(),
            routing_messages=(),
            text_dedup_records=(),
            source_credibility_records=(),
            external_requests=(),
            audit_ref=audit_ref,
            metrics=metrics,
        )


DIVIDEND_KEYWORDS = (
    "dividend",
    "\u0434\u0438\u0432\u0438\u0434\u0435\u043d\u0434",
    "\u043e\u0442\u0441\u0435\u0447",
    "record date",
)
EARNINGS_KEYWORDS = (
    "earnings",
    "financial results",
    "report",
    "\u043e\u0442\u0447\u0435\u0442",
    "\u043e\u0442\u0447\u0435\u0442\u043d",
    "\u043c\u0441\u0444\u043e",
    "\u0440\u0441\u0431\u0443",
)
CORPORATE_ACTION_KEYWORDS = (
    "split",
    "buyback",
    "delisting",
    "halt",
    "ticker change",
    "\u0441\u043f\u043b\u0438\u0442",
    "\u0432\u044b\u043a\u0443\u043f",
    "\u0434\u0435\u043b\u0438\u0441\u0442\u0438\u043d\u0433",
    "\u043f\u0440\u0438\u043e\u0441\u0442\u0430\u043d\u043e\u0432",
)
REGULATION_KEYWORDS = (
    "regulation",
    "sanction",
    "law",
    "central bank",
    "\u0440\u0435\u0433\u0443\u043b\u044f\u0442\u043e\u0440",
    "\u0441\u0430\u043d\u043a\u0446",
    "\u0446\u0431",
)
MACRO_KEYWORDS = (
    "macro",
    "inflation",
    "key rate",
    "ofz",
    "oil",
    "\u043a\u043b\u044e\u0447\u0435\u0432\u0430\u044f \u0441\u0442\u0430\u0432\u043a\u0430",
    "\u0438\u043d\u0444\u043b\u044f\u0446",
    "\u043e\u0444\u0437",
    "\u043d\u0435\u0444\u0442",
)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    normalized_keywords = tuple(normalize_text(keyword) for keyword in keywords)
    padded_text = f" {text} "
    return any(f" {keyword} " in padded_text for keyword in normalized_keywords if keyword)


def _is_market_wide_source_set(source_types: tuple[str, ...]) -> bool:
    return bool(source_types) and set(source_types) <= (MACRO_SOURCE_TYPES | REGULATORY_SOURCE_TYPES)


def _strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "enabled"}:
            return True
        if normalized in {"false", "0", "no", "disabled"}:
            return False
    return bool(value)


def _as_mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    return tuple(item for item in value if isinstance(item, Mapping))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ref_id(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return text.rsplit(":", 1)[-1]


def _profile_value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item or "").strip())
    return (str(value),)


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return (dict(value),)
    if isinstance(value, (list, tuple)):
        return tuple(dict(item) for item in value if isinstance(item, Mapping))
    return ()


def _float_or_zero(value: Any) -> float:
    try:
        return _clip01(float(value))
    except (TypeError, ValueError):
        return 0.0


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _to_mapping(item: Any) -> Mapping[str, Any]:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if isinstance(item, Mapping):
        return item
    return dict(getattr(item, "__dict__", {}))


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _average(values: list[float | int]) -> float:
    if not values:
        return 0.0
    return float(sum(float(value) for value in values) / len(values))


def _to_epoch_ms(value: str | None) -> int:
    if not value:
        return int(time.time() * 1000)
    return int(parse_utc_iso(value).timestamp() * 1000)
