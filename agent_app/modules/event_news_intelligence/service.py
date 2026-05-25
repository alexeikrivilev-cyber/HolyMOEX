from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from agent_app.contracts.unified_objects import (
    CachePolicy,
    ExternalRequest,
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
    abnormal_return_after_event,
    abnormal_volume_after_event,
    clip,
    clip_required,
    event_confidence_score,
    event_decay_score,
    event_reaction,
    normalize_sentiment,
    overreaction_score,
    signed_score,
    underreaction_score,
    weighted_average,
)
from .repository import (
    AuditRecord,
    EventNewsIntelligenceRepository,
    EventReaction,
    EventRoutingMessage,
    FeatureRecord,
    InMemoryEventNewsIntelligenceRepository,
    InstrumentProfile,
    RawTextItem,
    StructuredEvent,
    stable_record_id,
)


MODULE_NAME = "Event & News Intelligence Module"
CALCULATION_VERSION = "event_news_intelligence_v1"
DEFAULT_FAST_MODEL_ID = "deepseek/deepseek-v4-flash"
DEFAULT_REASONING_MODEL_ID = "qwen/qwen3.6-35b-a3b"
DEFAULT_MODEL_ID = DEFAULT_REASONING_MODEL_ID
PROHIBITED_POLZA_MODELS = {"deepseek/" + "deepseek-v4" + "-pro"}

VALID_CONTOURS = {"event_contour", "intraday_contour"}
VALID_HORIZONS = {"intraday", "swing", "position"}
INPUT_FIELDS = {
    "routing_message_refs",
    "raw_text_refs",
    "instrument_ids",
    "event_ontology_version",
    "llm_prompt_version",
    "market_reaction_window",
}
VALID_REACTION_WINDOWS = {"5m", "1h", "1d"}
VALID_EVENT_TYPES = {
    "earnings",
    "dividend",
    "corporate_action",
    "macro",
    "regulation",
    "sanctions",
    "management",
    "sector",
    "market_structure",
    "other",
}
VALID_TASK_TYPES = {
    "event_extraction",
    "sentiment_scoring",
    "news_classification",
    "entity_matching",
    "duplicate_preclassification",
    "disclosure_classification",
    "short_news_summarization",
    "raw_text_relevance_filtering",
    "report_extraction",
    "earnings_analysis",
    "dividend_extraction",
    "macro_text_analysis",
    "complex_corporate_action_interpretation",
    "multi_source_event_synthesis",
    "validation_research_commentary",
    "strategy_risk_explanation",
}
FAST_LLM_TASK_TYPES = {
    "event_extraction",
    "sentiment_scoring",
    "news_classification",
    "entity_matching",
    "duplicate_preclassification",
    "disclosure_classification",
    "short_news_summarization",
    "raw_text_relevance_filtering",
}
REASONING_LLM_TASK_TYPES = VALID_TASK_TYPES - FAST_LLM_TASK_TYPES
MACRO_SECTOR_EVENT_TYPES = {"macro", "sector", "market_structure"}
FORBIDDEN_LLM_FIELDS = {
    "trading_recommendation",
    "trade_recommendation",
    "recommendation",
    "trading_advice",
    "freeform_trading_recommendation",
    "decision_action",
    "order_intent",
    "target_position_pct",
    "target_quantity",
    "order_side",
    "submit_order",
}

BASE_EVENT_TTL_SECONDS = {
    "earnings": 7 * 24 * 60 * 60,
    "dividend": 14 * 24 * 60 * 60,
    "corporate_action": 14 * 24 * 60 * 60,
    "macro": 3 * 24 * 60 * 60,
    "regulation": 5 * 24 * 60 * 60,
    "sanctions": 7 * 24 * 60 * 60,
    "management": 3 * 24 * 60 * 60,
    "sector": 2 * 24 * 60 * 60,
    "market_structure": 6 * 60 * 60,
    "other": 4 * 60 * 60,
}
DEFAULT_EVENT_PRESSURE_WEIGHTS = {
    "news_sentiment_score": 1.0,
    "news_materiality_score": 1.0,
    "news_novelty_score": 1.0,
    "event_decay_score": 1.0,
}
OFFICIAL_CONFIRMATION_SOURCE_TYPES = {
    "issuer_disclosure",
    "prime_disclosure",
    "akm_disclosure",
    "corporate_site",
    "regulatory_text",
    "cbr_macro",
    "moex_macro",
    "macro_text",
}
OFFICIAL_CONFIRMATION_REQUIRED_EVENT_TYPES = {"corporate_action", "dividend", "earnings"}
SOURCE_CREDIBILITY_DEFAULTS = {
    "issuer_disclosure": 0.9,
    "prime_disclosure": 0.9,
    "akm_disclosure": 0.88,
    "regulatory_text": 0.9,
    "cbr_macro": 0.9,
    "moex_macro": 0.88,
    "corporate_site": 0.85,
    "news_api": 0.7,
    "rbc_news": 0.75,
    "tass_news": 0.75,
    "interfax_news": 0.75,
    "prime_news": 0.75,
    "finam_news": 0.70,
    "smartlab_news": 0.55,
    "macro_text": 0.75,
    "macro_api": 0.75,
}
EVENT_EXTRACTION_SYSTEM_PROMPT = (
    "Extract MOEX event JSON only. Fill instrument_ids, event_type/subtype, relevance, "
    "materiality, sentiment, novelty, confidence, evidence, reason_codes. No buy/sell advice, "
    "no weights, no risk-policy changes, no orders, no markdown."
)
REASONING_SYSTEM_PROMPT = (
    "Extract structured MOEX report, disclosure or macro intelligence as strict JSON only. "
    "Use source-grounded evidence for every score and interpretation. You may reason over the "
    "document, but do not output recommendations, weights, risk-policy changes, order intents, "
    "target positions, or free-form prose."
)
LLM_OUTPUT_SCHEMA_DESCRIPTION = {
    "schema_version": "string",
    "model_id": "string",
    "model_version": "string",
    "task_type": "event_extraction | sentiment_scoring | report_extraction | earnings_analysis | dividend_extraction | macro_text_analysis",
    "instrument_ids": ["string"],
    "items": [
        {
            "event_type": "earnings | dividend | corporate_action | macro | regulation | sanctions | management | sector | market_structure | other",
            "event_subtype": "string",
            "event_ts": "UTC ISO-8601 string",
            "instrument_ids": ["string"],
            "relevance_score": "number 0..1",
            "materiality_score": "number 0..1",
            "novelty_score": "number 0..1",
            "surprise_score": "number 0..1",
            "sentiment_score": "number -1..1",
            "confidence_score": "number 0..1",
            "evidence": ["short source-grounded text spans or facts"],
            "reason_codes": ["snake_case strings"],
            "expected_horizons": ["intraday | swing | position"],
        }
    ],
    "confidence_score": "number 0..1",
    "evidence": ["string"],
    "reason_codes": ["snake_case strings"],
    "warnings": ["string"],
}
LLM_SCORING_GUIDE = {
    "relevance_score": "Direct issuer/entity/sector relevance to requested MOEX universe.",
    "materiality_score": "Financial, regulatory, operational, sanction, dividend, earnings, or governance impact.",
    "novelty_score": "New information versus repeated/known news; use max_similarity_to_recent_events when known.",
    "surprise_score": "Difference versus known expectations, history, guidance, consensus, or prior disclosure.",
    "sentiment_score": "-1 strongly negative, 0 neutral/mixed, +1 strongly positive for affected instruments.",
    "confidence_score": "Quality of extraction given source specificity, evidence, ambiguity, and entity match.",
}


class EventNewsIntelligenceError(ValueError):
    """Raised when Event & News Intelligence violates the documented module contract."""


