from __future__ import annotations

import json
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
    additional_supply_risk_score,
    adjustment_factor_from_prices,
    buyback_intensity,
    clip,
    clip_required,
    corporate_action_pressure_score,
    dividend_adjustment_factor,
    free_float_change,
    parse_date,
    signed_to_unit,
    split_adjustment_factor,
)
from .repository import (
    CorporateActionRecord,
    CorporateActionsAdjustmentRepository,
    FeatureRecord,
    InMemoryCorporateActionsAdjustmentRepository,
    InstrumentMappingUpdate,
    InstrumentProfile,
    InstrumentStatusUpdate,
    RawCandle,
    StructuredEvent,
    stable_record_id,
    stable_uuid_id,
)


MODULE_NAME = "Corporate Actions Adjustment Module"
CALCULATION_VERSION = "corporate_actions_adjustment_v1"

VALID_CONTOURS = {"event_contour", "daily_contour"}
VALID_HORIZONS = {"swing", "position"}
VALID_ACTION_TYPES = {
    "dividend",
    "split",
    "consolidation",
    "ticker_change",
    "additional_issue",
    "buyback",
    "delisting",
    "halt",
    "other",
}
PRICE_ADJUSTING_ACTION_TYPES = {"dividend", "split", "consolidation"}
VALID_ADJUSTMENT_POLICIES = {"total_return", "price_only", "custom"}
INPUT_FIELDS = {
    "instrument_ids",
    "corporate_action_refs",
    "price_series_ref",
    "instrument_profile_ref",
    "adjustment_policy",
}
PERMANENT_TTL_SECONDS = 0
DEFAULT_PRESSURE_WEIGHTS = {
    "buyback_intensity": 1.0,
    "additional_supply_risk_score": 1.0,
    "tradability_change_impact": 1.0,
}
AFFECTED_HISTORICAL_RETURN_FEATURES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "log_return",
    "momentum_5d",
    "momentum_20d",
    "trend_slope",
    "trend_t_stat",
    "realized_vol_5d",
    "realized_vol_20d",
    "realized_vol_60d",
    "market_beta",
    "historical_gap_size",
    "expected_gap_risk",
)


class CorporateActionsAdjustmentError(ValueError):
    """Raised when module 13 violates its documented contract."""


