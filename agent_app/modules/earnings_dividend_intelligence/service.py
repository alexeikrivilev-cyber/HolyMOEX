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
    clip,
    clip_required,
    days_to_record_date,
    dividend_carry_score,
    dividend_surprise,
    expected_dividend_yield,
    expected_gap_risk,
    gap_close_probability_20d,
    gap_close_speed_median,
    historical_gap_size,
    margin_surprise,
    parse_date,
    payout_ratio,
    signed_to_unit,
    surprise,
    weighted_average,
    z_to_unit,
)
from .repository import (
    EarningsDividendIntelligenceRepository,
    EarningsDividendRecord,
    FeatureRecord,
    FinancialExpectation,
    HistoricalGapRecord,
    InMemoryEarningsDividendIntelligenceRepository,
    RawCandle,
    RawTextItem,
    StructuredEvent,
    stable_record_id,
    stable_uuid_id,
)


MODULE_NAME = "Earnings & Dividend Intelligence Module"
CALCULATION_VERSION = "earnings_dividend_intelligence_v1"
DEFAULT_MODEL_ID = "qwen/qwen3.6-35b-a3b"
PROHIBITED_POLZA_MODELS = {"deepseek/" + "deepseek-v4" + "-pro"}

VALID_CONTOURS = {"event_contour", "daily_contour"}
VALID_HORIZONS = {"swing", "position"}
VALID_TASK_TYPES = {"report_extraction", "dividend_extraction"}
INPUT_FIELDS = {
    "instrument_ids",
    "report_refs",
    "dividend_event_refs",
    "financial_expectation_ref",
    "historical_gap_ref",
    "llm_prompt_version",
}

EARNINGS_TTL_SECONDS = 90 * 24 * 60 * 60
DIVIDEND_DEFAULT_TTL_SECONDS = 45 * 24 * 60 * 60
MARKET_PRICE_TTL_SECONDS = 24 * 60 * 60

SURPRISE_METRICS = {
    "revenue_surprise": ("revenue_actual", "revenue_expected", "ratio"),
    "ebitda_surprise": ("ebitda_actual", "ebitda_expected", "ratio"),
    "net_income_surprise": ("net_income_actual", "net_income_expected", "ratio"),
    "fcf_surprise": ("fcf_actual", "fcf_expected", "ratio"),
    "debt_surprise": ("net_debt_actual", "net_debt_expected", "ratio"),
    "capex_surprise": ("capex_actual", "capex_expected", "ratio"),
}
ALL_METRIC_NAMES = (
    "revenue_surprise",
    "ebitda_surprise",
    "net_income_surprise",
    "fcf_surprise",
    "margin_surprise",
    "debt_surprise",
    "capex_surprise",
    "earnings_quality_score",
    "report_materiality_score",
    "report_sentiment_score",
    "expected_dividend_yield",
    "dividend_probability",
    "dividend_surprise",
    "days_to_record_date",
    "historical_gap_size",
    "expected_gap_risk",
    "gap_close_probability_20d",
    "gap_close_speed_median",
    "payout_ratio",
    "dividend_sustainability_score",
    "dividend_carry_score",
)

DEFAULT_EARNINGS_QUALITY_WEIGHTS = {
    "fcf_conversion_z": 1.0,
    "margin_quality_z": 1.0,
    "negative_one_off_items_ratio_z": 1.0,
    "accruals_quality_z": 1.0,
}
DEFAULT_REPORT_MATERIALITY_WEIGHTS = {
    "abs_revenue_surprise": 1.0,
    "abs_ebitda_surprise": 1.0,
    "abs_net_income_surprise": 1.0,
    "guidance_change_score": 1.0,
}
DEFAULT_DIVIDEND_SUSTAINABILITY_WEIGHTS = {
    "payout_safety_z": 1.0,
    "fcf_coverage_z": 1.0,
    "negative_leverage_z": 1.0,
    "dividend_policy_confidence": 1.0,
}

FORBIDDEN_LLM_FIELDS = {
    "trading_recommendation",
    "trade_recommendation",
    "recommendation",
    "decision_action",
    "order_intent",
    "target_position_pct",
    "target_quantity",
    "order_side",
    "submit_order",
    "portfolio_change",
}

REPORT_EXTRACTION_SYSTEM_PROMPT = (
    "You extract earnings and dividend facts for a MOEX analytical module. "
    "Return only strict JSON in the requested envelope. Separate facts from scores. "
    "Do not include trading advice, order instructions, target positions, portfolio changes, "
    "risk-policy changes, or free-form prose. Every extracted item must include evidence "
    "and reason_codes grounded in the supplied text."
)
LLM_OUTPUT_SCHEMA_DESCRIPTION = {
    "schema_version": "string",
    "model_id": "string",
    "model_version": "string",
    "task_type": "report_extraction | dividend_extraction",
    "instrument_ids": ["string"],
    "items": [
        {
            "item_type": "earnings | dividend",
            "instrument_id": "string",
            "period": "string",
            "event_ts": "UTC ISO-8601 string",
            "financial_facts": {
                "revenue_actual": "number | null",
                "ebitda_actual": "number | null",
                "net_income_actual": "number | null",
                "fcf_actual": "number | null",
                "margin_actual": "number | null",
                "net_debt_actual": "number | null",
                "capex_actual": "number | null",
            },
            "dividend_terms": {
                "expected_dividend_per_share": "number | null",
                "announced_dividend": "number | null",
                "record_date": "YYYY-MM-DD | null",
                "ex_dividend_date": "YYYY-MM-DD | null",
                "status": "policy_expected | recommended | approved | paid | cancelled | unknown",
            },
            "scores": {
                "report_sentiment_score": "number -1..1 | null",
                "dividend_probability": "number 0..1 | null",
            },
            "evidence": ["short source-grounded text spans or facts"],
            "reason_codes": ["snake_case strings"],
        }
    ],
    "confidence_score": "number 0..1",
    "evidence": ["string"],
    "reason_codes": ["snake_case strings"],
    "warnings": ["string"],
}


class EarningsDividendIntelligenceError(ValueError):
    """Raised when module 12 violates its documented contract."""