@dataclass(frozen=True)
class EventNewsInput:
    routing_message_refs: tuple[str, ...]
    raw_text_refs: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    event_ontology_version: str
    llm_prompt_version: str
    market_reaction_window: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "EventNewsInput":
        input_payload = payload.get("event_news_input")
        if not isinstance(input_payload, Mapping):
            raise EventNewsIntelligenceError("payload must contain event_news_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise EventNewsIntelligenceError(f"event_news_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise EventNewsIntelligenceError(f"event_news_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise EventNewsIntelligenceError("event_news_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise EventNewsIntelligenceError("event_news_input.instrument_ids must match module_job.instrument_ids")

        event_ontology_version = str(input_payload.get("event_ontology_version") or "")
        if not event_ontology_version:
            raise EventNewsIntelligenceError("event_news_input.event_ontology_version is required")
        llm_prompt_version = str(input_payload.get("llm_prompt_version") or "")
        if not llm_prompt_version:
            raise EventNewsIntelligenceError("event_news_input.llm_prompt_version is required")

        market_reaction_window = tuple(str(item) for item in (input_payload.get("market_reaction_window") or ()))
        if not market_reaction_window:
            raise EventNewsIntelligenceError("event_news_input.market_reaction_window is required")
        invalid_windows = sorted(set(market_reaction_window) - VALID_REACTION_WINDOWS)
        if invalid_windows:
            raise EventNewsIntelligenceError(f"invalid market_reaction_window: {invalid_windows}")
        if len(set(market_reaction_window)) != len(market_reaction_window):
            raise EventNewsIntelligenceError("event_news_input.market_reaction_window must not contain duplicates")

        return cls(
            routing_message_refs=tuple(str(item) for item in (input_payload.get("routing_message_refs") or ())),
            raw_text_refs=tuple(str(item) for item in (input_payload.get("raw_text_refs") or ())),
            instrument_ids=instrument_ids,
            event_ontology_version=event_ontology_version,
            llm_prompt_version=llm_prompt_version,
            market_reaction_window=market_reaction_window,
        )


@dataclass(frozen=True)
class LlmEnvelope:
    schema_version: str
    model_id: str
    model_version: str
    task_type: str
    instrument_ids: tuple[str, ...]
    items: tuple[Mapping[str, Any], ...]
    confidence_score: float
    evidence: tuple[str, ...]
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "LlmEnvelope":
        missing = [
            field_name
            for field_name in (
                "schema_version",
                "model_id",
                "model_version",
                "task_type",
                "instrument_ids",
                "items",
                "confidence_score",
                "evidence",
                "reason_codes",
                "warnings",
            )
            if field_name not in payload
        ]
        if missing:
            raise EventNewsIntelligenceError(f"LLM output missing required fields: {missing}")
        schema_version = str(payload.get("schema_version") or "")
        model_id = str(payload.get("model_id") or "")
        model_version = str(payload.get("model_version") or "")
        task_type = str(payload.get("task_type") or "")
        if not schema_version or not model_id or not model_version:
            raise EventNewsIntelligenceError("LLM output schema_version, model_id and model_version must be non-empty")
        if task_type not in VALID_TASK_TYPES:
            raise EventNewsIntelligenceError(f"invalid LLM task_type: {task_type}")
        items = payload.get("items")
        if not isinstance(items, list):
            raise EventNewsIntelligenceError("LLM output items must be a list")
        if any(not isinstance(item, Mapping) for item in items):
            raise EventNewsIntelligenceError("LLM output items must contain only JSON objects")
        confidence_score = _optional_float(payload.get("confidence_score"))
        if confidence_score is None:
            raise EventNewsIntelligenceError("LLM output confidence_score must be numeric")
        evidence = _string_tuple(payload.get("evidence"))
        reason_codes = _string_tuple(payload.get("reason_codes"))
        return cls(
            schema_version=schema_version,
            model_id=model_id,
            model_version=model_version,
            task_type=task_type,
            instrument_ids=_string_tuple(payload.get("instrument_ids")),
            items=tuple(items),
            confidence_score=clip_required(confidence_score),
            evidence=evidence,
            reason_codes=reason_codes,
            warnings=_string_tuple(payload.get("warnings")),
        )


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_type: str
    raw_value: float
    normalized_value: float | None
    unit: str
    ttl_seconds: int
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    confidence_score: float = 1.0


@dataclass(frozen=True)
class EventNewsExecutionResult:
    module_job_result: ModuleJobResult
    structured_events: tuple[StructuredEvent, ...]
    feature_records: tuple[FeatureRecord, ...]
    event_reactions: tuple[EventReaction, ...]
    structured_event_refs: tuple[str, ...]
    feature_record_refs: tuple[str, ...]
    event_reaction_refs: tuple[str, ...]

    @property
    def structured_event(self) -> StructuredEvent | None:
        if len(self.structured_events) == 1:
            return self.structured_events[0]
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "structured_events": [event.to_dict() for event in self.structured_events],
            "feature_records": [record.to_dict() for record in self.feature_records],
            "event_reactions": [reaction.to_dict() for reaction in self.event_reactions],
            "structured_event_refs": list(self.structured_event_refs),
            "feature_record_refs": list(self.feature_record_refs),
            "event_reaction_refs": list(self.event_reaction_refs),
        }
        if len(self.structured_events) == 1:
            payload["structured_event"] = self.structured_events[0].to_dict()
        return payload


class EventNewsIntelligenceService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: EventNewsIntelligenceRepository | None = None,
        gateway: Any | None = None,
        event_pressure_weights: Mapping[str, float] | None = None,
        model_id: str | None = None,
    ) -> None:
        self.repository = repository or InMemoryEventNewsIntelligenceRepository()
        self.gateway = gateway
        self.event_pressure_weights = dict(event_pressure_weights or DEFAULT_EVENT_PRESSURE_WEIGHTS)
        self.model_id = _safe_polza_model(model_id, "")

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> EventNewsExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> EventNewsExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> EventNewsExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            result = ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                errors=(f"{self.module_name} requires module_job",),
            )
            return EventNewsExecutionResult(result, (), (), (), (), (), ())

        try:
            self.validate_module_job(job)
            event_input = EventNewsInput.from_dict(payload, job)
            raw_text_items, routing_messages, profiles = self.load_text_and_metadata(event_input, job)
            warnings: list[str] = []
            if not raw_text_items:
                warnings.append("raw_text_items_missing")
                return self._empty_result(job, started_at, "skipped", tuple(warnings), ())
            max_items = _env_int("LLM_MAX_ITEMS_PER_RUN", 20)
            if max_items > 0 and len(raw_text_items) > max_items:
                warnings.append("llm_items_per_run_capped")
                raw_text_items = raw_text_items[:max_items]
            if len(profiles) < len(event_input.instrument_ids):
                warnings.append("instrument_profile_missing_or_inactive")

            structured_events: list[StructuredEvent] = []
            feature_records: list[FeatureRecord] = []
            event_reactions: list[EventReaction] = []

            for raw_item in raw_text_items:
                item_warnings, item_events, item_features, item_reactions = self.process_raw_text_item(
                    raw_item=raw_item,
                    routing_messages=tuple(
                        message
                        for message in routing_messages
                        if message.raw_text_item_id in {None, raw_item.raw_text_item_id}
                    ),
                    profiles=profiles,
                    event_input=event_input,
                    job=job,
                )
                warnings.extend(item_warnings)
                structured_events.extend(item_events)
                feature_records.extend(item_features)
                event_reactions.extend(item_reactions)

            event_refs = tuple(self.write_structured_event(event) for event in structured_events)
            reaction_refs = tuple(self.write_event_reaction(reaction) for reaction in event_reactions)
            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            output_refs = event_refs + reaction_refs + feature_refs

            no_event_only = bool(raw_text_items) and not structured_events and "no_event_found" in warnings
            if not structured_events and not no_event_only:
                warnings.append("structured_event_not_extracted")
            if structured_events and not feature_records:
                warnings.append("feature_records_not_written")

            status = (
                "success"
                if structured_events and feature_records and not warnings
                else "partial_success"
                if output_refs or no_event_only
                else "skipped"
            )
            return EventNewsExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(feature_records),
                    events_written=len(structured_events),
                ),
                structured_events=tuple(structured_events),
                feature_records=tuple(feature_records),
                event_reactions=tuple(event_reactions),
                structured_event_refs=event_refs,
                feature_record_refs=feature_refs,
                event_reaction_refs=reaction_refs,
            )
        except (EventNewsIntelligenceError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise EventNewsIntelligenceError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise EventNewsIntelligenceError("module_job.module_name must be Event & News Intelligence Module")
        if job.contour not in VALID_CONTOURS:
            raise EventNewsIntelligenceError("module_job.contour must be event_contour or intraday_contour")
        if not job.instrument_ids:
            raise EventNewsIntelligenceError("module_job.instrument_ids is required")
        if not job.horizons:
            raise EventNewsIntelligenceError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise EventNewsIntelligenceError(f"invalid module_job.horizons: {invalid_horizons}")
        if not job.run_mode:
            raise EventNewsIntelligenceError("module_job.run_mode is required")

    def load_text_and_metadata(
        self,
        event_input: EventNewsInput,
        job: ModuleJob,
    ) -> tuple[tuple[RawTextItem, ...], tuple[EventRoutingMessage, ...], tuple[InstrumentProfile, ...]]:
        routing_refs = tuple(dict.fromkeys((*event_input.routing_message_refs, *_refs_with_prefix(job.input_refs, "raw_text.event_routing_message"))))
        raw_text_refs = tuple(dict.fromkeys((*event_input.raw_text_refs, *_refs_with_prefix(job.input_refs, "raw_text.raw_text_item"))))
        routing_messages = self.repository.list_routing_messages(
            routing_refs,
            job.universe_id,
            event_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        raw_refs_from_routing = tuple(
            f"raw_text.raw_text_item:{message.raw_text_item_id}"
            for message in routing_messages
            if message.raw_text_item_id
        )
        raw_text_items = self.repository.list_raw_text_items(
            tuple(dict.fromkeys((*raw_text_refs, *raw_refs_from_routing))),
            job.universe_id,
            event_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        profiles = self.repository.list_instrument_profiles(job.universe_id, event_input.instrument_ids)
        return raw_text_items, routing_messages, profiles

    def process_raw_text_item(
        self,
        *,
        raw_item: RawTextItem,
        routing_messages: tuple[EventRoutingMessage, ...],
        profiles: tuple[InstrumentProfile, ...],
        event_input: EventNewsInput,
        job: ModuleJob,
    ) -> tuple[tuple[str, ...], tuple[StructuredEvent, ...], tuple[FeatureRecord, ...], tuple[EventReaction, ...]]:
        warnings: list[str] = []
        source_ref = f"raw_text.raw_text_item:{raw_item.raw_text_item_id}"
        try:
            envelope, llm_warnings = self.load_or_request_llm_envelope(raw_item, event_input, job)
            warnings.extend(llm_warnings)
        except EventNewsIntelligenceError as error:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="error",
                    event_type="llm_output_schema_validation_failed",
                    message=str(error),
                    object_type="raw_text_item",
                    object_ref=source_ref,
                    reason_codes=("llm_output_schema_validated",),
                    payload={"raw_text_item_id": raw_item.raw_text_item_id},
                )
            )
            return (str(error),), (), (), ()

        if envelope is None:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="warning",
                    event_type="llm_output_missing",
                    message="No validated LLM envelope available for raw text item",
                    object_type="raw_text_item",
                    object_ref=source_ref,
                    reason_codes=("llm_usage_required_for_text_semantics",),
                )
            )
            return ("llm_output_missing",), (), (), ()

        if not envelope.items:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="info",
                    event_type="llm_no_event_found",
                    message="Validated LLM envelope contained no material events for this raw text item",
                    object_type="raw_text_item",
                    object_ref=source_ref,
                    reason_codes=tuple(dict.fromkeys(("no_event_found", *envelope.reason_codes))),
                    payload={
                        "raw_text_item_id": raw_item.raw_text_item_id,
                        "task_type": envelope.task_type,
                        "model_id": envelope.model_id,
                        "confidence_score": envelope.confidence_score,
                        "warnings": list(envelope.warnings),
                        "processed_status": "no_event_found",
                    },
                )
            )
            return tuple(dict.fromkeys(("no_event_found", *envelope.warnings))), (), (), ()

        structured_events: list[StructuredEvent] = []
        feature_records: list[FeatureRecord] = []
        event_reactions: list[EventReaction] = []
        for index, item in enumerate(envelope.items):
            try:
                event = self.apply_event_ontology(
                    item=item,
                    envelope=envelope,
                    raw_item=raw_item,
                    routing_messages=routing_messages,
                    profiles=profiles,
                    event_input=event_input,
                    job=job,
                    item_index=index,
                )
                structured_events.append(event)
                reactions = self.compute_market_reaction_if_available(
                    item=item,
                    raw_item=raw_item,
                    event=event,
                    event_input=event_input,
                    job=job,
                    profiles=profiles,
                )
                event_reactions.extend(reactions)
                for instrument_id in event.instrument_ids:
                    metrics = self.compute_event_metric_values(
                        item=item,
                        envelope=envelope,
                        raw_item=raw_item,
                        event=event,
                        instrument_id=instrument_id,
                        profiles=profiles,
                        reactions=reactions,
                        event_input=event_input,
                        job=job,
                    )
                    horizons = self.expected_horizons(item, event.event_type, job.horizons)
                    for metric_value in metrics:
                        for horizon in horizons:
                            feature_records.append(
                                self.build_feature_record(
                                    event=event,
                                    metric_value=metric_value,
                                    instrument_id=instrument_id,
                                    horizon=horizon,
                                    contour=job.contour,
                                )
                            )
            except EventNewsIntelligenceError as error:
                warnings.append(str(error))
                self.write_audit_record(
                    AuditRecord(
                        module_name=self.module_name,
                        job_id=job.job_id,
                        severity="warning",
                        event_type="structured_event_rejected",
                        message=str(error),
                        object_type="raw_text_item",
                        object_ref=source_ref,
                        reason_codes=("event_type_from_ontology", "evidence_required_for_model_scores"),
                        payload={"item_index": index},
                    )
                )
        return tuple(dict.fromkeys(warnings)), tuple(structured_events), tuple(feature_records), tuple(event_reactions)

    def load_or_request_llm_envelope(
        self,
        raw_item: RawTextItem,
        event_input: EventNewsInput,
        job: ModuleJob,
    ) -> tuple[LlmEnvelope | None, tuple[str, ...]]:
        embedded = _embedded_llm_payload(raw_item)
        if embedded is not None:
            return self.validate_llm_output(embedded), ()

        request = self.create_llm_request(raw_item, event_input, job)
        if self.gateway is None:
            return None, ("llm_gateway_unavailable",)
        response = self._gateway_process(request)
        response_status = getattr(response, "status", "")
        response_errors = tuple(getattr(response, "errors", ()) or ())
        if response_status == "rate_limited" or "llm_throttled" in response_errors:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="warning",
                    event_type="llm_throttled",
                    message="PolzaAI LLM request was throttled; trading contour continues without this text item.",
                    object_type="external_request",
                    object_ref=f"request_logs.external_request:{request.request_id}",
                    reason_codes=("llm_throttled",),
                    payload={
                        "task_type": request.payload.get("task_type"),
                        "model_id": request.payload.get("model"),
                        "raw_text_item_id": raw_item.raw_text_item_id,
                    },
                )
            )
            return None, (f"external_request_created:{request.request_id}", "llm_throttled")
        payload = _llm_payload_from_response(response)
        if payload is None:
            return None, (f"external_request_created:{request.request_id}", "llm_response_empty")
        return self.validate_llm_output(payload), (f"external_request_created:{request.request_id}",)

    def validate_llm_output(self, payload: Mapping[str, Any] | str) -> LlmEnvelope:
        parsed = _parse_json_payload(payload)
        self._assert_no_forbidden_fields(parsed)
        envelope = LlmEnvelope.from_payload(parsed)
        for index, item in enumerate(envelope.items):
            self._assert_no_forbidden_fields(item)
            if str(item.get("event_type") or "") not in VALID_EVENT_TYPES:
                raise EventNewsIntelligenceError(f"LLM item {index} event_type is not in ontology")
            evidence = _string_tuple(item.get("evidence")) or envelope.evidence
            has_model_scores = any(
                key in item
                for key in (
                    "relevance_score",
                    "materiality_score",
                    "novelty_score",
                    "surprise_score",
                    "sentiment_score",
                    "confidence_score",
                )
            )
            if has_model_scores and not evidence:
                raise EventNewsIntelligenceError(f"LLM item {index} has model scores without evidence")
        return envelope

    def apply_event_ontology(
        self,
        *,
        item: Mapping[str, Any],
        envelope: LlmEnvelope,
        raw_item: RawTextItem,
        routing_messages: tuple[EventRoutingMessage, ...],
        profiles: tuple[InstrumentProfile, ...],
        event_input: EventNewsInput,
        job: ModuleJob,
        item_index: int,
    ) -> StructuredEvent:
        event_type = str(item.get("event_type") or "")
        if event_type not in VALID_EVENT_TYPES:
            raise EventNewsIntelligenceError(f"event_type_from_ontology_failed:{event_type}")
        evidence = self.extract_evidence(item, envelope)
        if not evidence:
            raise EventNewsIntelligenceError("evidence_required_for_model_scores")
        instrument_ids = self.extract_affected_instruments(
            item=item,
            envelope=envelope,
            raw_item=raw_item,
            routing_messages=routing_messages,
            profiles=profiles,
            event_type=event_type,
            requested_instrument_ids=event_input.instrument_ids,
        )
        if not instrument_ids:
            raise EventNewsIntelligenceError("affected_instruments_not_resolved")

        event_ts = _coerce_timestamp(item.get("event_ts") or raw_item.published_at or raw_item.fetched_at, job.time_range.to_ts)
        source_refs = tuple(
            dict.fromkeys(
                (
                    f"raw_text.raw_text_item:{raw_item.raw_text_item_id}",
                    *(
                        f"raw_text.event_routing_message:{message.routing_message_id}"
                        for message in routing_messages
                        if message.routing_message_id
                    ),
                    *_string_tuple(item.get("source_refs")),
                )
            )
        )
        scores = self._event_scores(item, envelope, raw_item, profiles, instrument_ids, event_type)
        confirmation_status = self._confirmation_status(raw_item, event_type)
        reason_codes = self.assign_reason_codes(item, envelope, scores=scores, event_type=event_type)
        if confirmation_status != "official_confirmed":
            reason_codes = tuple(dict.fromkeys((*reason_codes, confirmation_status)))
        event_payload = {
            "schema_version": envelope.schema_version,
            "task_type": envelope.task_type,
            "model_id": envelope.model_id,
            "llm_prompt_version": event_input.llm_prompt_version,
            "event_ontology_version": event_input.event_ontology_version,
            "source_type": raw_item.source_type or raw_item.source,
            "source": raw_item.source,
            "trust_level": raw_item.source_payload.get("trust_level"),
            "confirmation_status": confirmation_status,
            "official_confirmation_required": confirmation_status == "candidate_requires_official_confirmation",
            "content_hash": raw_item.content_hash,
            "item_index": item_index,
            "source_credibility_score": scores["source_credibility_score"],
            "issuer_relevance_score": scores["issuer_relevance_score"],
            "sector_relevance_score": scores["sector_relevance_score"],
            "calculation_version": CALCULATION_VERSION,
        }
        event_id = stable_record_id(
            "event",
            {
                "raw_text_item_id": raw_item.raw_text_item_id,
                "item_index": item_index,
                "event_type": event_type,
                "event_subtype": str(item.get("event_subtype") or ""),
                "event_ts": event_ts,
                "instrument_ids": instrument_ids,
                "evidence": evidence,
                "model_version": envelope.model_version,
            },
        )
        return StructuredEvent(
            event_id=event_id,
            instrument_ids=instrument_ids,
            event_type=event_type,
            event_subtype=str(item.get("event_subtype") or "unspecified"),
            event_ts=event_ts,
            detected_at=to_utc_iso(utc_now()),
            source_refs=source_refs,
            relevance_score=scores["relevance_score"],
            materiality_score=scores["materiality_score"],
            novelty_score=scores["novelty_score"],
            surprise_score=scores["surprise_score"],
            sentiment_score=scores["sentiment_score"],
            confidence_score=scores["confidence_score"],
            evidence=evidence,
            reason_codes=reason_codes,
            model_version=envelope.model_version,
            payload=event_payload,
        )

    def extract_affected_instruments(
        self,
        *,
        item: Mapping[str, Any],
        envelope: LlmEnvelope,
        raw_item: RawTextItem,
        routing_messages: tuple[EventRoutingMessage, ...],
        profiles: tuple[InstrumentProfile, ...],
        event_type: str,
        requested_instrument_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        requested = set(requested_instrument_ids)
        profile_ids = {profile.instrument_id for profile in profiles}
        candidates: list[str] = []
        for source in (
            _string_tuple(item.get("instrument_ids")),
            envelope.instrument_ids,
            raw_item.instrument_ids,
            *(message.instrument_ids for message in routing_messages),
        ):
            candidates.extend(str(instrument_id) for instrument_id in source)
        candidates.extend(self._alias_matched_instruments(raw_item, profiles))
        in_universe = tuple(dict.fromkeys(instrument_id for instrument_id in candidates if instrument_id in requested))
        out_of_universe = tuple(instrument_id for instrument_id in candidates if instrument_id and instrument_id not in requested)
        if out_of_universe and event_type not in MACRO_SECTOR_EVENT_TYPES:
            raise EventNewsIntelligenceError("news_outside_universe_without_macro_sector_classification")
        if in_universe:
            return in_universe
        if event_type in MACRO_SECTOR_EVENT_TYPES:
            return tuple(instrument_id for instrument_id in requested_instrument_ids if not profile_ids or instrument_id in profile_ids)
        return ()

    def compute_relevance_score(
        self,
        item: Mapping[str, Any],
        issuer_relevance_score: float,
        sector_relevance_score: float,
    ) -> float:
        explicit = clip(_optional_float(item.get("relevance_score")))
        if explicit is not None:
            return explicit
        return clip_required(max(issuer_relevance_score, sector_relevance_score))

    def compute_materiality_score(self, item: Mapping[str, Any], event_type: str) -> tuple[float, tuple[str, ...]]:
        explicit = clip(_optional_float(item.get("materiality_score")))
        if explicit is not None:
            return explicit, ()
        raise EventNewsIntelligenceError(f"news_materiality_score_required:{event_type}")

    def compute_novelty_score(self, item: Mapping[str, Any], raw_item: RawTextItem) -> tuple[float, tuple[str, ...]]:
        explicit = clip(_optional_float(item.get("novelty_score")))
        if explicit is not None:
            return explicit, ()
        max_similarity = _optional_float(item.get("max_similarity_to_recent_events"))
        if max_similarity is None:
            max_similarity = _optional_float(raw_item.source_payload.get("max_similarity_to_recent_events"))
        if max_similarity is not None:
            return clip_required(1.0 - clip_required(max_similarity)), ()
        raise EventNewsIntelligenceError("news_novelty_score_requires_score_or_recent_event_similarity")

    def compute_sentiment_score(self, item: Mapping[str, Any]) -> tuple[float, tuple[str, ...]]:
        explicit = signed_score(_optional_float(item.get("sentiment_score")))
        if explicit is not None:
            return explicit, ()
        raise EventNewsIntelligenceError("news_sentiment_score_required")

    def compute_surprise_score(self, item: Mapping[str, Any]) -> tuple[float, tuple[str, ...]]:
        explicit = clip(_optional_float(item.get("surprise_score")))
        if explicit is not None:
            return explicit, ()
        raise EventNewsIntelligenceError("news_surprise_score_required")

    def extract_evidence(self, item: Mapping[str, Any], envelope: LlmEnvelope) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*_string_tuple(item.get("evidence")), *envelope.evidence)))

    def assign_reason_codes(
        self,
        item: Mapping[str, Any],
        envelope: LlmEnvelope,
        *,
        scores: Mapping[str, float],
        event_type: str,
    ) -> tuple[str, ...]:
        reason_codes = [*_string_tuple(item.get("reason_codes")), *envelope.reason_codes]
        reason_codes.append(f"event_type:{event_type}")
        if scores["confidence_score"] < 0.5:
            reason_codes.append("low_event_confidence")
        return tuple(dict.fromkeys(reason_codes))

    def compute_market_reaction_if_available(
        self,
        *,
        item: Mapping[str, Any],
        raw_item: RawTextItem,
        event: StructuredEvent,
        event_input: EventNewsInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...] = (),
    ) -> tuple[EventReaction, ...]:
        reaction_payload = _reaction_payload(item, raw_item)
        if not reaction_payload:
            if event_input.market_reaction_window and self.gateway is not None:
                for request in self.create_market_reaction_requests(event, event_input, job, profiles):
                    self._gateway_process(request)
            return ()

        reactions: list[EventReaction] = []
        for window in event_input.market_reaction_window:
            window_payload = _window_reaction_payload(reaction_payload, window)
            if not window_payload:
                continue
            actual_return = _first_float(window_payload, "return_after_event", "actual_return", "instrument_return")
            market_return = _first_float(window_payload, "market_return_after_event", "market_return")
            reaction_return = _first_float(window_payload, "event_reaction", "reaction_return")
            if reaction_return is None:
                reaction_return = event_reaction(actual_return, market_return)
            expected_return = _first_float(window_payload, "expected_beta_adjusted_return", "expected_return", "beta_adjusted_return")
            abnormal_return = abnormal_return_after_event(actual_return, expected_return)
            abnormal_volume = _first_float(window_payload, "abnormal_volume", "abnormal_volume_after_event")
            if abnormal_volume is None:
                abnormal_volume = abnormal_volume_after_event(
                    _first_float(window_payload, "volume_after_event"),
                    _first_float(window_payload, "average_volume_same_window"),
                )
            for instrument_id in event.instrument_ids:
                reactions.append(
                    EventReaction(
                        event_id=event.event_id,
                        instrument_id=instrument_id,
                        horizon=window,
                        reaction_return=reaction_return,
                        market_adjusted_return=abnormal_return,
                        abnormal_volume=abnormal_volume,
                        calculated_at=to_utc_iso(utc_now()),
                        calculation_version=CALCULATION_VERSION,
                        payload={
                            "market_reaction_separate_from_sentiment": True,
                            "raw_window_payload": dict(window_payload),
                        },
                    )
                )
        return tuple(reactions)

    def compute_event_metric_values(
        self,
        *,
        item: Mapping[str, Any],
        envelope: LlmEnvelope,
        raw_item: RawTextItem,
        event: StructuredEvent,
        instrument_id: str,
        profiles: tuple[InstrumentProfile, ...],
        reactions: tuple[EventReaction, ...],
        event_input: EventNewsInput,
        job: ModuleJob,
    ) -> tuple[MetricValue, ...]:
        del event_input
        profile = next((profile for profile in profiles if profile.instrument_id == instrument_id), None)
        source_ref = f"raw_text.raw_text_item:{raw_item.raw_text_item_id}"
        event_ref = f"events.structured_event:{event.event_id}"
        source_refs = tuple(dict.fromkeys((source_ref, event_ref, *event.source_refs)))
        scores = self._event_scores(item, envelope, raw_item, profiles, event.instrument_ids, event.event_type)
        ttl_seconds = self.ttl_seconds_for_event(event.event_type)
        half_life_seconds = max(60, ttl_seconds // 2)
        decay = event_decay_score(event.event_ts, job.time_range.to_ts, half_life_seconds)
        pressure = weighted_average(
            {
                "news_sentiment_score": event.sentiment_score,
                "news_materiality_score": event.materiality_score,
                "news_novelty_score": event.novelty_score,
                "event_decay_score": decay,
            },
            self.event_pressure_weights,
        )
        metric_values: list[MetricValue] = []
        quality_flags = tuple(dict.fromkeys(scores["quality_flags"]))
        self._append_metric(metric_values, "news_sentiment_score", "model_score", event.sentiment_score, normalize_sentiment(event.sentiment_score), "score_-1_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"criteria": "LLM/classifier score in [-1,1] with evidence and reason codes"})
        self._append_metric(metric_values, "news_materiality_score", "model_score", event.materiality_score, event.materiality_score, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"criteria": "direct financial impact, regulatory impact, issuer relevance, surprise"})
        self._append_metric(metric_values, "news_novelty_score", "model_score", event.novelty_score, event.novelty_score, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "1 - max_similarity_to_recent_events from validated LLM/classifier context"})
        self._append_metric(metric_values, "news_surprise_score", "model_score", event.surprise_score, event.surprise_score, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"criteria": "vs known expectations/history; requires evidence"})
        self._append_metric(metric_values, "source_credibility_score", "raw_metric", scores["source_credibility_score"], scores["source_credibility_score"], "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"source": raw_item.source_type or raw_item.source})
        self._append_metric(metric_values, "issuer_relevance_score", "derived_metric", scores["issuer_relevance_score"], scores["issuer_relevance_score"], "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"instrument_id": instrument_id, "ticker": profile.ticker if profile else ""})
        self._append_metric(metric_values, "sector_relevance_score", "derived_metric", scores["sector_relevance_score"], scores["sector_relevance_score"], "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"sector": profile.sector if profile else None})
        self._append_metric(metric_values, "event_confidence", "composite_score", event.confidence_score, event.confidence_score, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "WAvg([source_credibility_score, issuer_relevance_score, extraction_confidence], [0.3,0.4,0.3])"})
        self._append_metric(metric_values, "event_decay_score", "derived_metric", decay, decay, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "exp(-age_seconds / half_life_seconds)", "half_life_seconds": half_life_seconds})

        reaction_by_window = {reaction.horizon: reaction for reaction in reactions if reaction.instrument_id == instrument_id}
        for window, metric_name in (("5m", "event_reaction_5m"), ("1h", "event_reaction_1h"), ("1d", "event_reaction_1d")):
            reaction = reaction_by_window.get(window)
            if reaction is None or reaction.reaction_return is None:
                continue
            self._append_metric(metric_values, metric_name, "derived_metric", reaction.reaction_return, None, "return", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": f"return_{window}_after_event - market_return_{window}_after_event"})
        best_reaction = _select_reaction(reaction_by_window)
        if best_reaction is not None:
            if best_reaction.market_adjusted_return is not None:
                self._append_metric(metric_values, "abnormal_return_after_event", "derived_metric", best_reaction.market_adjusted_return, None, "return", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "actual return after event minus expected beta-adjusted return", "reaction_horizon": best_reaction.horizon})
            if best_reaction.abnormal_volume is not None:
                self._append_metric(metric_values, "abnormal_volume_after_event", "derived_metric", best_reaction.abnormal_volume, None, "ratio_delta", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "volume_after_event / average_volume_same_window - 1", "reaction_horizon": best_reaction.horizon})
            reaction_z = _reaction_z(item, best_reaction)
            underreaction = underreaction_score(event.sentiment_score, event.materiality_score, reaction_z)
            overreaction = overreaction_score(reaction_z, event.materiality_score)
            self._append_metric(metric_values, "underreaction_score", "derived_metric", underreaction, underreaction, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "positive_event_strength * max(0, 1 - abs(event_reaction_z))", "reaction_horizon": best_reaction.horizon})
            self._append_metric(metric_values, "overreaction_score", "derived_metric", overreaction, overreaction, "score_0_1", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "abs(event_reaction_z) * (1 - materiality_score) clipped 0..1", "reaction_horizon": best_reaction.horizon})

        if pressure is not None:
            self._append_metric(metric_values, "event_pressure_score", "composite_score", pressure, normalize_sentiment(pressure), "score", ttl_seconds, source_refs, quality_flags, event.confidence_score, {"formula": "WAvg([news_sentiment_score, news_materiality_score, news_novelty_score, event_decay_score], service_config_active_weights)", "component_values": {"news_sentiment_score": event.sentiment_score, "news_materiality_score": event.materiality_score, "news_novelty_score": event.novelty_score, "event_decay_score": decay}, "weight_source": "service_config_default_or_injected"})
        return tuple(metric_values)

    def build_feature_record(
        self,
        *,
        event: StructuredEvent,
        metric_value: MetricValue,
        instrument_id: str,
        horizon: str,
        contour: str,
    ) -> FeatureRecord:
        feature_id = stable_record_id(
            "feature",
            {
                "event_id": event.event_id,
                "instrument_id": instrument_id,
                "metric_name": metric_value.metric_name,
                "horizon": horizon,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        return FeatureRecord(
            feature_id=feature_id,
            instrument_id=instrument_id,
            metric_name=metric_value.metric_name,
            metric_group="event",
            metric_type=metric_value.metric_type,
            raw_value=float(metric_value.raw_value),
            normalized_value=metric_value.normalized_value,
            unit=metric_value.unit,
            horizon=horizon,
            contour=contour,
            timestamp=event.event_ts,
            ttl_seconds=metric_value.ttl_seconds,
            confidence_score=clip_required(metric_value.confidence_score),
            source_module=self.module_name,
            source_refs=metric_value.source_refs,
            calculation_version=CALCULATION_VERSION,
            quality_flags=metric_value.quality_flags,
            payload={
                **dict(metric_value.payload),
                "event_id": event.event_id,
                "event_type": event.event_type,
                "event_subtype": event.event_subtype,
                "model_version": event.model_version,
                "evidence": list(event.evidence),
                "reason_codes": list(event.reason_codes),
                "calculation_version": CALCULATION_VERSION,
            },
        )

    def create_llm_request(
        self,
        raw_item: RawTextItem,
        event_input: EventNewsInput,
        job: ModuleJob,
    ) -> ExternalRequest:
        text_payload = {
            "raw_text_item_id": raw_item.raw_text_item_id,
            "source": raw_item.source,
            "source_type": raw_item.source_type,
            "source_url": raw_item.source_url,
            "title": raw_item.title,
            "body": raw_item.body,
            "published_at": raw_item.published_at,
            "instrument_ids": list(raw_item.instrument_ids or event_input.instrument_ids),
            "event_ontology_version": event_input.event_ontology_version,
        }
        task_type = self.llm_task_type(raw_item)
        content_hash = raw_item.content_hash or stable_record_id(
            "raw_text_content",
            {
                "raw_text_item_id": raw_item.raw_text_item_id,
                "title": raw_item.title,
                "body": raw_item.body,
                "source_url": raw_item.source_url,
            },
        )
        model_id = self.model_id or polza_model_for_task(task_type)
        system_prompt = REASONING_SYSTEM_PROMPT if task_type in REASONING_LLM_TASK_TYPES else EVENT_EXTRACTION_SYSTEM_PROMPT
        prompt_payload = {
            "task": task_type,
            "prompt_version": event_input.llm_prompt_version,
            "module_contract": {
                "module_name": self.module_name,
                "event_ontology_version": event_input.event_ontology_version,
                "llm_prompt_version": event_input.llm_prompt_version,
                "allowed_event_types": sorted(VALID_EVENT_TYPES),
                "requested_instrument_ids": list(event_input.instrument_ids),
                "market_reaction_is_separate": True,
                "do_not_output_trading_recommendations": True,
            },
            "output_schema": LLM_OUTPUT_SCHEMA_DESCRIPTION,
            "scoring_guide": LLM_SCORING_GUIDE,
            "acceptance_rules": [
                "Return a single JSON object and no text outside JSON.",
                "Use only event_type values from allowed_event_types.",
                "Each item with model scores must include non-empty evidence and reason_codes.",
                "Do not infer affected instruments outside requested_instrument_ids unless event_type is macro, sector, or market_structure.",
                "Do not include order_intent, decision_action, target_quantity, target_position_pct, or trading recommendation fields.",
                "If the text has no material event, return items as an empty list with a warning.",
            ],
            "input": text_payload,
        }
        idempotency_key = ":".join(
            (
                job.idempotency_key,
                "llm_completion",
                raw_item.raw_text_item_id,
                content_hash,
                task_type,
                event_input.llm_prompt_version,
                event_input.event_ontology_version,
                model_id,
            )
        )
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="polza_ai",
            request_type="llm_completion",
            universe_id=job.universe_id,
            instrument_ids=event_input.instrument_ids,
            payload={
                "model": model_id,
                "model_id": model_id,
                "task_type": task_type,
                "prompt_version": event_input.llm_prompt_version,
                "llm_prompt_version": event_input.llm_prompt_version,
                "event_ontology_version": event_input.event_ontology_version,
                "content_hash": content_hash,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True),
                    },
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 2400 if task_type in REASONING_LLM_TASK_TYPES else 1000,
                "reasoning": {"enabled": task_type in REASONING_LLM_TASK_TYPES, "effort": "medium", "summary": "auto"},
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=3600, write_cache=True),
            timeout_ms=10000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=500),
            idempotency_key=idempotency_key,
        )

    def llm_task_type(self, raw_item: RawTextItem) -> str:
        explicit = str(raw_item.source_payload.get("llm_task_type") or raw_item.source_payload.get("task_type") or "").strip()
        if explicit in VALID_TASK_TYPES:
            return explicit
        text = " ".join(
            str(value or "")
            for value in (raw_item.source_type, raw_item.source, raw_item.title, raw_item.source_url)
        ).lower()
        text_with_body = f"{text} {(raw_item.body or '')[:1500].lower()}"
        if any(marker in text_with_body for marker in (
            "отчет",
            "отчёт",
            "мсфо",
            "рсбу",
            "финансовые результаты",
            "операционные результаты",
            "существенный факт",
            "собрание акционеров",
            "совет директоров",
        )):
            return "report_extraction"
        if any(marker in text_with_body for marker in ("дивиденд", "дивиденды")) and len(raw_item.body or "") > 3000:
            return "dividend_extraction"
        if any(marker in text_with_body for marker in ("ключев", "руониа", "цб", "банк россии")):
            return "macro_text_analysis"
        russian_report_markers = (
            "отчет",
            "отчёт",
            "мсфо",
            "рсбу",
            "финансовые результаты",
            "операционные результаты",
            "существенный факт",
            "собрание акционеров",
            "совет директоров",
        )
        if any(marker in text for marker in russian_report_markers):
            return "report_extraction"
        if any(marker in text for marker in ("дивиденд", "дивиденды")) and len(raw_item.body or "") > 3000:
            return "dividend_extraction"
        if any(marker in text for marker in ("ключев", "руониа")):
            return "macro_text_analysis"
        if any(marker in text for marker in ("report", "отчет", "отчёт", "ifrs", "rsbu", "msfo", "мсфо", "financial")):
            return "report_extraction"
        if any(marker in text for marker in ("dividend", "дивиденд")) and len(raw_item.body or "") > 3000:
            return "dividend_extraction"
        if any(marker in text for marker in ("macro", "cbr", "ключев", "ruonia", "zc yc", "zcyc")):
            return "macro_text_analysis"
        return "event_extraction"

    def create_market_reaction_requests(
        self,
        event: StructuredEvent,
        event_input: EventNewsInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...] = (),
    ) -> tuple[ExternalRequest, ...]:
        profile_by_id = {profile.instrument_id: profile for profile in profiles}
        return tuple(
            self.create_market_reaction_request(
                event,
                event_input,
                job,
                instrument_id=instrument_id,
                profile=profile_by_id.get(instrument_id),
            )
            for instrument_id in event.instrument_ids
        )

    def create_market_reaction_request(
        self,
        event: StructuredEvent,
        event_input: EventNewsInput,
        job: ModuleJob,
        *,
        instrument_id: str | None = None,
        profile: InstrumentProfile | None = None,
    ) -> ExternalRequest:
        target_instrument_id = instrument_id or (event.instrument_ids[0] if event.instrument_ids else "")
        board_id = str(
            (profile.board_id if profile is not None else "")
            or (profile.metadata.get("board_id") if profile is not None else "")
            or "TQBR"
        )
        secid = (profile.ticker if profile is not None and profile.ticker else _strip_moex_prefix(target_instrument_id))
        idempotency_key = f"{job.idempotency_key}:market_reaction:{event.event_id}:{target_instrument_id}:{board_id}:1d"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="market_data",
            universe_id=job.universe_id,
            instrument_ids=(target_instrument_id,) if target_instrument_id else (),
            payload={
                "event_id": event.event_id,
                "event_ts": event.event_ts,
                "secid": secid,
                "board_id": board_id,
                "timeframe": "1d",
                "timeframes": ["1d"],
                "market_reaction_window": list(event_input.market_reaction_window),
                "time_range": job.time_range.to_dict(),
                "purpose": "compute_market_reaction_if_available",
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=300, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=1, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def expected_horizons(
        self,
        item: Mapping[str, Any],
        event_type: str,
        job_horizons: tuple[str, ...],
    ) -> tuple[str, ...]:
        explicit = tuple(horizon for horizon in _string_tuple(item.get("expected_horizons")) if horizon in VALID_HORIZONS)
        candidates = explicit
        if not candidates:
            if event_type in {"market_structure", "other"}:
                candidates = ("intraday", "swing")
            elif event_type in {"earnings", "dividend", "corporate_action"}:
                candidates = ("swing", "position")
            elif event_type in {"macro", "regulation", "sanctions", "management", "sector"}:
                candidates = ("intraday", "swing", "position")
            else:
                candidates = job_horizons
        selected = tuple(horizon for horizon in job_horizons if horizon in set(candidates))
        return selected or job_horizons

    def ttl_seconds_for_event(self, event_type: str) -> int:
        return BASE_EVENT_TTL_SECONDS.get(event_type, BASE_EVENT_TTL_SECONDS["other"])

    def write_structured_event(self, event: StructuredEvent) -> str:
        return self.repository.save_structured_event(event)

    def write_event_reaction(self, reaction: EventReaction) -> str:
        return self.repository.save_event_reaction(reaction)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

    def _event_scores(
        self,
        item: Mapping[str, Any],
        envelope: LlmEnvelope,
        raw_item: RawTextItem,
        profiles: tuple[InstrumentProfile, ...],
        instrument_ids: tuple[str, ...],
        event_type: str,
    ) -> Mapping[str, Any]:
        source_credibility = self._source_credibility(raw_item)
        issuer_relevance = self._issuer_relevance(raw_item, profiles, instrument_ids)
        sector_relevance = self._sector_relevance(raw_item, profiles, instrument_ids, event_type)
        relevance = self.compute_relevance_score(item, issuer_relevance, sector_relevance)
        materiality, materiality_flags = self.compute_materiality_score(item, event_type)
        novelty, novelty_flags = self.compute_novelty_score(item, raw_item)
        sentiment, sentiment_flags = self.compute_sentiment_score(item)
        surprise, surprise_flags = self.compute_surprise_score(item)
        extraction_confidence = clip(_optional_float(item.get("confidence_score")))
        if extraction_confidence is None:
            extraction_confidence = envelope.confidence_score
        confidence = event_confidence_score(source_credibility, issuer_relevance, extraction_confidence)
        quality_flags = tuple(dict.fromkeys((*materiality_flags, *novelty_flags, *sentiment_flags, *surprise_flags)))
        return {
            "source_credibility_score": source_credibility,
            "issuer_relevance_score": issuer_relevance,
            "sector_relevance_score": sector_relevance,
            "extraction_confidence": extraction_confidence,
            "relevance_score": relevance,
            "materiality_score": materiality,
            "novelty_score": novelty,
            "surprise_score": surprise,
            "sentiment_score": sentiment,
            "confidence_score": confidence,
            "quality_flags": quality_flags,
        }

    def _source_credibility(self, raw_item: RawTextItem) -> float:
        explicit = clip(_optional_float(raw_item.source_payload.get("source_credibility_score")))
        if explicit is not None:
            return explicit
        source_type = (raw_item.source_type or raw_item.source or "").lower()
        return SOURCE_CREDIBILITY_DEFAULTS.get(source_type, 0.6)

    def _confirmation_status(self, raw_item: RawTextItem, event_type: str) -> str:
        explicit = str(raw_item.source_payload.get("confirmation_status") or "")
        if explicit == "official_confirmed":
            return explicit
        source_type = (raw_item.source_type or raw_item.source or "").lower()
        if event_type not in OFFICIAL_CONFIRMATION_REQUIRED_EVENT_TYPES:
            return "official_confirmed" if source_type in OFFICIAL_CONFIRMATION_SOURCE_TYPES else "candidate_early_signal"
        if source_type in OFFICIAL_CONFIRMATION_SOURCE_TYPES:
            return "official_confirmed"
        return "candidate_requires_official_confirmation"

    def _issuer_relevance(
        self,
        raw_item: RawTextItem,
        profiles: tuple[InstrumentProfile, ...],
        instrument_ids: tuple[str, ...],
    ) -> float:
        if set(instrument_ids) & set(raw_item.instrument_ids):
            return 1.0
        matches = self._alias_matched_instruments(raw_item, profiles)
        if set(matches) & set(instrument_ids):
            return 1.0
        if instrument_ids:
            return 0.5
        return 0.0

    def _sector_relevance(
        self,
        raw_item: RawTextItem,
        profiles: tuple[InstrumentProfile, ...],
        instrument_ids: tuple[str, ...],
        event_type: str,
    ) -> float:
        text = _combined_text(raw_item).lower()
        if event_type in MACRO_SECTOR_EVENT_TYPES and instrument_ids:
            sectors = {
                str(profile.sector).lower()
                for profile in profiles
                if profile.instrument_id in set(instrument_ids) and profile.sector
            }
            if any(sector and sector in text for sector in sectors):
                return 1.0
            return 0.6
        return 0.0

    def _alias_matched_instruments(
        self,
        raw_item: RawTextItem,
        profiles: tuple[InstrumentProfile, ...],
    ) -> tuple[str, ...]:
        text = _combined_text(raw_item).lower()
        matches: list[str] = []
        for profile in profiles:
            aliases = (
                profile.instrument_id,
                profile.ticker,
                profile.issuer_name or "",
                *profile.aliases,
                *profile.related_entities,
            )
            if any(alias and str(alias).lower() in text for alias in aliases):
                matches.append(profile.instrument_id)
        return tuple(dict.fromkeys(matches))

    def _append_metric(
        self,
        metrics: list[MetricValue],
        metric_name: str,
        metric_type: str,
        raw_value: float | None,
        normalized_value: float | None,
        unit: str,
        ttl_seconds: int,
        source_refs: tuple[str, ...],
        quality_flags: tuple[str, ...],
        confidence_score: float,
        payload: Mapping[str, Any],
    ) -> None:
        if raw_value is None:
            return
        metrics.append(
            MetricValue(
                metric_name=metric_name,
                metric_type=metric_type,
                raw_value=float(raw_value),
                normalized_value=None if normalized_value is None else float(normalized_value),
                unit=unit,
                ttl_seconds=ttl_seconds,
                source_refs=source_refs,
                quality_flags=quality_flags,
                confidence_score=confidence_score,
                payload={"calculation_version": CALCULATION_VERSION, **dict(payload)},
            )
        )

    def _gateway_process(self, request: ExternalRequest) -> Any:
        if hasattr(self.gateway, "process"):
            return self.gateway.process(request)
        if hasattr(self.gateway, "execute"):
            result = self.gateway.execute(request)
            return getattr(result, "response", result)
        if callable(self.gateway):
            return self.gateway(request)
        raise EventNewsIntelligenceError("gateway does not expose process/execute")

    def _assert_no_forbidden_fields(self, payload: Mapping[str, Any]) -> None:
        for key, value in payload.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_LLM_FIELDS:
                raise EventNewsIntelligenceError(f"forbidden LLM field: {key}")
            if isinstance(value, Mapping):
                self._assert_no_forbidden_fields(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        self._assert_no_forbidden_fields(item)

    def _module_job_result(
        self,
        *,
        job: ModuleJob,
        started_at: str,
        status: str,
        output_refs: tuple[str, ...],
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        metrics_written: int,
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
            metrics_written=metrics_written,
            events_written=events_written,
            data_quality_score=1.0 if not warnings and not errors else 0.75 if output_refs else 0.0,
        )

    def _empty_result(
        self,
        job: ModuleJob,
        started_at: str,
        status: str,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
    ) -> EventNewsExecutionResult:
        return EventNewsExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status=status,
                output_refs=(),
                warnings=warnings,
                errors=errors,
                metrics_written=0,
                events_written=0,
            ),
            structured_events=(),
            feature_records=(),
            event_reactions=(),
            structured_event_refs=(),
            feature_record_refs=(),
            event_reaction_refs=(),
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> EventNewsExecutionResult:
        self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="module_execution_failed",
                message=str(error),
                reason_codes=("event_news_module_failed",),
            )
        )
        return self._empty_result(job, started_at, "failed", (), (str(error),))