@dataclass(frozen=True)
class CorporateActionsInput:
    instrument_ids: tuple[str, ...]
    corporate_action_refs: tuple[str, ...]
    price_series_ref: str
    instrument_profile_ref: str
    adjustment_policy: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "CorporateActionsInput":
        input_payload = payload.get("corporate_actions_input")
        if not isinstance(input_payload, Mapping):
            raise CorporateActionsAdjustmentError("payload must contain corporate_actions_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise CorporateActionsAdjustmentError(f"corporate_actions_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise CorporateActionsAdjustmentError(f"corporate_actions_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise CorporateActionsAdjustmentError("corporate_actions_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise CorporateActionsAdjustmentError("corporate_actions_input.instrument_ids must match module_job.instrument_ids")

        adjustment_policy = str(input_payload.get("adjustment_policy") or "")
        if adjustment_policy not in VALID_ADJUSTMENT_POLICIES:
            raise CorporateActionsAdjustmentError("adjustment_policy must be total_return, price_only or custom")

        return cls(
            instrument_ids=instrument_ids,
            corporate_action_refs=tuple(str(item) for item in (input_payload.get("corporate_action_refs") or ())),
            price_series_ref=str(input_payload.get("price_series_ref") or ""),
            instrument_profile_ref=str(input_payload.get("instrument_profile_ref") or ""),
            adjustment_policy=adjustment_policy,
        )


CorporateActionsAdjustmentInput = CorporateActionsInput


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_type: str
    raw_value: float | None
    normalized_value: float | None
    unit: str
    source_refs: tuple[str, ...]
    quality_flags: tuple[str, ...] = ()
    confidence_score: float = 1.0
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecomputeTrigger:
    trigger_id: str
    instrument_id: str
    corporate_action_id: str
    affected_feature_names: tuple[str, ...]
    status: str
    reason_codes: tuple[str, ...]
    source_refs: tuple[str, ...]
    created_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "instrument_id": self.instrument_id,
            "corporate_action_id": self.corporate_action_id,
            "affected_feature_names": list(self.affected_feature_names),
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "source_refs": list(self.source_refs),
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class CorporateActionsExecutionResult:
    module_job_result: ModuleJobResult
    corporate_action_records: tuple[CorporateActionRecord, ...]
    feature_records: tuple[FeatureRecord, ...]
    adjusted_price_series_refs: tuple[str, ...]
    instrument_mapping_updates: tuple[InstrumentMappingUpdate, ...]
    instrument_status_updates: tuple[InstrumentStatusUpdate, ...]
    recompute_triggers: tuple[RecomputeTrigger, ...]
    corporate_action_record_refs: tuple[str, ...]
    feature_record_refs: tuple[str, ...]
    instrument_mapping_update_refs: tuple[str, ...]
    instrument_status_update_refs: tuple[str, ...]
    recompute_trigger_refs: tuple[str, ...]

    @property
    def corporate_action_record(self) -> CorporateActionRecord | None:
        return self.corporate_action_records[0] if len(self.corporate_action_records) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "corporate_action_records": [record.to_dict() for record in self.corporate_action_records],
            "feature_records": [record.to_dict() for record in self.feature_records],
            "adjusted_price_series_refs": list(self.adjusted_price_series_refs),
            "instrument_mapping_updates": [update.to_dict() for update in self.instrument_mapping_updates],
            "instrument_status_updates": [update.to_dict() for update in self.instrument_status_updates],
            "recompute_triggers": [trigger.to_dict() for trigger in self.recompute_triggers],
            "corporate_action_record_refs": list(self.corporate_action_record_refs),
            "feature_record_refs": list(self.feature_record_refs),
            "instrument_mapping_update_refs": list(self.instrument_mapping_update_refs),
            "instrument_status_update_refs": list(self.instrument_status_update_refs),
            "recompute_trigger_refs": list(self.recompute_trigger_refs),
        }
        if len(self.corporate_action_records) == 1:
            payload["corporate_action_record"] = self.corporate_action_records[0].to_dict()
        return payload


class CorporateActionsAdjustmentService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: CorporateActionsAdjustmentRepository | None = None,
        gateway: Any | None = None,
        pressure_weights: Mapping[str, float] | None = None,
    ) -> None:
        self.repository = repository or InMemoryCorporateActionsAdjustmentRepository()
        self.gateway = gateway
        self.pressure_weights = dict(pressure_weights or DEFAULT_PRESSURE_WEIGHTS)

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> CorporateActionsExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> CorporateActionsExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> CorporateActionsExecutionResult:
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
            return self._empty_execution_result(result)

        try:
            self.validate_module_job(job)
            module_input = CorporateActionsInput.from_dict(payload, job)
            events, profiles, candles = self.load_inputs(module_input, job)

            warnings: list[str] = []
            external_requests = self.create_external_requests_for_missing_data(
                module_input=module_input,
                job=job,
                events=events,
                profiles=profiles,
                candles=candles,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self._gateway_process(request)
                warnings.append(f"external_request_created:{request.request_id}")

            if not events:
                warnings.append("corporate_action_events_missing")
                return self._empty_result(job, started_at, "skipped", tuple(dict.fromkeys(warnings)), ())

            profile_by_id = {profile.instrument_id: profile for profile in profiles}
            missing_profiles = tuple(item for item in module_input.instrument_ids if item not in profile_by_id)
            warnings.extend(f"instrument_profile_missing:{instrument_id}" for instrument_id in missing_profiles)

            corporate_action_records: list[CorporateActionRecord] = []
            feature_records: list[FeatureRecord] = []
            adjusted_price_series_refs: list[str] = []
            mapping_updates: list[InstrumentMappingUpdate] = []
            status_updates: list[InstrumentStatusUpdate] = []
            recompute_triggers: list[RecomputeTrigger] = []

            for event in events:
                event_instruments = self.event_instrument_ids(event, module_input.instrument_ids)
                if not event_instruments:
                    warnings.append(f"corporate_action_outside_requested_universe:{event.event_id}")
                    continue

                action_type = self.classify_corporate_action(event)
                effective_date = self.validate_effective_date(event)
                if effective_date is None:
                    warnings.append(f"effective_date_required:{event.event_id}")
                    continue

                for instrument_id in event_instruments:
                    profile = profile_by_id.get(instrument_id)
                    instrument_candles = tuple(candle for candle in candles if candle.instrument_id == instrument_id)
                    source_refs = self.source_refs(event, module_input)
                    adjustment_factor = self.compute_adjustment_factor(
                        action_type=action_type,
                        event=event,
                        profile=profile,
                        candles=instrument_candles,
                        effective_date=effective_date,
                        adjustment_policy=module_input.adjustment_policy,
                    )
                    requires_recompute = self.requires_recompute(action_type, adjustment_factor)
                    factor_missing = action_type in PRICE_ADJUSTING_ACTION_TYPES and adjustment_factor is None
                    if factor_missing:
                        warnings.append(f"adjustment_factor_missing:{instrument_id}:{event.event_id}")

                    corporate_action_id = stable_uuid_id(
                        {
                            "event_id": event.event_id,
                            "instrument_id": instrument_id,
                            "action_type": action_type,
                            "effective_date": effective_date,
                            "calculation_version": CALCULATION_VERSION,
                        }
                    )
                    adjusted_ref = self.adjusted_price_series_ref(instrument_id, corporate_action_id)
                    adjusted_candles = self.build_adjusted_price_series(
                        candles=instrument_candles,
                        effective_date=effective_date,
                        adjustment_factor=adjustment_factor,
                        corporate_action_id=corporate_action_id,
                        adjustment_policy=module_input.adjustment_policy,
                    )
                    if adjusted_candles:
                        adjusted_price_series_refs.append(self.repository.save_adjusted_candles(adjusted_candles, adjusted_ref))
                    elif action_type in PRICE_ADJUSTING_ACTION_TYPES and adjustment_factor is not None:
                        warnings.append(f"no_pre_effective_candles_to_adjust:{instrument_id}:{event.event_id}")

                    mapping_update = self.build_instrument_mapping_update(
                        action_type=action_type,
                        event=event,
                        profile=profile,
                        instrument_id=instrument_id,
                        effective_date=effective_date,
                        source_refs=source_refs,
                    )
                    if mapping_update is not None:
                        mapping_updates.append(mapping_update)

                    status_update = self.build_instrument_status_update(
                        action_type=action_type,
                        event=event,
                        profile=profile,
                        instrument_id=instrument_id,
                        effective_date=effective_date,
                        source_refs=source_refs,
                    )
                    if status_update is not None:
                        status_updates.append(status_update)

                    metric_values = self.compute_metric_values(
                        action_type=action_type,
                        event=event,
                        profile=profile,
                        instrument_id=instrument_id,
                        adjustment_factor=adjustment_factor,
                        source_refs=source_refs,
                        factor_missing=factor_missing,
                        status_update=status_update,
                    )
                    feature_records.extend(
                        self.build_feature_records(
                            metric_values=metric_values,
                            instrument_id=instrument_id,
                            job=job,
                            timestamp=event.event_ts,
                            horizons=self.output_horizons(job),
                        )
                    )

                    record_payload = {
                        "event": event.to_dict(),
                        "adjustment_policy": module_input.adjustment_policy,
                        "adjusted_price_series_ref": adjusted_ref if adjusted_candles else None,
                        "requires_recompute": requires_recompute,
                        "quality_flags": ["adjustment_factor_missing"] if factor_missing else [],
                        "instrument_mapping_update": mapping_update.to_dict() if mapping_update else None,
                        "instrument_status_update": status_update.to_dict() if status_update else None,
                    }
                    corporate_action_records.append(
                        CorporateActionRecord(
                            corporate_action_id=corporate_action_id,
                            instrument_id=instrument_id,
                            action_type=action_type,
                            effective_date=effective_date,
                            adjustment_factor=adjustment_factor,
                            source_refs=source_refs,
                            confidence_score=clip_required(event.confidence_score),
                            requires_recompute=requires_recompute,
                            event_id=event.event_id,
                            payload=record_payload,
                            source_module=self.module_name,
                            calculation_version=CALCULATION_VERSION,
                        )
                    )

                    if requires_recompute:
                        recompute_triggers.append(
                            self.emit_downstream_recompute_trigger(
                                instrument_id=instrument_id,
                                corporate_action_id=corporate_action_id,
                                source_refs=source_refs,
                                blocked=factor_missing,
                                payload={
                                    "action_type": action_type,
                                    "effective_date": effective_date,
                                    "adjustment_policy": module_input.adjustment_policy,
                                },
                            )
                        )

            corporate_refs = tuple(self.write_corporate_action_record(record) for record in corporate_action_records)
            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            mapping_refs = tuple(self.write_instrument_mapping_update(update) for update in mapping_updates)
            status_refs = tuple(self.write_instrument_status_update(update) for update in status_updates)
            recompute_refs = tuple(f"recompute_trigger:{trigger.trigger_id}" for trigger in recompute_triggers)
            output_refs = corporate_refs + feature_refs + tuple(adjusted_price_series_refs) + mapping_refs + status_refs + recompute_refs

            if not corporate_action_records:
                warnings.append("corporate_action_records_not_written")
            status = "success" if corporate_action_records and not warnings else "partial_success" if output_refs else "skipped"

            return CorporateActionsExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(feature_records),
                    events_written=len(corporate_action_records),
                ),
                corporate_action_records=tuple(corporate_action_records),
                feature_records=tuple(feature_records),
                adjusted_price_series_refs=tuple(adjusted_price_series_refs),
                instrument_mapping_updates=tuple(mapping_updates),
                instrument_status_updates=tuple(status_updates),
                recompute_triggers=tuple(recompute_triggers),
                corporate_action_record_refs=corporate_refs,
                feature_record_refs=feature_refs,
                instrument_mapping_update_refs=mapping_refs,
                instrument_status_update_refs=status_refs,
                recompute_trigger_refs=recompute_refs,
            )
        except (CorporateActionsAdjustmentError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise CorporateActionsAdjustmentError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise CorporateActionsAdjustmentError("module_job.module_name must be Corporate Actions Adjustment Module")
        if job.contour not in VALID_CONTOURS:
            raise CorporateActionsAdjustmentError("module_job.contour must be event_contour or daily_contour")
        if not job.instrument_ids:
            raise CorporateActionsAdjustmentError("module_job.instrument_ids is required")
        if not job.input_refs:
            raise CorporateActionsAdjustmentError("module_job.input_refs is required")
        if not job.run_mode:
            raise CorporateActionsAdjustmentError("module_job.run_mode is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise CorporateActionsAdjustmentError(f"Corporate Actions Adjustment Module supports swing/position horizons: {invalid_horizons}")

    def load_inputs(
        self,
        module_input: CorporateActionsInput,
        job: ModuleJob,
    ) -> tuple[tuple[StructuredEvent, ...], tuple[InstrumentProfile, ...], tuple[RawCandle, ...]]:
        event_refs = tuple(
            dict.fromkeys(
                (
                    *module_input.corporate_action_refs,
                    *_refs_with_prefix(job.input_refs, "events.structured_event"),
                )
            )
        )
        events = self.repository.list_structured_events(
            event_refs,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        profiles = self.repository.list_instrument_profiles(
            module_input.instrument_profile_ref,
            job.universe_id,
            module_input.instrument_ids,
        )
        candles = self.repository.list_candles(
            module_input.price_series_ref,
            job.universe_id,
            module_input.instrument_ids,
            job.time_range.from_ts,
            job.time_range.to_ts,
        )
        return events, profiles, candles

    def create_external_requests_for_missing_data(
        self,
        *,
        module_input: CorporateActionsInput,
        job: ModuleJob,
        events: tuple[StructuredEvent, ...],
        profiles: tuple[InstrumentProfile, ...],
        candles: tuple[RawCandle, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if not events:
            requests.append(self._external_request(job, module_input, "issuer_disclosure", "text_fetch", "corporate_action_disclosures"))
        if len(profiles) < len(module_input.instrument_ids):
            known = {profile.instrument_id for profile in profiles}
            for instrument_id in module_input.instrument_ids:
                if instrument_id not in known:
                    requests.append(self._moex_instrument_request(job, instrument_id, "instrument_metadata"))
        if not candles:
            for profile in profiles:
                requests.append(self._moex_market_data_request(job, profile, "historical_prices"))
        if any(self.classify_corporate_action(event) in {"halt", "delisting", "ticker_change"} for event in events):
            for profile in profiles:
                requests.append(self._moex_instrument_request(job, profile.instrument_id, "trading_status"))
        return tuple(requests)

    def classify_corporate_action(self, event: StructuredEvent) -> str:
        payload_action = _text_field(event.payload, "action_type", "corporate_action_type", "type")
        candidate_text = " ".join(
            value.lower()
            for value in (payload_action, event.event_type, event.event_subtype, json.dumps(event.payload, sort_keys=True, default=str))
            if value
        )
        if event.event_type == "dividend" or "dividend" in candidate_text:
            return "dividend"
        if any(token in candidate_text for token in ("ticker_change", "ticker change", "symbol_change", "rename")):
            return "ticker_change"
        if any(token in candidate_text for token in ("additional_issue", "additional issue", "secondary offering", "new shares")):
            return "additional_issue"
        if "buyback" in candidate_text or "repurchase" in candidate_text:
            return "buyback"
        if "delisting" in candidate_text or "delist" in candidate_text:
            return "delisting"
        if "halt" in candidate_text or "suspension" in candidate_text or "suspended" in candidate_text:
            return "halt"
        if "consolidation" in candidate_text or "reverse split" in candidate_text:
            return "consolidation"
        if "split" in candidate_text:
            return "split"
        if payload_action in VALID_ACTION_TYPES:
            return payload_action
        return "other"

    def validate_effective_date(self, event: StructuredEvent) -> str | None:
        for key in (
            "effective_date",
            "ex_dividend_date",
            "ex_date",
            "record_date",
            "halt_date",
            "delisting_date",
            "action_date",
            "date",
        ):
            parsed = parse_date(event.payload.get(key))
            if parsed is not None:
                return parsed.isoformat()
        parsed = parse_date(event.event_ts)
        return parsed.isoformat() if parsed is not None else None

    def compute_adjustment_factor(
        self,
        *,
        action_type: str,
        event: StructuredEvent,
        profile: InstrumentProfile | None,
        candles: tuple[RawCandle, ...],
        effective_date: str,
        adjustment_policy: str,
    ) -> float | None:
        explicit = _first_float(
            event.payload,
            "adjustment_factor",
            "exchange_adjustment_factor",
            "provider_adjustment_factor",
            "factor",
        )
        if explicit is not None and explicit > 0:
            return explicit

        internal = adjustment_factor_from_prices(
            _first_float(event.payload, "adjusted_price", "adjusted_close_price"),
            _first_float(event.payload, "raw_price", "raw_close_price"),
        )
        if internal is not None:
            return internal

        if adjustment_policy == "custom":
            return None

        if action_type == "dividend":
            if adjustment_policy == "price_only":
                return 1.0
            dividend = _first_float(
                event.payload,
                "dividend_per_share",
                "announced_dividend",
                "recommended_dividend",
                "dividend",
            )
            reference_price = _first_float(
                event.payload,
                "reference_price",
                "previous_close",
                "close_before",
                "pre_event_close",
            )
            if reference_price is None:
                reference_price = _latest_close_before(candles, effective_date)
            return dividend_adjustment_factor(dividend, reference_price)

        if action_type == "split":
            return split_adjustment_factor(_ratio_new_to_old(event.payload, default_numeric_mode="new_to_old"))
        if action_type == "consolidation":
            ratio = _ratio_new_to_old(event.payload, default_numeric_mode="old_to_new")
            return split_adjustment_factor(ratio)

        del profile
        return None

    def requires_recompute(self, action_type: str, adjustment_factor: float | None) -> bool:
        if action_type in PRICE_ADJUSTING_ACTION_TYPES:
            return True
        if action_type in {"ticker_change", "additional_issue", "buyback", "delisting", "halt"}:
            return True
        return adjustment_factor is not None

    def build_adjusted_price_series(
        self,
        *,
        candles: tuple[RawCandle, ...],
        effective_date: str,
        adjustment_factor: float | None,
        corporate_action_id: str,
        adjustment_policy: str,
    ) -> tuple[RawCandle, ...]:
        if adjustment_factor is None or adjustment_factor <= 0:
            return ()
        effective = parse_date(effective_date)
        if effective is None:
            return ()
        adjusted: list[RawCandle] = []
        for candle in candles:
            candle_date = parse_date(candle.close_ts or candle.open_ts)
            if candle_date is None or candle_date >= effective:
                continue
            adjusted.append(
                candle.with_adjustment(
                    adjustment_factor=adjustment_factor,
                    corporate_action_id=corporate_action_id,
                    effective_date=effective_date,
                    calculation_version=CALCULATION_VERSION,
                    adjustment_policy=adjustment_policy,
                )
            )
        return tuple(adjusted)

    def build_instrument_mapping_update(
        self,
        *,
        action_type: str,
        event: StructuredEvent,
        profile: InstrumentProfile | None,
        instrument_id: str,
        effective_date: str,
        source_refs: tuple[str, ...],
    ) -> InstrumentMappingUpdate | None:
        if action_type != "ticker_change":
            return None
        provider_symbol = _text_field(event.payload, "new_ticker", "new_symbol", "provider_symbol", "ticker_after")
        if not provider_symbol:
            return None
        provider = _text_field(event.payload, "provider") or "moex_iss"
        previous_symbol = _text_field(event.payload, "old_ticker", "old_symbol", "ticker_before") or (profile.ticker if profile else None)
        update_id = stable_record_id(
            "instrument_mapping_update",
            {
                "instrument_id": instrument_id,
                "provider": provider,
                "provider_symbol": provider_symbol,
                "effective_date": effective_date,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        return InstrumentMappingUpdate(
            mapping_update_id=update_id,
            instrument_id=instrument_id,
            provider=provider,
            provider_symbol=provider_symbol,
            previous_provider_symbol=previous_symbol,
            effective_date=effective_date,
            source_refs=source_refs,
            payload={
                "instrument_mapping_versioned": True,
                "event_id": event.event_id,
                "calculation_version": CALCULATION_VERSION,
            },
        )

    def build_instrument_status_update(
        self,
        *,
        action_type: str,
        event: StructuredEvent,
        profile: InstrumentProfile | None,
        instrument_id: str,
        effective_date: str,
        source_refs: tuple[str, ...],
    ) -> InstrumentStatusUpdate | None:
        explicit = _optional_bool_field(event.payload, "new_tradable", "tradable_after", "tradable", "trading_allowed")
        if explicit is None and action_type in {"halt", "delisting"}:
            explicit = False
        if explicit is None:
            return None
        old_tradable = profile.tradable if profile is not None else None
        if old_tradable is not None and bool(old_tradable) == bool(explicit):
            return None
        update_id = stable_record_id(
            "instrument_status_update",
            {
                "instrument_id": instrument_id,
                "new_tradable": bool(explicit),
                "effective_date": effective_date,
                "event_id": event.event_id,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        return InstrumentStatusUpdate(
            status_update_id=update_id,
            instrument_id=instrument_id,
            old_tradable=old_tradable,
            new_tradable=bool(explicit),
            effective_date=effective_date,
            source_refs=source_refs,
            reason_codes=(f"corporate_action_{action_type}", "source_refs_required"),
            payload={
                "event_id": event.event_id,
                "source_module": self.module_name,
                "calculation_version": CALCULATION_VERSION,
            },
        )

    def compute_metric_values(
        self,
        *,
        action_type: str,
        event: StructuredEvent,
        profile: InstrumentProfile | None,
        instrument_id: str,
        adjustment_factor: float | None,
        source_refs: tuple[str, ...],
        factor_missing: bool,
        status_update: InstrumentStatusUpdate | None,
    ) -> tuple[MetricValue, ...]:
        values: list[MetricValue] = []
        quality_flags = ("adjustment_factor_missing", "historical_return_features_blocked") if factor_missing else ()
        if action_type in PRICE_ADJUSTING_ACTION_TYPES or adjustment_factor is not None:
            values.append(
                MetricValue(
                    metric_name="adjustment_factor",
                    metric_type="raw_metric",
                    raw_value=adjustment_factor,
                    normalized_value=None,
                    unit="factor",
                    source_refs=source_refs,
                    quality_flags=quality_flags,
                    confidence_score=_metric_confidence(event.confidence_score, quality_flags),
                    payload={"rule": "provider/exchange factor else adjusted_price / raw_price"},
                )
            )

        halt_flag = 1.0 if action_type == "halt" else 0.0
        if action_type in {"halt", "delisting"} or _has_any(event.payload, "halt_flag", "trading_halt"):
            values.append(
                MetricValue(
                    metric_name="halt_flag",
                    metric_type="raw_metric",
                    raw_value=halt_flag,
                    normalized_value=halt_flag,
                    unit="flag",
                    source_refs=source_refs,
                    confidence_score=clip_required(event.confidence_score),
                    payload={"rule": "1 if trading halt detected for instrument, else 0"},
                )
            )

        tradability_change_impact = None
        if status_update is not None:
            tradability_change_flag = 1.0
            if status_update.old_tradable is None:
                tradability_change_impact = -1.0 if not status_update.new_tradable else 1.0
            else:
                tradability_change_impact = 1.0 if status_update.new_tradable and not status_update.old_tradable else -1.0
        else:
            tradability_change_flag = 0.0
        if status_update is not None or action_type in {"halt", "delisting"}:
            values.append(
                MetricValue(
                    metric_name="tradability_change_flag",
                    metric_type="raw_metric",
                    raw_value=tradability_change_flag,
                    normalized_value=tradability_change_flag,
                    unit="flag",
                    source_refs=source_refs,
                    confidence_score=clip_required(event.confidence_score),
                    payload={"rule": "1 if instrument tradability changed, else 0"},
                )
            )

        buyback_value = _first_float(event.payload, "buyback_value_period", "buyback_value", "buyback_amount")
        free_float_market_cap = _first_float(event.payload, "free_float_market_cap")
        if free_float_market_cap is None and profile is not None:
            free_float_market_cap = profile.float_field("free_float_market_cap")
        buyback_metric = buyback_intensity(buyback_value, free_float_market_cap)
        if action_type == "buyback" or buyback_metric is not None:
            values.append(
                MetricValue(
                    metric_name="buyback_intensity",
                    metric_type="raw_metric",
                    raw_value=buyback_metric,
                    normalized_value=clip(buyback_metric),
                    unit="ratio",
                    source_refs=source_refs,
                    quality_flags=() if buyback_metric is not None else ("buyback_baseline_missing",),
                    confidence_score=clip_required(event.confidence_score),
                    payload={"formula": "buyback_value_period / free_float_market_cap"},
                )
            )

        free_float_current = _first_float(event.payload, "free_float_current", "free_float_after")
        free_float_previous = _first_float(event.payload, "free_float_previous", "free_float_before")
        if profile is not None:
            free_float_current = free_float_current if free_float_current is not None else profile.float_field("free_float_current")
            free_float_previous = free_float_previous if free_float_previous is not None else profile.float_field("free_float_previous")
        free_float_metric = free_float_change(free_float_current, free_float_previous)
        if free_float_metric is not None:
            values.append(
                MetricValue(
                    metric_name="free_float_change",
                    metric_type="raw_metric",
                    raw_value=free_float_metric,
                    normalized_value=signed_to_unit(free_float_metric, expected_abs_bound=0.2),
                    unit="ratio_diff",
                    source_refs=source_refs,
                    confidence_score=clip_required(event.confidence_score),
                    payload={"formula": "free_float_current - free_float_previous"},
                )
            )

        new_shares_expected = _first_float(event.payload, "new_shares_expected", "new_shares", "additional_shares")
        current_shares = _first_float(event.payload, "current_shares_outstanding", "shares_outstanding")
        if profile is not None:
            current_shares = current_shares if current_shares is not None else profile.float_field("current_shares_outstanding", "shares_outstanding")
        additional_supply_metric = additional_supply_risk_score(new_shares_expected, current_shares)
        if action_type == "additional_issue" or additional_supply_metric is not None:
            values.append(
                MetricValue(
                    metric_name="additional_supply_risk_score",
                    metric_type="derived_metric",
                    raw_value=additional_supply_metric,
                    normalized_value=clip(additional_supply_metric),
                    unit="ratio",
                    source_refs=source_refs,
                    quality_flags=() if additional_supply_metric is not None else ("additional_supply_baseline_missing",),
                    confidence_score=clip_required(event.confidence_score),
                    payload={"formula": "new_shares_expected / current_shares_outstanding"},
                )
            )

        pressure = corporate_action_pressure_score(
            buyback_metric,
            additional_supply_metric,
            tradability_change_impact,
            self.pressure_weights,
        )
        if any(value is not None for value in (buyback_metric, additional_supply_metric, tradability_change_impact)):
            values.append(
                MetricValue(
                    metric_name="corporate_action_pressure_score",
                    metric_type="composite_score",
                    raw_value=pressure,
                    normalized_value=signed_to_unit(pressure),
                    unit="score",
                    source_refs=source_refs,
                    quality_flags=() if pressure is not None else ("pressure_components_missing",),
                    confidence_score=clip_required(event.confidence_score),
                    payload={
                        "formula": "WAvg([buyback_intensity, -additional_supply_risk_score, tradability_change_impact], active_weights)",
                        "active_weights": dict(self.pressure_weights),
                        "instrument_id": instrument_id,
                    },
                )
            )

        return tuple(values)

    def build_feature_records(
        self,
        *,
        metric_values: tuple[MetricValue, ...],
        instrument_id: str,
        job: ModuleJob,
        timestamp: str,
        horizons: tuple[str, ...],
    ) -> tuple[FeatureRecord, ...]:
        records: list[FeatureRecord] = []
        metric_timestamp = _coerce_timestamp(timestamp, job.time_range.to_ts)
        for metric_value in metric_values:
            for horizon in horizons:
                feature_payload = {
                    "instrument_id": instrument_id,
                    "metric_name": metric_value.metric_name,
                    "horizon": horizon,
                    "timestamp": metric_timestamp,
                    "calculation_version": CALCULATION_VERSION,
                }
                records.append(
                    FeatureRecord(
                        feature_id=f"feature_{stable_record_id('feature', feature_payload).split('_', 1)[1]}",
                        instrument_id=instrument_id,
                        metric_name=metric_value.metric_name,
                        metric_group="event",
                        metric_type=metric_value.metric_type,
                        raw_value=metric_value.raw_value,
                        normalized_value=metric_value.normalized_value,
                        unit=metric_value.unit,
                        horizon=horizon,
                        contour=job.contour,
                        timestamp=metric_timestamp,
                        ttl_seconds=PERMANENT_TTL_SECONDS,
                        confidence_score=_metric_confidence(metric_value.confidence_score, metric_value.quality_flags),
                        source_module=self.module_name,
                        source_refs=metric_value.source_refs,
                        calculation_version=CALCULATION_VERSION,
                        quality_flags=metric_value.quality_flags,
                        payload={"calculation_version": CALCULATION_VERSION, **dict(metric_value.payload)},
                    )
                )
        return tuple(records)

    def emit_downstream_recompute_trigger(
        self,
        *,
        instrument_id: str,
        corporate_action_id: str,
        source_refs: tuple[str, ...],
        blocked: bool,
        payload: Mapping[str, Any],
    ) -> RecomputeTrigger:
        status = "blocked" if blocked else "pending"
        reason_codes = (
            ("affected_features_recompute_triggered", "adjustment_factor_missing")
            if blocked
            else ("affected_features_recompute_triggered",)
        )
        trigger_id = stable_record_id(
            "recompute",
            {
                "instrument_id": instrument_id,
                "corporate_action_id": corporate_action_id,
                "status": status,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        return RecomputeTrigger(
            trigger_id=trigger_id,
            instrument_id=instrument_id,
            corporate_action_id=corporate_action_id,
            affected_feature_names=AFFECTED_HISTORICAL_RETURN_FEATURES,
            status=status,
            reason_codes=reason_codes,
            source_refs=source_refs,
            created_at=to_utc_iso(utc_now()),
            payload={"calculation_version": CALCULATION_VERSION, **dict(payload)},
        )

    def event_instrument_ids(self, event: StructuredEvent, requested: tuple[str, ...]) -> tuple[str, ...]:
        requested_set = set(requested)
        event_ids = tuple(item for item in event.instrument_ids if item in requested_set)
        if event_ids:
            return event_ids
        if not event.instrument_ids and len(requested) == 1:
            return requested
        return ()

    def source_refs(self, event: StructuredEvent, module_input: CorporateActionsInput) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    f"events.structured_event:{event.event_id}",
                    *event.source_refs,
                    *module_input.corporate_action_refs,
                )
            )
        )

    def output_horizons(self, job: ModuleJob) -> tuple[str, ...]:
        return tuple(horizon for horizon in (job.horizons or ("swing", "position")) if horizon in VALID_HORIZONS) or ("swing", "position")

    def adjusted_price_series_ref(self, instrument_id: str, corporate_action_id: str) -> str:
        return f"raw_market.raw_candle:adjusted:{instrument_id}:{corporate_action_id}:{CALCULATION_VERSION}"

    def write_corporate_action_record(self, record: CorporateActionRecord) -> str:
        return self.repository.save_corporate_action_record(record)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_instrument_mapping_update(self, update: InstrumentMappingUpdate) -> str:
        return self.repository.save_instrument_mapping_update(update)

    def write_instrument_status_update(self, update: InstrumentStatusUpdate) -> str:
        return self.repository.save_instrument_status_update(update)

    def _external_request(
        self,
        job: ModuleJob,
        module_input: CorporateActionsInput,
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
                "corporate_action_refs": list(module_input.corporate_action_refs),
                "price_series_ref": module_input.price_series_ref,
                "instrument_profile_ref": module_input.instrument_profile_ref,
                "adjustment_policy": module_input.adjustment_policy,
                "time_range": job.time_range.to_dict(),
                "gateway_only": True,
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=24 * 60 * 60, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _moex_market_data_request(self, job: ModuleJob, profile: InstrumentProfile, suffix: str) -> ExternalRequest:
        board_id = profile.board_id or "TQBR"
        secid = profile.ticker or _strip_moex_prefix(profile.instrument_id)
        idempotency_key = f"{job.idempotency_key}:{suffix}:{profile.instrument_id}:{board_id}:1d"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="market_data",
            universe_id=job.universe_id,
            instrument_ids=(profile.instrument_id,),
            payload={
                "secid": secid,
                "board_id": board_id,
                "timeframe": "1d",
                "timeframes": ["1d"],
                "time_range": job.time_range.to_dict(),
                "gateway_only": True,
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=24 * 60 * 60, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _moex_instrument_request(self, job: ModuleJob, instrument_id: str, suffix: str) -> ExternalRequest:
        secid = _strip_moex_prefix(instrument_id)
        idempotency_key = f"{job.idempotency_key}:{suffix}:{instrument_id}:TQBR"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="instruments",
            universe_id=job.universe_id,
            instrument_ids=(instrument_id,),
            payload={"secid": secid, "board_id": "TQBR", "gateway_only": True},
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=24 * 60 * 60, write_cache=True),
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
        raise CorporateActionsAdjustmentError("gateway does not expose process/execute")

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
    ) -> CorporateActionsExecutionResult:
        return CorporateActionsExecutionResult(
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
            corporate_action_records=(),
            feature_records=(),
            adjusted_price_series_refs=(),
            instrument_mapping_updates=(),
            instrument_status_updates=(),
            recompute_triggers=(),
            corporate_action_record_refs=(),
            feature_record_refs=(),
            instrument_mapping_update_refs=(),
            instrument_status_update_refs=(),
            recompute_trigger_refs=(),
        )

    def _empty_execution_result(self, result: ModuleJobResult) -> CorporateActionsExecutionResult:
        return CorporateActionsExecutionResult(result, (), (), (), (), (), (), (), (), (), (), ())

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> CorporateActionsExecutionResult:
        return self._empty_result(job, started_at, "failed", (), (str(error),))


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


def _latest_close_before(candles: tuple[RawCandle, ...], effective_date: str) -> float | None:
    effective = parse_date(effective_date)
    if effective is None:
        return None
    candidates = [
        candle
        for candle in candles
        if candle.close_price is not None
        and parse_date(candle.close_ts or candle.open_ts) is not None
        and parse_date(candle.close_ts or candle.open_ts) < effective
    ]
    if not candidates:
        return None
    return float(sorted(candidates, key=lambda candle: candle.close_ts or candle.open_ts)[-1].close_price)


def _ratio_new_to_old(payload: Mapping[str, Any], default_numeric_mode: str) -> float | None:
    for key in ("split_ratio", "consolidation_ratio", "ratio", "new_to_old_ratio"):
        value = payload.get(key)
        numeric = _optional_float(value)
        if numeric is not None and numeric > 0 and default_numeric_mode == "old_to_new" and key != "new_to_old_ratio":
            return 1.0 / numeric
        parsed = _parse_ratio(value)
        if parsed is not None:
            return parsed
    new_shares = _first_float(payload, "new_shares", "shares_after_per_old")
    old_shares = _first_float(payload, "old_shares", "shares_before")
    if new_shares is not None and old_shares not in (None, 0):
        return new_shares / old_shares
    numeric = _first_float(payload, "split_coefficient", "coefficient")
    if numeric is None or numeric <= 0:
        return None
    if default_numeric_mode == "old_to_new":
        return 1.0 / numeric
    return numeric


def _parse_ratio(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if numeric > 0 else None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        left = _optional_float(value[0])
        right = _optional_float(value[1])
        if left is not None and right not in (None, 0):
            return left / right
    text = str(value).strip().replace("/", ":")
    if ":" in text:
        left, right = text.split(":", 1)
        left_value = _optional_float(left)
        right_value = _optional_float(right)
        if left_value is not None and right_value not in (None, 0):
            return left_value / right_value
    numeric = _optional_float(text)
    if numeric is not None and numeric > 0:
        return numeric
    return None


def _text_field(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _optional_bool_field(payload: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "allowed", "tradable", "open"}:
            return True
        if text in {"false", "0", "no", "n", "blocked", "halted", "closed", "suspended"}:
            return False
    return None


def _has_any(payload: Mapping[str, Any], *keys: str) -> bool:
    return any(key in payload and payload.get(key) not in (None, "") for key in keys)


def _first_float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(payload.get(key))
        if value is not None:
            return value
    nested_keys = ("metrics", "fields", "corporate_action", "terms")
    for nested_key in nested_keys:
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            value = _first_float(nested, *keys)
            if value is not None:
                return value
    return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text


def _metric_confidence(base: float, quality_flags: tuple[str, ...]) -> float:
    confidence = clip_required(base)
    if "adjustment_factor_missing" in quality_flags:
        confidence = min(confidence, 0.4)
    if quality_flags:
        confidence = min(confidence, 0.75)
    return confidence