@dataclass(frozen=True)
class EarningsDividendInput:
    instrument_ids: tuple[str, ...]
    report_refs: tuple[str, ...]
    dividend_event_refs: tuple[str, ...]
    financial_expectation_ref: str
    historical_gap_ref: str
    llm_prompt_version: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "EarningsDividendInput":
        input_payload = payload.get("earnings_dividend_input")
        if not isinstance(input_payload, Mapping):
            raise EarningsDividendIntelligenceError("payload must contain earnings_dividend_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise EarningsDividendIntelligenceError(f"earnings_dividend_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise EarningsDividendIntelligenceError(f"earnings_dividend_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise EarningsDividendIntelligenceError("earnings_dividend_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise EarningsDividendIntelligenceError("earnings_dividend_input.instrument_ids must match module_job.instrument_ids")

        llm_prompt_version = str(input_payload.get("llm_prompt_version") or "")
        if not llm_prompt_version:
            raise EarningsDividendIntelligenceError("earnings_dividend_input.llm_prompt_version is required")

        return cls(
            instrument_ids=instrument_ids,
            report_refs=tuple(str(item) for item in (input_payload.get("report_refs") or ())),
            dividend_event_refs=tuple(str(item) for item in (input_payload.get("dividend_event_refs") or ())),
            financial_expectation_ref=str(input_payload.get("financial_expectation_ref") or ""),
            historical_gap_ref=str(input_payload.get("historical_gap_ref") or ""),
            llm_prompt_version=llm_prompt_version,
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
            raise EarningsDividendIntelligenceError(f"LLM output missing required fields: {missing}")
        task_type = str(payload.get("task_type") or "")
        if task_type not in VALID_TASK_TYPES:
            raise EarningsDividendIntelligenceError(f"invalid LLM task_type: {task_type}")
        items = payload.get("items")
        if not isinstance(items, list):
            raise EarningsDividendIntelligenceError("LLM output items must be a list")
        if any(not isinstance(item, Mapping) for item in items):
            raise EarningsDividendIntelligenceError("LLM output items must contain only JSON objects")
        evidence = _string_tuple(payload.get("evidence"))
        reason_codes = _string_tuple(payload.get("reason_codes"))
        if items and (not evidence or not reason_codes):
            raise EarningsDividendIntelligenceError("LLM output with items requires evidence and reason_codes")
        confidence_score = _optional_float(payload.get("confidence_score"))
        if confidence_score is None:
            raise EarningsDividendIntelligenceError("LLM output confidence_score must be numeric")
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            model_id=str(payload.get("model_id") or ""),
            model_version=str(payload.get("model_version") or ""),
            task_type=task_type,
            instrument_ids=_string_tuple(payload.get("instrument_ids")),
            items=tuple(items),
            confidence_score=clip_required(confidence_score),
            evidence=evidence,
            reason_codes=reason_codes,
            warnings=_string_tuple(payload.get("warnings")),
        )


@dataclass(frozen=True)
class FinancialFactSet:
    instrument_id: str
    period: str
    fields: Mapping[str, Any]
    source_refs: tuple[str, ...]
    as_of_ts: str
    confidence_score: float
    evidence: tuple[str, ...]
    reason_codes: tuple[str, ...]
    model_version: str


@dataclass(frozen=True)
class DividendFactSet:
    instrument_id: str
    fields: Mapping[str, Any]
    source_refs: tuple[str, ...]
    as_of_ts: str
    confidence_score: float
    evidence: tuple[str, ...]
    reason_codes: tuple[str, ...]
    model_version: str


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float | None
    normalized_value: float | None
    unit: str
    ttl_seconds: int
    source_refs: tuple[str, ...]
    quality_flags: tuple[str, ...] = ()
    confidence_score: float = 1.0
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EarningsSnapshot:
    earnings_snapshot_id: str
    instrument_id: str
    period: str
    revenue_surprise: float | None
    ebitda_surprise: float | None
    net_income_surprise: float | None
    confidence_score: float
    source_refs: tuple[str, ...]
    as_of_ts: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "earnings_snapshot_id": self.earnings_snapshot_id,
            "instrument_id": self.instrument_id,
            "period": self.period,
            "revenue_surprise": self.revenue_surprise,
            "ebitda_surprise": self.ebitda_surprise,
            "net_income_surprise": self.net_income_surprise,
            "confidence_score": self.confidence_score,
            "source_refs": list(self.source_refs),
            "as_of_ts": self.as_of_ts,
            "quality_flags": list(self.quality_flags),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class DividendSnapshot:
    dividend_snapshot_id: str
    instrument_id: str
    expected_dividend_yield: float | None
    dividend_probability: float
    days_to_record_date: int | None
    confidence_score: float
    source_refs: tuple[str, ...]
    as_of_ts: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dividend_snapshot_id": self.dividend_snapshot_id,
            "instrument_id": self.instrument_id,
            "expected_dividend_yield": self.expected_dividend_yield,
            "dividend_probability": self.dividend_probability,
            "days_to_record_date": self.days_to_record_date,
            "confidence_score": self.confidence_score,
            "source_refs": list(self.source_refs),
            "as_of_ts": self.as_of_ts,
            "quality_flags": list(self.quality_flags),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class EarningsDividendExecutionResult:
    module_job_result: ModuleJobResult
    structured_events: tuple[StructuredEvent, ...]
    feature_records: tuple[FeatureRecord, ...]
    earnings_snapshots: tuple[EarningsSnapshot, ...]
    dividend_snapshots: tuple[DividendSnapshot, ...]
    structured_event_refs: tuple[str, ...]
    feature_record_refs: tuple[str, ...]
    earnings_dividend_record_refs: tuple[str, ...]

    @property
    def earnings_snapshot(self) -> EarningsSnapshot | None:
        return self.earnings_snapshots[0] if len(self.earnings_snapshots) == 1 else None

    @property
    def dividend_snapshot(self) -> DividendSnapshot | None:
        return self.dividend_snapshots[0] if len(self.dividend_snapshots) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "structured_events": [event.to_dict() for event in self.structured_events],
            "feature_records": [record.to_dict() for record in self.feature_records],
            "earnings_snapshots": [snapshot.to_dict() for snapshot in self.earnings_snapshots],
            "dividend_snapshots": [snapshot.to_dict() for snapshot in self.dividend_snapshots],
            "structured_event_refs": list(self.structured_event_refs),
            "feature_record_refs": list(self.feature_record_refs),
            "earnings_dividend_record_refs": list(self.earnings_dividend_record_refs),
        }
        if len(self.earnings_snapshots) == 1:
            payload["earnings_snapshot"] = self.earnings_snapshots[0].to_dict()
        if len(self.dividend_snapshots) == 1:
            payload["dividend_snapshot"] = self.dividend_snapshots[0].to_dict()
        return payload


class EarningsDividendIntelligenceService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: EarningsDividendIntelligenceRepository | None = None,
        gateway: Any | None = None,
        earnings_quality_weights: Mapping[str, float] | None = None,
        report_materiality_weights: Mapping[str, float] | None = None,
        dividend_sustainability_weights: Mapping[str, float] | None = None,
        model_id: str | None = None,
    ) -> None:
        self.repository = repository or InMemoryEarningsDividendIntelligenceRepository()
        self.gateway = gateway
        self.earnings_quality_weights = dict(earnings_quality_weights or DEFAULT_EARNINGS_QUALITY_WEIGHTS)
        self.report_materiality_weights = dict(report_materiality_weights or DEFAULT_REPORT_MATERIALITY_WEIGHTS)
        self.dividend_sustainability_weights = dict(dividend_sustainability_weights or DEFAULT_DIVIDEND_SUSTAINABILITY_WEIGHTS)
        self.model_id = _safe_polza_model(
            model_id or os.getenv("POLZA_REASONING_MODEL") or os.getenv("POLZA_DEFAULT_MODEL"),
            DEFAULT_MODEL_ID,
        )

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> EarningsDividendExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> EarningsDividendExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> EarningsDividendExecutionResult:
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
            return EarningsDividendExecutionResult(result, (), (), (), (), (), (), ())

        try:
            self.validate_module_job(job)
            module_input = EarningsDividendInput.from_dict(payload, job)
            raw_text_items, structured_events, expectations, candles, gap_records = self.load_inputs(module_input, job)

            warnings: list[str] = []
            data_requests = self.create_external_requests_for_missing_data(
                module_input=module_input,
                job=job,
                raw_text_items=raw_text_items,
                structured_events=structured_events,
                candles=candles,
                gap_records=gap_records,
            )
            for request in data_requests:
                if self.gateway is not None:
                    self._gateway_process(request)
                warnings.append(f"external_request_created:{request.request_id}")

            llm_warnings, report_facts, dividend_facts = self.extract_facts(
                raw_text_items=raw_text_items,
                structured_events=structured_events,
                module_input=module_input,
                job=job,
            )
            warnings.extend(llm_warnings)

            feature_records: list[FeatureRecord] = []
            output_events: list[StructuredEvent] = []
            ed_records: list[EarningsDividendRecord] = []
            earnings_snapshots: list[EarningsSnapshot] = []
            dividend_snapshots: list[DividendSnapshot] = []

            for instrument_id in module_input.instrument_ids:
                instrument_reports = tuple(fact for fact in report_facts if fact.instrument_id == instrument_id)
                instrument_dividends = tuple(fact for fact in dividend_facts if fact.instrument_id == instrument_id)
                instrument_candles = tuple(candle for candle in candles if candle.instrument_id == instrument_id)
                instrument_gaps = tuple(record for record in gap_records if record.instrument_id == instrument_id)

                if not instrument_reports and not instrument_dividends:
                    warnings.append(f"earnings_dividend_facts_missing:{instrument_id}")

                for fact in instrument_reports:
                    expectation, expectation_warnings = self.select_expectation(expectations, instrument_id, fact.period)
                    warnings.extend(expectation_warnings)
                    snapshot, metrics, event = self.compute_earnings_outputs(
                        fact=fact,
                        expectation=expectation,
                        job=job,
                    )
                    earnings_snapshots.append(snapshot)
                    output_events.append(event)
                    feature_records.extend(
                        self.build_feature_records(
                            instrument_id=instrument_id,
                            metric_values=metrics,
                            horizons=job.horizons,
                            contour=job.contour,
                            timestamp=fact.as_of_ts,
                            context={"period": fact.period, "snapshot_id": snapshot.earnings_snapshot_id},
                        )
                    )
                    ed_records.append(self.build_earnings_record(snapshot, event.event_id))

                for fact in instrument_dividends:
                    snapshot, metrics, event, dividend_warnings = self.compute_dividend_outputs(
                        fact=fact,
                        candles=instrument_candles,
                        gap_records=instrument_gaps,
                        job=job,
                    )
                    warnings.extend(dividend_warnings)
                    dividend_snapshots.append(snapshot)
                    output_events.append(event)
                    feature_records.extend(
                        self.build_feature_records(
                            instrument_id=instrument_id,
                            metric_values=metrics,
                            horizons=job.horizons,
                            contour=job.contour,
                            timestamp=fact.as_of_ts,
                            context={"snapshot_id": snapshot.dividend_snapshot_id},
                        )
                    )
                    ed_records.append(self.build_dividend_record(snapshot, event.event_id))

            event_refs = tuple(self.write_structured_event(event) for event in output_events)
            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            ed_refs = tuple(self.write_earnings_dividend_record(record) for record in ed_records)
            output_refs = event_refs + feature_refs + ed_refs

            if not output_refs:
                warnings.append("earnings_dividend_output_missing")
            status = "success" if output_refs and not warnings else "partial_success" if output_refs else "skipped"
            return EarningsDividendExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(feature_records),
                    events_written=len(output_events),
                ),
                structured_events=tuple(output_events),
                feature_records=tuple(feature_records),
                earnings_snapshots=tuple(earnings_snapshots),
                dividend_snapshots=tuple(dividend_snapshots),
                structured_event_refs=event_refs,
                feature_record_refs=feature_refs,
                earnings_dividend_record_refs=ed_refs,
            )
        except (EarningsDividendIntelligenceError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise EarningsDividendIntelligenceError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise EarningsDividendIntelligenceError("module_job.module_name must be Earnings & Dividend Intelligence Module")
        if job.contour not in VALID_CONTOURS:
            raise EarningsDividendIntelligenceError("module_job.contour must be event_contour or daily_contour")
        if not job.instrument_ids:
            raise EarningsDividendIntelligenceError("module_job.instrument_ids is required")
        if not job.input_refs:
            raise EarningsDividendIntelligenceError("module_job.input_refs is required")
        if not job.horizons:
            raise EarningsDividendIntelligenceError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise EarningsDividendIntelligenceError(f"invalid module_job.horizons: {invalid_horizons}")
        if not job.run_mode:
            raise EarningsDividendIntelligenceError("module_job.run_mode is required")

    def load_inputs(
        self,
        module_input: EarningsDividendInput,
        job: ModuleJob,
    ) -> tuple[
        tuple[RawTextItem, ...],
        tuple[StructuredEvent, ...],
        tuple[FinancialExpectation, ...],
        tuple[RawCandle, ...],
        tuple[HistoricalGapRecord, ...],
    ]:
        report_refs = tuple(dict.fromkeys((*module_input.report_refs, *_refs_with_prefix(job.input_refs, "raw_text.raw_text_item"))))
        event_refs = tuple(dict.fromkeys((*module_input.dividend_event_refs, *_refs_with_prefix(job.input_refs, "events.structured_event"))))
        raw_text_items = self.repository.list_raw_text_items(
            report_refs,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        structured_events = self.repository.list_structured_events(
            event_refs,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        expectations = self.repository.list_financial_expectations(
            module_input.financial_expectation_ref,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        candles_by_ref = self.repository.list_market_candles(
            module_input.historical_gap_ref,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        if module_input.historical_gap_ref:
            current_price_candles = self.repository.list_market_candles(
                "",
                job.universe_id,
                module_input.instrument_ids,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )
            candles = tuple(
                {
                    candle.raw_candle_id: candle
                    for candle in (*candles_by_ref, *current_price_candles)
                }.values()
            )
        else:
            candles = candles_by_ref
        gap_records = self.repository.list_historical_gap_records(
            module_input.historical_gap_ref,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        return raw_text_items, structured_events, expectations, candles, gap_records

    def extract_facts(
        self,
        *,
        raw_text_items: tuple[RawTextItem, ...],
        structured_events: tuple[StructuredEvent, ...],
        module_input: EarningsDividendInput,
        job: ModuleJob,
    ) -> tuple[tuple[str, ...], tuple[FinancialFactSet, ...], tuple[DividendFactSet, ...]]:
        warnings: list[str] = []
        report_facts: list[FinancialFactSet] = []
        dividend_facts: list[DividendFactSet] = []

        for event in structured_events:
            source_refs = tuple(dict.fromkeys((f"events.structured_event:{event.event_id}", *event.source_refs)))
            if event.event_type == "earnings":
                fact = _financial_fact_from_payload(
                    event.payload,
                    source_refs=source_refs,
                    instrument_fallback=event.instrument_ids[0] if event.instrument_ids else "",
                    as_of_ts=event.event_ts,
                    confidence_score=event.confidence_score,
                    evidence=event.evidence,
                    reason_codes=event.reason_codes,
                    model_version=event.model_version,
                )
                if fact is not None:
                    report_facts.append(fact)
                elif _item_has_financial_facts(event.payload):
                    warnings.append(f"report_period_missing:{event.event_id}")
            if event.event_type == "dividend":
                fact = _dividend_fact_from_payload(
                    event.payload,
                    source_refs=source_refs,
                    instrument_fallback=event.instrument_ids[0] if event.instrument_ids else "",
                    as_of_ts=event.event_ts,
                    confidence_score=event.confidence_score,
                    evidence=event.evidence,
                    reason_codes=event.reason_codes,
                    model_version=event.model_version,
                )
                if fact is not None:
                    dividend_facts.append(fact)

        max_llm_items = _env_int("LLM_MAX_ITEMS_PER_RUN", 20)
        if max_llm_items > 0 and len(raw_text_items) > max_llm_items:
            warnings.append("llm_items_per_run_capped")
            raw_text_items = raw_text_items[:max_llm_items]

        for raw_item in raw_text_items:
            source_refs = (f"raw_text.raw_text_item:{raw_item.raw_text_item_id}",)
            payload = raw_item.source_payload
            structured_report = _financial_fact_from_payload(
                payload,
                source_refs=source_refs,
                instrument_fallback=raw_item.instrument_ids[0] if raw_item.instrument_ids else "",
                as_of_ts=raw_item.published_at or raw_item.fetched_at or job.time_range.to_ts,
                confidence_score=float(payload.get("confidence_score") or 1.0),
                evidence=_string_tuple(payload.get("evidence")),
                reason_codes=_string_tuple(payload.get("reason_codes")),
                model_version=str(payload.get("model_version") or "structured_source"),
            )
            structured_dividend = _dividend_fact_from_payload(
                payload,
                source_refs=source_refs,
                instrument_fallback=raw_item.instrument_ids[0] if raw_item.instrument_ids else "",
                as_of_ts=raw_item.published_at or raw_item.fetched_at or job.time_range.to_ts,
                confidence_score=float(payload.get("confidence_score") or 1.0),
                evidence=_string_tuple(payload.get("evidence")),
                reason_codes=_string_tuple(payload.get("reason_codes")),
                model_version=str(payload.get("model_version") or "structured_source"),
            )
            if structured_report is not None:
                report_facts.append(structured_report)
            elif _item_has_financial_facts(payload):
                warnings.append(f"report_period_missing:{raw_item.raw_text_item_id}")
            if structured_dividend is not None:
                dividend_facts.append(structured_dividend)
            if structured_report is not None or structured_dividend is not None:
                continue

            llm_payload = _embedded_llm_payload(raw_item)
            if llm_payload is None and self.gateway is not None and (raw_item.body or raw_item.title):
                if not _llm_text_runtime_enabled(job):
                    warnings.append(f"llm_text_module_disabled_in_live_runtime:{raw_item.raw_text_item_id}")
                    continue
                request = self.create_llm_extraction_request(raw_item, module_input, job)
                response = self._gateway_process(request)
                warnings.append(f"external_request_created:{request.request_id}")
                llm_payload = _llm_payload_from_response(response)
            if llm_payload is None:
                if raw_item.body or raw_item.title:
                    warnings.append(f"llm_extraction_missing:{raw_item.raw_text_item_id}")
                continue

            envelope_payload = _parse_json_payload(llm_payload)
            self._assert_no_forbidden_fields(envelope_payload)
            envelope = LlmEnvelope.from_payload(envelope_payload)
            for item in envelope.items:
                item_evidence = _string_tuple(item.get("evidence")) or envelope.evidence
                item_reason_codes = _string_tuple(item.get("reason_codes")) or envelope.reason_codes
                if not item_evidence or not item_reason_codes:
                    raise EarningsDividendIntelligenceError("LLM item requires evidence and reason_codes")
                if _item_has_financial_facts(item):
                    fact = _financial_fact_from_payload(
                        item,
                        source_refs=source_refs,
                        instrument_fallback=raw_item.instrument_ids[0] if raw_item.instrument_ids else "",
                        as_of_ts=_coerce_timestamp(item.get("event_ts"), raw_item.published_at or raw_item.fetched_at or job.time_range.to_ts),
                        confidence_score=envelope.confidence_score,
                        evidence=item_evidence,
                        reason_codes=item_reason_codes,
                        model_version=envelope.model_version,
                    )
                    if fact is not None:
                        report_facts.append(fact)
                    elif _item_has_financial_facts(item):
                        warnings.append(f"report_period_missing:{raw_item.raw_text_item_id}")
                if _item_has_dividend_terms(item):
                    fact = _dividend_fact_from_payload(
                        item,
                        source_refs=source_refs,
                        instrument_fallback=raw_item.instrument_ids[0] if raw_item.instrument_ids else "",
                        as_of_ts=_coerce_timestamp(item.get("event_ts"), raw_item.published_at or raw_item.fetched_at or job.time_range.to_ts),
                        confidence_score=envelope.confidence_score,
                        evidence=item_evidence,
                        reason_codes=item_reason_codes,
                        model_version=envelope.model_version,
                    )
                    if fact is not None:
                        dividend_facts.append(fact)
            warnings.extend(envelope.warnings)

        return tuple(dict.fromkeys(warnings)), tuple(_dedupe_report_facts(report_facts)), tuple(_dedupe_dividend_facts(dividend_facts))

    def select_expectation(
        self,
        expectations: tuple[FinancialExpectation, ...],
        instrument_id: str,
        period: str,
    ) -> tuple[FinancialExpectation | None, tuple[str, ...]]:
        warnings: list[str] = []
        candidates = [
            item
            for item in expectations
            if item.instrument_id == instrument_id and (not period or not item.period or item.period == period)
        ]
        valid = []
        for item in candidates:
            if not item.estimate_source or not item.model_version:
                warnings.append(f"expectation_source_or_model_missing:{instrument_id}:{item.expectation_id}")
                continue
            if not item.source_refs:
                warnings.append(f"expectation_source_refs_missing:{instrument_id}:{item.expectation_id}")
                continue
            valid.append(item)
        sources = {item.estimate_source for item in valid if item.estimate_source}
        if len(sources) > 1:
            warnings.append(f"multiple_estimate_sources_not_mixed:{instrument_id}")
        if not valid:
            warnings.append(f"missing_expectation_baseline:{instrument_id}:{period or 'unknown_period'}")
            return None, tuple(dict.fromkeys(warnings))
        return sorted(valid, key=lambda item: item.as_of_ts or "")[-1], tuple(dict.fromkeys(warnings))

    def compute_earnings_outputs(
        self,
        *,
        fact: FinancialFactSet,
        expectation: FinancialExpectation | None,
        job: ModuleJob,
    ) -> tuple[EarningsSnapshot, tuple[MetricValue, ...], StructuredEvent]:
        fields = dict(fact.fields)
        expected_fields = dict(expectation.fields) if expectation is not None else {}
        expectation_refs = expectation.source_refs if expectation is not None else ()
        source_refs = tuple(dict.fromkeys((*fact.source_refs, *expectation_refs)))
        if not source_refs:
            raise EarningsDividendIntelligenceError("source_refs required for earnings_snapshot")

        metrics: list[MetricValue] = []
        surprise_values: dict[str, float | None] = {}
        missing_baseline = []

        for metric_name, (actual_key, expected_key, unit) in SURPRISE_METRICS.items():
            actual = _field_float(fields, actual_key, actual_key.replace("_actual", ""), actual_key.replace("net_", ""))
            expected = _field_float(expected_fields, expected_key, expected_key.replace("_expected", ""), actual_key)
            value = surprise(actual, expected)
            flags: tuple[str, ...] = ()
            if actual is not None and expected is None:
                flags = ("missing_expectation_baseline",)
                missing_baseline.append(metric_name)
            elif value is None:
                continue
            surprise_values[metric_name] = value
            metrics.append(
                MetricValue(
                    metric_name=metric_name,
                    metric_group="fundamental",
                    metric_type="derived_metric",
                    raw_value=value,
                    normalized_value=signed_to_unit(value),
                    unit=unit,
                    ttl_seconds=EARNINGS_TTL_SECONDS,
                    source_refs=source_refs,
                    quality_flags=flags,
                    confidence_score=_metric_confidence(fact.confidence_score, flags),
                    payload={
                        "formula": f"({actual_key} - {expected_key}) / abs({expected_key})",
                        "actual_value": actual,
                        "expected_value": expected,
                        "estimate_source": expectation.estimate_source if expectation else None,
                        "expectation_model_version": expectation.model_version if expectation else None,
                    },
                )
            )

        margin_actual = _field_float(fields, "margin_actual", "ebitda_margin_actual", "margin")
        margin_expected = _field_float(expected_fields, "margin_expected", "ebitda_margin_expected", "margin")
        margin_value = margin_surprise(margin_actual, margin_expected)
        if margin_actual is not None or margin_expected is not None:
            flags = ("missing_expectation_baseline",) if margin_actual is not None and margin_expected is None else ()
            if flags:
                missing_baseline.append("margin_surprise")
            if margin_value is not None or flags:
                metrics.append(
                    MetricValue(
                        metric_name="margin_surprise",
                        metric_group="fundamental",
                        metric_type="derived_metric",
                        raw_value=margin_value,
                        normalized_value=signed_to_unit(margin_value, expected_abs_bound=0.25),
                        unit="ratio_delta",
                        ttl_seconds=EARNINGS_TTL_SECONDS,
                        source_refs=source_refs,
                        quality_flags=flags,
                        confidence_score=_metric_confidence(fact.confidence_score, flags),
                        payload={
                            "formula": "margin_actual - margin_expected",
                            "actual_value": margin_actual,
                            "expected_value": margin_expected,
                            "estimate_source": expectation.estimate_source if expectation else None,
                            "expectation_model_version": expectation.model_version if expectation else None,
                        },
                    )
                )
                surprise_values["margin_surprise"] = margin_value

        earnings_quality_components = {
            "fcf_conversion_z": _field_float(fields, "fcf_conversion_z"),
            "margin_quality_z": _field_float(fields, "margin_quality_z"),
            "negative_one_off_items_ratio_z": _negative(_field_float(fields, "one_off_items_ratio_z")),
            "accruals_quality_z": _field_float(fields, "accruals_quality_z"),
        }
        earnings_quality = weighted_average(earnings_quality_components, self.earnings_quality_weights)
        if earnings_quality is not None:
            metrics.append(
                MetricValue(
                    metric_name="earnings_quality_score",
                    metric_group="fundamental",
                    metric_type="composite_score",
                    raw_value=earnings_quality,
                    normalized_value=z_to_unit(earnings_quality),
                    unit="score",
                    ttl_seconds=EARNINGS_TTL_SECONDS,
                    source_refs=fact.source_refs,
                    confidence_score=fact.confidence_score,
                    payload={
                        "formula": "WAvg([fcf_conversion_z, margin_quality_z, -one_off_items_ratio_z, accruals_quality_z], active_weights)",
                        "component_values": {key: value for key, value in earnings_quality_components.items() if value is not None},
                        "weight_source": "service_config_default_or_injected",
                    },
                )
            )

        materiality_components = {
            "abs_revenue_surprise": _abs_or_none(surprise_values.get("revenue_surprise")),
            "abs_ebitda_surprise": _abs_or_none(surprise_values.get("ebitda_surprise")),
            "abs_net_income_surprise": _abs_or_none(surprise_values.get("net_income_surprise")),
            "guidance_change_score": clip(_field_float(fields, "guidance_change_score")),
        }
        materiality = weighted_average(materiality_components, self.report_materiality_weights)
        if materiality is not None:
            metrics.append(
                MetricValue(
                    metric_name="report_materiality_score",
                    metric_group="event",
                    metric_type="composite_score",
                    raw_value=materiality,
                    normalized_value=clip(materiality),
                    unit="score",
                    ttl_seconds=EARNINGS_TTL_SECONDS,
                    source_refs=source_refs,
                    confidence_score=fact.confidence_score,
                    payload={
                        "formula": "WAvg([abs(revenue_surprise), abs(ebitda_surprise), abs(net_income_surprise), guidance_change_score], active_weights)",
                        "component_values": {key: value for key, value in materiality_components.items() if value is not None},
                        "weight_source": "service_config_default_or_injected",
                    },
                )
            )

        sentiment = _field_float(fields, "report_sentiment_score", "sentiment_score")
        if sentiment is not None and fact.evidence and fact.reason_codes:
            metrics.append(
                MetricValue(
                    metric_name="report_sentiment_score",
                    metric_group="event",
                    metric_type="model_score",
                    raw_value=clip(sentiment, -1.0, 1.0),
                    normalized_value=signed_to_unit(sentiment),
                    unit="score",
                    ttl_seconds=EARNINGS_TTL_SECONDS,
                    source_refs=fact.source_refs,
                    confidence_score=fact.confidence_score,
                    payload={
                        "model_version": fact.model_version,
                        "evidence": list(fact.evidence),
                        "reason_codes": list(fact.reason_codes),
                        "score_scale": "[-1, 1]",
                    },
                )
            )

        quality_flags = tuple(dict.fromkeys(("missing_expectation_baseline",) if missing_baseline else ()))
        snapshot = EarningsSnapshot(
            earnings_snapshot_id=stable_uuid_id(
                {
                    "instrument_id": fact.instrument_id,
                    "period": fact.period,
                    "source_refs": source_refs,
                    "calculation_version": CALCULATION_VERSION,
                }
            ),
            instrument_id=fact.instrument_id,
            period=fact.period,
            revenue_surprise=surprise_values.get("revenue_surprise"),
            ebitda_surprise=surprise_values.get("ebitda_surprise"),
            net_income_surprise=surprise_values.get("net_income_surprise"),
            confidence_score=_snapshot_confidence(metrics, quality_flags),
            source_refs=source_refs,
            as_of_ts=fact.as_of_ts,
            quality_flags=quality_flags,
            payload={
                "financial_facts": {key: value for key, value in fields.items() if key.endswith("_actual") or key in {"period", "reporting_standard"}},
                "estimate_source": expectation.estimate_source if expectation else None,
                "expectation_model_version": expectation.model_version if expectation else None,
                "missing_baseline_metrics": missing_baseline,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        event = self.build_structured_event(
            event_type="earnings",
            event_subtype="report",
            instrument_id=fact.instrument_id,
            event_ts=fact.as_of_ts,
            source_refs=source_refs,
            relevance_score=1.0,
            materiality_score=clip_required(materiality or 0.0),
            novelty_score=clip_required(_field_float(fields, "novelty_score") or 0.5),
            surprise_score=clip_required(max((_abs_or_none(value) or 0.0 for value in surprise_values.values()), default=0.0)),
            sentiment_score=clip(sentiment, -1.0, 1.0) or 0.0,
            confidence_score=snapshot.confidence_score,
            evidence=fact.evidence or source_refs,
            reason_codes=tuple(dict.fromkeys((*fact.reason_codes, "earnings_report_processed"))),
            model_version=fact.model_version,
            payload={"earnings_snapshot_id": snapshot.earnings_snapshot_id, "period": fact.period},
        )
        return snapshot, tuple(metrics), event

    def compute_dividend_outputs(
        self,
        *,
        fact: DividendFactSet,
        candles: tuple[RawCandle, ...],
        gap_records: tuple[HistoricalGapRecord, ...],
        job: ModuleJob,
    ) -> tuple[DividendSnapshot, tuple[MetricValue, ...], StructuredEvent, tuple[str, ...]]:
        fields = dict(fact.fields)
        warnings: list[str] = []
        metrics: list[MetricValue] = []
        source_refs = tuple(dict.fromkeys(fact.source_refs))
        if not source_refs:
            raise EarningsDividendIntelligenceError("source_refs required for dividend_snapshot")

        price_record = _latest_price(candles)
        price = price_record.close_price if price_record is not None else None
        price_refs = price_record.source_refs if price_record is not None else ()
        metric_source_refs = tuple(dict.fromkeys((*source_refs, *price_refs)))
        if price is None:
            warnings.append(f"market_price_missing:{fact.instrument_id}")

        expected_dividend = _field_float(fields, "expected_dividend_per_share", "expected_dividend", "dividend_per_share")
        announced_dividend = _field_float(fields, "announced_dividend", "recommended_dividend", "dividend_per_share")
        if announced_dividend is not None and not source_refs:
            raise EarningsDividendIntelligenceError("announced dividend requires source reference")
        expected_for_yield = expected_dividend if expected_dividend is not None else announced_dividend
        dividend_yield = expected_dividend_yield(expected_for_yield, price)

        if dividend_yield is not None:
            metrics.append(
                MetricValue(
                    metric_name="expected_dividend_yield",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=dividend_yield,
                    normalized_value=clip(dividend_yield / 0.2),
                    unit="ratio",
                    ttl_seconds=self.dividend_ttl_seconds(fields, job),
                    source_refs=metric_source_refs,
                    confidence_score=fact.confidence_score,
                    payload={
                        "formula": "expected_dividend_per_share / P_t",
                        "expected_dividend_per_share": expected_for_yield,
                        "price": price,
                        "price_source_refs": list(price_refs),
                    },
                )
            )

        dividend_probability_value = self.compute_dividend_probability(fields)
        metrics.append(
            MetricValue(
                metric_name="dividend_probability",
                metric_group="dividend",
                metric_type="model_score" if _field_float(fields, "dividend_probability") is not None else "derived_metric",
                raw_value=dividend_probability_value,
                normalized_value=dividend_probability_value,
                unit="probability",
                ttl_seconds=self.dividend_ttl_seconds(fields, job),
                source_refs=source_refs,
                confidence_score=fact.confidence_score,
                payload={
                    "formula": "rule probability 0..1 based on policy, earnings, FCF and management statements",
                    "model_version": str(fields.get("dividend_probability_model_version") or CALCULATION_VERSION),
                    "evidence": list(fact.evidence),
                    "reason_codes": list(fact.reason_codes or ("dividend_probability_rule",)),
                    "status": fields.get("status"),
                },
            )
        )

        dividend_surprise_value = dividend_surprise(announced_dividend, expected_dividend)
        if announced_dividend is not None or expected_dividend is not None:
            flags = ("missing_expectation_baseline",) if announced_dividend is not None and expected_dividend is None else ()
            metrics.append(
                MetricValue(
                    metric_name="dividend_surprise",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=dividend_surprise_value,
                    normalized_value=signed_to_unit(dividend_surprise_value),
                    unit="ratio",
                    ttl_seconds=self.dividend_ttl_seconds(fields, job),
                    source_refs=source_refs,
                    quality_flags=flags,
                    confidence_score=_metric_confidence(fact.confidence_score, flags),
                    payload={
                        "formula": "(announced_dividend - expected_dividend) / abs(expected_dividend)",
                        "announced_dividend": announced_dividend,
                        "expected_dividend": expected_dividend,
                    },
                )
            )

        record_date = parse_date(fields.get("record_date") or fields.get("expected_record_date"))
        as_of_date = parse_utc_iso(job.time_range.to_ts).date()
        days_to_record = days_to_record_date(as_of_date, record_date)
        if record_date is None and any(key in fields for key in ("announced_dividend", "recommended_dividend", "dividend_per_share")):
            warnings.append(f"dividend_record_date_missing:{fact.instrument_id}")
        if days_to_record is not None:
            metrics.append(
                MetricValue(
                    metric_name="days_to_record_date",
                    metric_group="dividend",
                    metric_type="raw_metric",
                    raw_value=float(days_to_record),
                    normalized_value=None,
                    unit="calendar_days",
                    ttl_seconds=self.dividend_ttl_seconds(fields, job),
                    source_refs=source_refs,
                    confidence_score=fact.confidence_score,
                    payload={"formula": "calendar days from as_of_date to record_date", "record_date": record_date.isoformat()},
                )
            )

        gap_stats, gap_refs, gap_warnings = self.compute_gap_stats(gap_records)
        warnings.extend(gap_warnings)
        if gap_stats.get("historical_gap_size") is not None:
            metrics.append(
                MetricValue(
                    metric_name="historical_gap_size",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=gap_stats["historical_gap_size"],
                    normalized_value=clip(gap_stats["historical_gap_size"] / 0.2),
                    unit="ratio",
                    ttl_seconds=DIVIDEND_DEFAULT_TTL_SECONDS,
                    source_refs=gap_refs,
                    confidence_score=0.85,
                    payload={"formula": "median abs(open_after_record_date / close_before_record_date - 1) using adjusted prices"},
                )
            )
        if gap_stats.get("gap_close_probability_20d") is not None:
            metrics.append(
                MetricValue(
                    metric_name="gap_close_probability_20d",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=gap_stats["gap_close_probability_20d"],
                    normalized_value=gap_stats["gap_close_probability_20d"],
                    unit="probability",
                    ttl_seconds=DIVIDEND_DEFAULT_TTL_SECONDS,
                    source_refs=gap_refs,
                    confidence_score=0.85,
                    payload={"formula": "historical share of dividend gaps closed within 20 trading days"},
                )
            )
        if gap_stats.get("gap_close_speed_median") is not None:
            metrics.append(
                MetricValue(
                    metric_name="gap_close_speed_median",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=gap_stats["gap_close_speed_median"],
                    normalized_value=None,
                    unit="trading_days",
                    ttl_seconds=DIVIDEND_DEFAULT_TTL_SECONDS,
                    source_refs=gap_refs,
                    confidence_score=0.85,
                    payload={"formula": "median trading days to close dividend gap historically"},
                )
            )

        gap_risk = expected_gap_risk(
            dividend_yield,
            gap_stats.get("gap_close_probability_20d"),
            gap_stats.get("volatility_adjustment") or _field_float(fields, "volatility_adjustment"),
        )
        if gap_risk is not None:
            metrics.append(
                MetricValue(
                    metric_name="expected_gap_risk",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=gap_risk,
                    normalized_value=clip(gap_risk / 0.2),
                    unit="ratio",
                    ttl_seconds=DIVIDEND_DEFAULT_TTL_SECONDS,
                    source_refs=tuple(dict.fromkeys((*metric_source_refs, *gap_refs))),
                    confidence_score=0.8,
                    payload={"formula": "expected_dividend_yield * (1 - gap_close_probability_20d) adjusted by volatility"},
                )
            )

        payout = self.compute_payout_ratio(fields)
        if payout is not None:
            metrics.append(
                MetricValue(
                    metric_name="payout_ratio",
                    metric_group="dividend",
                    metric_type="derived_metric",
                    raw_value=payout,
                    normalized_value=clip(payout),
                    unit="ratio",
                    ttl_seconds=EARNINGS_TTL_SECONDS,
                    source_refs=source_refs,
                    confidence_score=fact.confidence_score,
                    payload={"formula": "dividends_total / net_income or dividends_total / fcf depending policy"},
                )
            )

        sustainability_components = {
            "payout_safety_z": _field_float(fields, "payout_safety_z") or (None if payout is None else 1.0 - payout),
            "fcf_coverage_z": _field_float(fields, "fcf_coverage_z"),
            "negative_leverage_z": _negative(_field_float(fields, "leverage_z", "net_debt_ebitda_z")),
            "dividend_policy_confidence": _field_float(fields, "dividend_policy_confidence"),
        }
        sustainability = weighted_average(sustainability_components, self.dividend_sustainability_weights)
        if sustainability is not None:
            metrics.append(
                MetricValue(
                    metric_name="dividend_sustainability_score",
                    metric_group="dividend",
                    metric_type="composite_score",
                    raw_value=sustainability,
                    normalized_value=z_to_unit(sustainability),
                    unit="score",
                    ttl_seconds=EARNINGS_TTL_SECONDS,
                    source_refs=source_refs,
                    confidence_score=fact.confidence_score,
                    payload={
                        "formula": "WAvg([payout_safety_z, fcf_coverage_z, -leverage_z, dividend_policy_confidence], active_weights)",
                        "component_values": {key: value for key, value in sustainability_components.items() if value is not None},
                        "weight_source": "service_config_default_or_injected",
                    },
                )
            )

        transaction_cost = _field_float(fields, "expected_transaction_cost", "transaction_cost")
        carry = dividend_carry_score(dividend_yield, dividend_probability_value, gap_risk, transaction_cost)
        if carry is not None:
            metrics.append(
                MetricValue(
                    metric_name="dividend_carry_score",
                    metric_group="dividend",
                    metric_type="composite_score",
                    raw_value=carry,
                    normalized_value=signed_to_unit(carry, expected_abs_bound=0.2),
                    unit="score",
                    ttl_seconds=self.dividend_ttl_seconds(fields, job),
                    source_refs=tuple(dict.fromkeys((*metric_source_refs, *gap_refs))),
                    confidence_score=fact.confidence_score,
                    payload={
                        "formula": "expected_dividend_yield * dividend_probability - expected_gap_risk - expected_transaction_cost",
                        "expected_transaction_cost": transaction_cost,
                    },
                )
            )

        quality_flags = tuple(dict.fromkeys(("dividend_record_date_missing",) if record_date is None else ()))
        snapshot = DividendSnapshot(
            dividend_snapshot_id=stable_uuid_id(
                {
                    "instrument_id": fact.instrument_id,
                    "record_date": record_date.isoformat() if record_date else "",
                    "source_refs": source_refs,
                    "calculation_version": CALCULATION_VERSION,
                }
            ),
            instrument_id=fact.instrument_id,
            expected_dividend_yield=dividend_yield,
            dividend_probability=dividend_probability_value,
            days_to_record_date=days_to_record,
            confidence_score=_snapshot_confidence(metrics, quality_flags),
            source_refs=source_refs,
            as_of_ts=fact.as_of_ts,
            quality_flags=quality_flags,
            payload={
                "dividend_terms": {
                    "expected_dividend_per_share": expected_for_yield,
                    "announced_dividend": announced_dividend,
                    "record_date": record_date.isoformat() if record_date else None,
                    "ex_dividend_date": str(fields.get("ex_dividend_date") or ""),
                    "status": fields.get("status"),
                },
                "historical_gap_uses_adjusted_prices": bool(gap_refs),
                "calculation_version": CALCULATION_VERSION,
            },
        )
        event = self.build_structured_event(
            event_type="dividend",
            event_subtype=str(fields.get("status") or "dividend_terms"),
            instrument_id=fact.instrument_id,
            event_ts=_record_date_ts(record_date) or fact.as_of_ts,
            source_refs=source_refs,
            relevance_score=1.0,
            materiality_score=clip_required((dividend_yield or 0.0) / 0.1),
            novelty_score=clip_required(_field_float(fields, "novelty_score") or 0.5),
            surprise_score=clip_required(abs(dividend_surprise_value or 0.0)),
            sentiment_score=clip(_field_float(fields, "sentiment_score"), -1.0, 1.0) or 0.0,
            confidence_score=snapshot.confidence_score,
            evidence=fact.evidence or source_refs,
            reason_codes=tuple(dict.fromkeys((*fact.reason_codes, "dividend_terms_processed"))),
            model_version=fact.model_version,
            payload={"dividend_snapshot_id": snapshot.dividend_snapshot_id, "record_date": record_date.isoformat() if record_date else None},
        )
        return snapshot, tuple(metrics), event, tuple(dict.fromkeys(warnings))

    def compute_gap_stats(self, gap_records: tuple[HistoricalGapRecord, ...]) -> tuple[Mapping[str, float | None], tuple[str, ...], tuple[str, ...]]:
        warnings: list[str] = []
        adjusted_records = [record for record in gap_records if record.adjusted_prices]
        if gap_records and not adjusted_records:
            warnings.append("adjusted_price_history_missing")
        if not adjusted_records:
            return {}, (), tuple(warnings)
        source_refs = tuple(dict.fromkeys(ref for record in adjusted_records for ref in record.source_refs))
        gap_returns = tuple(value for record in adjusted_records for value in record.gap_returns)
        closed = tuple(value for record in adjusted_records for value in record.closed_within_20d)
        days = tuple(value for record in adjusted_records for value in record.days_to_close)
        volatility_adjustments = tuple(record.volatility_adjustment for record in adjusted_records if record.volatility_adjustment is not None)
        return (
            {
                "historical_gap_size": historical_gap_size(gap_returns),
                "gap_close_probability_20d": gap_close_probability_20d(closed),
                "gap_close_speed_median": gap_close_speed_median(days),
                "volatility_adjustment": sum(volatility_adjustments) / len(volatility_adjustments) if volatility_adjustments else None,
            },
            source_refs,
            tuple(warnings),
        )

    def compute_dividend_probability(self, fields: Mapping[str, Any]) -> float:
        explicit = clip(_field_float(fields, "dividend_probability"), 0.0, 1.0)
        if explicit is not None:
            return explicit
        status = str(fields.get("status") or fields.get("dividend_status") or "").lower()
        base_by_status = {
            "cancelled": 0.0,
            "unknown": 0.35,
            "policy_expected": 0.55,
            "expected": 0.55,
            "recommended": 0.75,
            "board_recommended": 0.8,
            "approved": 0.95,
            "declared": 0.95,
            "paid": 1.0,
        }
        probability = base_by_status.get(status, 0.55 if _field_float(fields, "expected_dividend_per_share") is not None else 0.35)
        fcf_coverage = _field_float(fields, "fcf_coverage")
        if fcf_coverage is not None:
            if fcf_coverage >= 1.0:
                probability += 0.1
            elif fcf_coverage < 0.5:
                probability -= 0.15
        payout = self.compute_payout_ratio(fields)
        if payout is not None and payout > 1.0:
            probability -= 0.1
        policy_confidence = _field_float(fields, "dividend_policy_confidence")
        if policy_confidence is not None:
            probability = (probability + clip_required(policy_confidence)) / 2.0
        return clip_required(probability)

    def compute_payout_ratio(self, fields: Mapping[str, Any]) -> float | None:
        explicit = _field_float(fields, "payout_ratio")
        if explicit is not None:
            return explicit
        dividends_total = _field_float(fields, "dividends_total", "total_dividends")
        denominator_policy = str(fields.get("payout_denominator") or fields.get("dividend_policy_denominator") or "").lower()
        if denominator_policy == "fcf":
            denominator = _field_float(fields, "fcf_actual", "free_cash_flow", "free_cash_flow_ttm")
        elif denominator_policy == "net_income":
            denominator = _field_float(fields, "net_income_actual", "net_income", "net_income_ttm")
        else:
            denominator = _field_float(fields, "net_income_actual", "net_income", "net_income_ttm")
            if denominator is None:
                denominator = _field_float(fields, "fcf_actual", "free_cash_flow", "free_cash_flow_ttm")
        if dividends_total is None:
            announced = _field_float(fields, "announced_dividend", "expected_dividend_per_share", "dividend_per_share")
            shares = _field_float(fields, "shares_outstanding")
            if announced is not None and shares is not None:
                dividends_total = announced * shares
        return payout_ratio(dividends_total, denominator)

    def dividend_ttl_seconds(self, fields: Mapping[str, Any], job: ModuleJob) -> int:
        record_date = parse_date(fields.get("record_date") or fields.get("expected_record_date"))
        if record_date is None:
            return DIVIDEND_DEFAULT_TTL_SECONDS
        days = days_to_record_date(parse_utc_iso(job.time_range.to_ts).date(), record_date)
        if days is None or days <= 0:
            return 24 * 60 * 60
        return max(24 * 60 * 60, days * 24 * 60 * 60)

    def build_feature_records(
        self,
        *,
        instrument_id: str,
        metric_values: tuple[MetricValue, ...],
        horizons: tuple[str, ...],
        contour: str,
        timestamp: str,
        context: Mapping[str, Any],
    ) -> tuple[FeatureRecord, ...]:
        records: list[FeatureRecord] = []
        for metric_value in metric_values:
            if not metric_value.source_refs:
                raise EarningsDividendIntelligenceError(f"source_refs required for feature: {metric_value.metric_name}")
            for horizon in horizons:
                feature_payload = {
                    "instrument_id": instrument_id,
                    "metric_name": metric_value.metric_name,
                    "horizon": horizon,
                    "timestamp": timestamp,
                    "calculation_version": CALCULATION_VERSION,
                    **dict(context),
                }
                records.append(
                    FeatureRecord(
                        feature_id=stable_record_id("feature", feature_payload),
                        instrument_id=instrument_id,
                        metric_name=metric_value.metric_name,
                        metric_group=metric_value.metric_group,
                        metric_type=metric_value.metric_type,
                        raw_value=metric_value.raw_value,
                        normalized_value=metric_value.normalized_value,
                        unit=metric_value.unit,
                        horizon=horizon,
                        contour=contour,
                        timestamp=timestamp,
                        ttl_seconds=metric_value.ttl_seconds,
                        confidence_score=_metric_confidence(metric_value.confidence_score, metric_value.quality_flags),
                        source_module=self.module_name,
                        source_refs=metric_value.source_refs,
                        calculation_version=CALCULATION_VERSION,
                        quality_flags=metric_value.quality_flags,
                        payload={**dict(metric_value.payload), **dict(context), "formula_version": CALCULATION_VERSION},
                    )
                )
        return tuple(records)

    def build_structured_event(
        self,
        *,
        event_type: str,
        event_subtype: str,
        instrument_id: str,
        event_ts: str,
        source_refs: tuple[str, ...],
        relevance_score: float,
        materiality_score: float,
        novelty_score: float,
        surprise_score: float,
        sentiment_score: float,
        confidence_score: float,
        evidence: tuple[str, ...],
        reason_codes: tuple[str, ...],
        model_version: str,
        payload: Mapping[str, Any],
    ) -> StructuredEvent:
        event_payload = {
            "instrument_id": instrument_id,
            "event_type": event_type,
            "event_subtype": event_subtype,
            "event_ts": event_ts,
            "source_refs": source_refs,
            "calculation_version": CALCULATION_VERSION,
            **dict(payload),
        }
        return StructuredEvent(
            event_id=stable_record_id("event", event_payload),
            instrument_ids=(instrument_id,),
            event_type=event_type,
            event_subtype=event_subtype,
            event_ts=event_ts,
            detected_at=to_utc_iso(utc_now()),
            source_refs=source_refs,
            relevance_score=clip_required(relevance_score),
            materiality_score=clip_required(materiality_score),
            novelty_score=clip_required(novelty_score),
            surprise_score=clip_required(surprise_score),
            sentiment_score=clip_required(sentiment_score, -1.0, 1.0),
            confidence_score=clip_required(confidence_score),
            evidence=evidence,
            reason_codes=reason_codes,
            model_version=model_version or CALCULATION_VERSION,
            payload=event_payload,
        )

    def build_earnings_record(self, snapshot: EarningsSnapshot, event_id: str) -> EarningsDividendRecord:
        return EarningsDividendRecord(
            earnings_dividend_record_id=snapshot.earnings_snapshot_id,
            instrument_id=snapshot.instrument_id,
            event_id=event_id,
            record_type="earnings_snapshot",
            as_of_ts=snapshot.as_of_ts,
            payload=snapshot.to_dict(),
            source_module=self.module_name,
            calculation_version=CALCULATION_VERSION,
        )

    def build_dividend_record(self, snapshot: DividendSnapshot, event_id: str) -> EarningsDividendRecord:
        return EarningsDividendRecord(
            earnings_dividend_record_id=snapshot.dividend_snapshot_id,
            instrument_id=snapshot.instrument_id,
            event_id=event_id,
            record_type="dividend_snapshot",
            as_of_ts=snapshot.as_of_ts,
            payload=snapshot.to_dict(),
            source_module=self.module_name,
            calculation_version=CALCULATION_VERSION,
        )

    def create_external_requests_for_missing_data(
        self,
        *,
        module_input: EarningsDividendInput,
        job: ModuleJob,
        raw_text_items: tuple[RawTextItem, ...],
        structured_events: tuple[StructuredEvent, ...],
        candles: tuple[RawCandle, ...],
        gap_records: tuple[HistoricalGapRecord, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if not raw_text_items and not structured_events:
            requests.append(
                self._external_request(
                    job=job,
                    module_input=module_input,
                    provider="issuer_disclosure",
                    request_type="text_fetch",
                    suffix="issuer_disclosure",
                )
            )
        if not candles or not gap_records:
            for instrument_id in module_input.instrument_ids:
                requests.append(self._moex_market_data_request(job, instrument_id, "dividend_gap_history"))
        return tuple(requests)

    def create_llm_extraction_request(
        self,
        raw_item: RawTextItem,
        module_input: EarningsDividendInput,
        job: ModuleJob,
    ) -> ExternalRequest:
        source_payload = {
            "raw_text_item_id": raw_item.raw_text_item_id,
            "source": raw_item.source,
            "source_url": raw_item.source_url,
            "title": raw_item.title,
            "body": raw_item.body,
            "published_at": raw_item.published_at,
            "instrument_ids": list(raw_item.instrument_ids or module_input.instrument_ids),
        }
        prompt_payload = {
            "task": "extract earnings/dividend facts for feature calculation",
            "strict_rules": {
                "facts_and_scores_separated": True,
                "surprise_requires_baseline": True,
                "dividend_dates_explicit": True,
                "source_refs_required": True,
                "llm_evidence_required": True,
                "do_not_output_trading_recommendations": True,
                "allowed_instrument_ids": list(module_input.instrument_ids),
                "llm_prompt_version": module_input.llm_prompt_version,
            },
            "output_schema": LLM_OUTPUT_SCHEMA_DESCRIPTION,
            "input": source_payload,
        }
        task_type = self.llm_task_type(raw_item)
        content_hash = stable_record_id(
            "raw_text_content",
            {
                "raw_text_item_id": raw_item.raw_text_item_id,
                "title": raw_item.title,
                "body": raw_item.body,
                "source_url": raw_item.source_url,
            },
        )
        idempotency_key = ":".join(
            (
                job.idempotency_key,
                "llm_extraction",
                raw_item.raw_text_item_id,
                content_hash,
                task_type,
                module_input.llm_prompt_version,
                self.model_id,
            )
        )
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="polza_ai",
            request_type="llm_completion",
            universe_id=job.universe_id,
            instrument_ids=module_input.instrument_ids,
            payload={
                "model": self.model_id,
                "model_id": self.model_id,
                "task_type": task_type,
                "prompt_version": module_input.llm_prompt_version,
                "content_hash": content_hash,
                "messages": [
                    {"role": "system", "content": REPORT_EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 2000,
                "reasoning": {"enabled": True, "effort": "medium", "summary": "auto"},
                "llm_prompt_version": module_input.llm_prompt_version,
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
        text = " ".join((raw_item.source, raw_item.title or "", raw_item.body[:500] if raw_item.body else "")).lower()
        dividend_markers = (
            "\u0434\u0438\u0432\u0438\u0434\u0435\u043d\u0434",
            "\u0434\u0438\u0432\u0438\u0434\u0435\u043d\u0434\u044b",
            "\u0441\u043e\u0432\u0435\u0442 \u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440\u043e\u0432",
            "\u0441\u043e\u0431\u0440\u0430\u043d\u0438\u0435 \u0430\u043a\u0446\u0438\u043e\u043d\u0435\u0440\u043e\u0432",
            "record date",
            "dividend",
        )
        report_markers = (
            "\u043e\u0442\u0447\u0435\u0442",
            "\u043e\u0442\u0447\u0451\u0442",
            "\u043c\u0441\u0444\u043e",
            "\u0440\u0441\u0431\u0443",
            "\u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u044b\u0435 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b",
            "\u043e\u043f\u0435\u0440\u0430\u0446\u0438\u043e\u043d\u043d\u044b\u0435 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b",
            "\u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439 \u0444\u0430\u043a\u0442",
            "report",
            "ifrs",
            "rsbu",
            "msfo",
            "financial results",
            "operating results",
        )
        if any(marker in text for marker in dividend_markers):
            return "dividend_extraction"
        if any(marker in text for marker in report_markers):
            return "report_extraction"
        return "report_extraction"
    def write_structured_event(self, event: StructuredEvent) -> str:
        return self.repository.save_structured_event(event)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_earnings_dividend_record(self, record: EarningsDividendRecord) -> str:
        return self.repository.save_earnings_dividend_record(record)

    def _external_request(
        self,
        *,
        job: ModuleJob,
        module_input: EarningsDividendInput,
        provider: str,
        request_type: str,
        suffix: str,
    ) -> ExternalRequest:
        idempotency_key = f"{job.idempotency_key}:{suffix}"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider=provider,
            request_type=request_type,
            universe_id=job.universe_id,
            instrument_ids=module_input.instrument_ids,
            payload={
                "report_refs": list(module_input.report_refs),
                "dividend_event_refs": list(module_input.dividend_event_refs),
                "financial_expectation_ref": module_input.financial_expectation_ref,
                "historical_gap_ref": module_input.historical_gap_ref,
                "time_range": job.time_range.to_dict(),
                "gateway_only": True,
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=MARKET_PRICE_TTL_SECONDS, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _moex_market_data_request(self, job: ModuleJob, instrument_id: str, suffix: str) -> ExternalRequest:
        secid = _strip_moex_prefix(instrument_id)
        idempotency_key = f"{job.idempotency_key}:{suffix}:{instrument_id}:TQBR:1d"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="market_data",
            universe_id=job.universe_id,
            instrument_ids=(instrument_id,),
            payload={
                "secid": secid,
                "board_id": "TQBR",
                "timeframe": "1d",
                "timeframes": ["1d"],
                "dividend_gap_ref": "dividend_gap_history",
                "time_range": job.time_range.to_dict(),
                "gateway_only": True,
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=MARKET_PRICE_TTL_SECONDS, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _gateway_process(self, request: ExternalRequest) -> Any:
        if hasattr(self.gateway, "process"):
            return self.gateway.process(request)
        if hasattr(self.gateway, "execute"):
            result = self.gateway.execute(request)
            return getattr(result, "response", result)
        if callable(self.gateway):
            return self.gateway(request)
        raise EarningsDividendIntelligenceError("gateway does not expose process/execute")

    def _assert_no_forbidden_fields(self, payload: Mapping[str, Any]) -> None:
        for key, value in payload.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_LLM_FIELDS:
                raise EarningsDividendIntelligenceError(f"forbidden LLM field: {key}")
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

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> EarningsDividendExecutionResult:
        return EarningsDividendExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
                events_written=0,
            ),
            structured_events=(),
            feature_records=(),
            earnings_snapshots=(),
            dividend_snapshots=(),
            structured_event_refs=(),
            feature_record_refs=(),
            earnings_dividend_record_refs=(),
        )


def _financial_fact_from_payload(
    payload: Mapping[str, Any],
    *,
    source_refs: tuple[str, ...],
    instrument_fallback: str,
    as_of_ts: str,
    confidence_score: float,
    evidence: tuple[str, ...],
    reason_codes: tuple[str, ...],
    model_version: str,
) -> FinancialFactSet | None:
    fields = _merged_fields(payload, "financial_facts", "financial_statement", "earnings", "report", "fields", "metrics")
    if not _item_has_financial_facts(fields):
        return None
    instrument_id = str(payload.get("instrument_id") or fields.get("instrument_id") or instrument_fallback)
    period = str(payload.get("period") or fields.get("period") or fields.get("report_period") or "")
    if not instrument_id or not period or not source_refs:
        return None
    return FinancialFactSet(
        instrument_id=instrument_id,
        period=period,
        fields=fields,
        source_refs=source_refs,
        as_of_ts=as_of_ts,
        confidence_score=clip_required(float(confidence_score)),
        evidence=evidence,
        reason_codes=reason_codes,
        model_version=model_version or CALCULATION_VERSION,
    )


def _dividend_fact_from_payload(
    payload: Mapping[str, Any],
    *,
    source_refs: tuple[str, ...],
    instrument_fallback: str,
    as_of_ts: str,
    confidence_score: float,
    evidence: tuple[str, ...],
    reason_codes: tuple[str, ...],
    model_version: str,
) -> DividendFactSet | None:
    fields = _merged_fields(payload, "dividend_terms", "dividend", "dividend_facts", "fields", "metrics")
    if not _item_has_dividend_terms(fields):
        return None
    instrument_id = str(payload.get("instrument_id") or fields.get("instrument_id") or instrument_fallback)
    if not instrument_id or not source_refs:
        return None
    return DividendFactSet(
        instrument_id=instrument_id,
        fields=fields,
        source_refs=source_refs,
        as_of_ts=as_of_ts,
        confidence_score=clip_required(float(confidence_score)),
        evidence=evidence,
        reason_codes=reason_codes,
        model_version=model_version or CALCULATION_VERSION,
    )


def _item_has_financial_facts(payload: Mapping[str, Any]) -> bool:
    fields = _merged_fields(payload, "financial_facts", "financial_statement", "earnings", "report", "fields", "metrics")
    return any(
        _field_float(fields, key) is not None
        for key in (
            "revenue_actual",
            "revenue",
            "ebitda_actual",
            "ebitda",
            "net_income_actual",
            "net_income",
            "fcf_actual",
            "free_cash_flow",
            "margin_actual",
            "net_debt_actual",
            "capex_actual",
        )
    )


def _item_has_dividend_terms(payload: Mapping[str, Any]) -> bool:
    fields = _merged_fields(payload, "dividend_terms", "dividend", "dividend_facts", "fields", "metrics")
    return any(
        key in fields and fields.get(key) not in (None, "")
        for key in (
            "expected_dividend_per_share",
            "expected_dividend",
            "announced_dividend",
            "recommended_dividend",
            "dividend_per_share",
            "record_date",
            "expected_record_date",
            "dividend_probability",
            "payout_ratio",
        )
    )


def _merged_fields(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    fields: dict[str, Any] = {}
    for key in keys:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            fields.update(nested)
    fields.update(payload)
    return fields


def _embedded_llm_payload(raw_item: RawTextItem) -> Mapping[str, Any] | str | None:
    payload = raw_item.source_payload
    for key in ("llm_output", "llm_envelope", "report_extraction", "dividend_extraction", "polza_ai_response", "llm_response"):
        value = payload.get(key)
        if value:
            extracted = _maybe_nested_llm_payload(value)
            if extracted is not None:
                return extracted
    if all(key in payload for key in ("schema_version", "model_id", "model_version", "task_type")):
        return payload
    return None


def _maybe_nested_llm_payload(value: Any) -> Mapping[str, Any] | str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return None
    if all(key in value for key in ("schema_version", "model_id", "model_version", "task_type")):
        return value
    for key in ("data", "content", "output", "result"):
        if key in value:
            nested = _maybe_nested_llm_payload(value[key])
            if nested is not None:
                return nested
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
        raise EarningsDividendIntelligenceError("LLM output is not valid JSON") from error
    if not isinstance(parsed, Mapping):
        raise EarningsDividendIntelligenceError("LLM output JSON root must be an object")
    return parsed


def _dedupe_report_facts(facts: list[FinancialFactSet]) -> tuple[FinancialFactSet, ...]:
    by_key: dict[tuple[str, str, tuple[str, ...]], FinancialFactSet] = {}
    for fact in facts:
        by_key[(fact.instrument_id, fact.period, fact.source_refs)] = fact
    return tuple(by_key.values())


def _dedupe_dividend_facts(facts: list[DividendFactSet]) -> tuple[DividendFactSet, ...]:
    by_key: dict[tuple[str, str, tuple[str, ...]], DividendFactSet] = {}
    for fact in facts:
        record_date = str(fact.fields.get("record_date") or fact.fields.get("expected_record_date") or "")
        by_key[(fact.instrument_id, record_date, fact.source_refs)] = fact
    return tuple(by_key.values())


def _latest_price(candles: tuple[RawCandle, ...]) -> RawCandle | None:
    priced = [candle for candle in candles if candle.close_price is not None]
    if not priced:
        return None
    return sorted(priced, key=lambda item: item.close_ts or item.open_ts)[-1]


def _refs_with_prefix(refs: tuple[str, ...], prefix: str) -> tuple[str, ...]:
    return tuple(ref for ref in refs if str(ref).startswith(prefix))


def _field_float(fields: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(fields.get(key))
        if value is not None:
            return value
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _safe_polza_model(model_id: str | None, fallback: str) -> str:
    candidate = str(model_id or "").strip()
    if not candidate or candidate in PROHIBITED_POLZA_MODELS:
        return fallback
    return candidate


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _llm_text_runtime_enabled(job: ModuleJob) -> bool:
    if not _env_bool("LLM_ENABLED", True):
        return False
    if job.run_mode == "live_trading":
        return _env_bool("ENABLE_LLM_TEXT_SCHEDULES", False)
    return True


def _coerce_timestamp(value: Any, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    text = str(value)
    try:
        parse_utc_iso(text)
    except Exception:
        return fallback
    return text


def _record_date_ts(value: Any) -> str | None:
    parsed = parse_date(value)
    if parsed is None:
        return None
    return f"{parsed.isoformat()}T00:00:00Z"


def _negative(value: float | None) -> float | None:
    return None if value is None else -float(value)


def _abs_or_none(value: float | None) -> float | None:
    return None if value is None else abs(float(value))


def _metric_confidence(base: float, quality_flags: tuple[str, ...]) -> float:
    confidence = clip_required(base)
    if "missing_expectation_baseline" in quality_flags:
        confidence = min(confidence, 0.6)
    if "dividend_record_date_missing" in quality_flags:
        confidence = min(confidence, 0.7)
    return confidence


def _snapshot_confidence(metrics: tuple[MetricValue, ...] | list[MetricValue], quality_flags: tuple[str, ...]) -> float:
    if not metrics:
        return 0.0
    metric_confidence = sum(_metric_confidence(metric.confidence_score, metric.quality_flags) for metric in metrics) / len(metrics)
    if quality_flags:
        metric_confidence = min(metric_confidence, 0.75)
    return clip_required(metric_confidence)