def _embedded_llm_payload(raw_item: RawTextItem) -> Mapping[str, Any] | str | None:
    payload = raw_item.source_payload
    for key in ("llm_output", "llm_envelope", "event_extraction", "polza_ai_response", "llm_response"):
        value = payload.get(key)
        if value:
            extracted = _maybe_nested_llm_payload(value)
            if extracted is not None:
                return extracted
    if all(key in payload for key in ("schema_version", "model_id", "model_version", "task_type")):
        return payload
    return None


def polza_model_for_task(task_type: str, env: Mapping[str, str] | None = None) -> str:
    env_map = env if env is not None else os.environ
    fast_model = _safe_polza_model(env_map.get("POLZA_FAST_MODEL"), DEFAULT_FAST_MODEL_ID)
    reasoning_model = _safe_polza_model(env_map.get("POLZA_REASONING_MODEL"), DEFAULT_REASONING_MODEL_ID)
    default_model = _safe_polza_model(env_map.get("POLZA_DEFAULT_MODEL"), DEFAULT_MODEL_ID)
    if task_type in FAST_LLM_TASK_TYPES:
        return fast_model
    if task_type in REASONING_LLM_TASK_TYPES:
        return reasoning_model
    return default_model


def _safe_polza_model(model_id: str | None, fallback: str) -> str:
    candidate = str(model_id or "").strip()
    if not candidate or candidate in PROHIBITED_POLZA_MODELS:
        return fallback
    return candidate


def _maybe_nested_llm_payload(value: Any) -> Mapping[str, Any] | str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return None
    if all(key in value for key in ("schema_version", "model_id", "model_version", "task_type")):
        return value
    if "data" in value:
        return _maybe_nested_llm_payload(value["data"])
    if "content" in value:
        return _maybe_nested_llm_payload(value["content"])
    if "output" in value:
        return _maybe_nested_llm_payload(value["output"])
    return None


def _llm_payload_from_response(response: Any) -> Mapping[str, Any] | str | None:
    if response is None:
        return None
    if hasattr(response, "data"):
        data = response.data
    elif hasattr(response, "response") and hasattr(response.response, "data"):
        data = response.response.data
    elif isinstance(response, Mapping):
        nested = response.get("external_response") if isinstance(response.get("external_response"), Mapping) else response
        data = nested.get("data") if isinstance(nested, Mapping) else None
    else:
        return None
    if data is None:
        return None
    if isinstance(data, str):
        return data
    if not isinstance(data, Mapping):
        return None
    if all(key in data for key in ("schema_version", "model_id", "model_version", "task_type")):
        return data
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping) and message.get("content"):
                return message["content"]
    for key in ("llm_output", "content", "output", "result"):
        value = data.get(key)
        if value:
            return _maybe_nested_llm_payload(value)
    return None


def _parse_json_payload(payload: Mapping[str, Any] | str) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        raise EventNewsIntelligenceError("LLM output is not valid JSON") from error
    if not isinstance(parsed, Mapping):
        raise EventNewsIntelligenceError("LLM output JSON root must be an object")
    return parsed


def _reaction_payload(item: Mapping[str, Any], raw_item: RawTextItem) -> Mapping[str, Any]:
    for candidate in (
        item.get("market_reaction_data"),
        item.get("market_reaction"),
        raw_item.source_payload.get("market_reaction_data"),
        raw_item.source_payload.get("market_reaction"),
    ):
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _window_reaction_payload(payload: Mapping[str, Any], window: str) -> Mapping[str, Any]:
    direct = payload.get(window)
    if isinstance(direct, Mapping):
        return direct
    suffix = window.replace("m", "m").replace("h", "h").replace("d", "d")
    values = {}
    for key, value in payload.items():
        key_text = str(key)
        for ending in (f"_{suffix}", window):
            if key_text.endswith(ending):
                normalized_key = key_text[: -len(ending)].rstrip("_") or key_text
                values[normalized_key] = value
                break
    return values


def _select_reaction(reaction_by_window: Mapping[str, EventReaction]) -> EventReaction | None:
    for window in ("1h", "5m", "1d"):
        if window in reaction_by_window:
            return reaction_by_window[window]
    return next(iter(reaction_by_window.values()), None)


def _reaction_z(item: Mapping[str, Any], reaction: EventReaction) -> float | None:
    explicit = _optional_float(item.get("event_reaction_z"))
    if explicit is not None:
        return explicit
    payload_value = _optional_float(reaction.payload.get("event_reaction_z"))
    if payload_value is not None:
        return payload_value
    historical_std = _first_float(reaction.payload.get("raw_window_payload", {}), "event_reaction_std", "reaction_std")
    if historical_std in (None, 0) or reaction.reaction_return is None:
        return None
    return reaction.reaction_return / historical_std


def _refs_with_prefix(refs: tuple[str, ...], prefix: str) -> tuple[str, ...]:
    return tuple(ref for ref in refs if str(ref).startswith(prefix))


def _coerce_timestamp(value: Any, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    text = str(value)
    try:
        parse_utc_iso(text)
    except Exception:
        return fallback
    return text


def _combined_text(raw_item: RawTextItem) -> str:
    return " ".join(
        value
        for value in (
            raw_item.title or "",
            raw_item.body or "",
            raw_item.source_url or "",
            json.dumps(raw_item.source_payload, ensure_ascii=False, sort_keys=True, default=str),
        )
        if value
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        iterator = iter(value)
    except TypeError:
        return (str(value),)
    return tuple(str(item) for item in iterator if item not in (None, ""))


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(payload: Any, *keys: str) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    for key in keys:
        value = _optional_float(payload.get(key))
        if value is not None:
            return value
    return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
