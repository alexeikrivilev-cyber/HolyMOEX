from __future__ import annotations

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
    check_derivatives_liquidity,
    clip,
    compute_basis_change,
    compute_derivatives_pressure_score,
    compute_futures_basis,
    compute_implied_volatility_if_available,
    compute_iv_rv_spread,
    compute_open_interest_change,
    compute_options_skew,
    compute_put_call_ratio,
    compute_realized_volatility,
    compute_volume_oi_ratio,
    signed_to_unit,
    years_to_expiry,
    zscore,
)
from .repository import (
    DerivativesAvailabilityRecord,
    DerivativesPositioningRepository,
    FeatureRecord,
    InMemoryDerivativesPositioningRepository,
    InstrumentProfile,
    RawFuturesPoint,
    RawOptionPoint,
    SpotMarketPoint,
    stable_record_id,
    stable_uuid_id,
)


MODULE_NAME = "Derivatives & Positioning Module"
CALCULATION_VERSION = "derivatives_positioning_v1"

VALID_CONTOURS = {"intraday_contour", "daily_contour"}
VALID_HORIZONS = {"intraday", "swing"}
INPUT_FIELDS = {
    "instrument_ids",
    "futures_refs",
    "options_refs",
    "spot_refs",
    "liquidity_thresholds",
}
THRESHOLD_FIELDS = {"min_turnover", "min_open_interest"}
INTRADAY_TTL_SECONDS = 3600
DAILY_TTL_SECONDS = 86400
DEFAULT_ALIGNMENT_SECONDS = 3600
DAILY_ALIGNMENT_SECONDS = 36 * 3600
DEFAULT_PRESSURE_WEIGHTS = {
    "basis_change_z": 1.0,
    "open_interest_change_z": 1.0,
    "volume_oi_ratio_z": 1.0,
    "put_call_ratio_z": 1.0,
    "options_skew_z": 1.0,
}
VALID_SKIP_REASONS = {"no_data", "low_liquidity", "unsupported", "none"}


class DerivativesPositioningError(ValueError):
    """Raised when module 14 would violate its documented contract."""


@dataclass(frozen=True)
class LiquidityThresholds:
    min_turnover: float
    min_open_interest: float

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "LiquidityThresholds":
        missing_fields = sorted(THRESHOLD_FIELDS - set(payload))
        if missing_fields:
            raise DerivativesPositioningError(f"liquidity_thresholds missing required fields: {missing_fields}")
        extra_fields = sorted(set(payload) - THRESHOLD_FIELDS)
        if extra_fields:
            raise DerivativesPositioningError(f"liquidity_thresholds has undocumented fields: {extra_fields}")
        min_turnover = _required_non_negative_float(payload.get("min_turnover"), "liquidity_thresholds.min_turnover")
        min_open_interest = _required_non_negative_float(
            payload.get("min_open_interest"),
            "liquidity_thresholds.min_open_interest",
        )
        return cls(min_turnover=min_turnover, min_open_interest=min_open_interest)


@dataclass(frozen=True)
class DerivativesPositioningInput:
    instrument_ids: tuple[str, ...]
    futures_refs: tuple[str, ...]
    options_refs: tuple[str, ...]
    spot_refs: tuple[str, ...]
    liquidity_thresholds: LiquidityThresholds

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "DerivativesPositioningInput":
        input_payload = payload.get("derivatives_input")
        if not isinstance(input_payload, Mapping):
            raise DerivativesPositioningError("payload must contain derivatives_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise DerivativesPositioningError(f"derivatives_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise DerivativesPositioningError(f"derivatives_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise DerivativesPositioningError("derivatives_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise DerivativesPositioningError("derivatives_input.instrument_ids must match module_job.instrument_ids")

        thresholds_payload = input_payload.get("liquidity_thresholds")
        if not isinstance(thresholds_payload, Mapping):
            raise DerivativesPositioningError("derivatives_input.liquidity_thresholds is required")

        return cls(
            instrument_ids=instrument_ids,
            futures_refs=tuple(str(item) for item in (input_payload.get("futures_refs") or ())),
            options_refs=tuple(str(item) for item in (input_payload.get("options_refs") or ())),
            spot_refs=tuple(str(item) for item in (input_payload.get("spot_refs") or ())),
            liquidity_thresholds=LiquidityThresholds.from_mapping(thresholds_payload),
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


@dataclass(frozen=True)
class InstrumentComputation:
    availability_record: DerivativesAvailabilityRecord
    metric_values: tuple[MetricValue, ...]
    timestamp: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DerivativesPositioningExecutionResult:
    module_job_result: ModuleJobResult
    derivatives_availability_records: tuple[DerivativesAvailabilityRecord, ...]
    feature_records: tuple[FeatureRecord, ...]
    derivatives_availability_refs: tuple[str, ...]
    feature_record_refs: tuple[str, ...]

    @property
    def derivatives_availability_record(self) -> DerivativesAvailabilityRecord | None:
        return self.derivatives_availability_records[0] if len(self.derivatives_availability_records) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "derivatives_availability_records": [
                record.to_dict() for record in self.derivatives_availability_records
            ],
            "feature_records": [record.to_dict() for record in self.feature_records],
            "derivatives_availability_refs": list(self.derivatives_availability_refs),
            "feature_record_refs": list(self.feature_record_refs),
        }
        if len(self.derivatives_availability_records) == 1:
            payload["derivatives_availability_record"] = self.derivatives_availability_records[0].to_dict()[
                "derivatives_availability_record"
            ]
        return payload


class DerivativesPositioningService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: DerivativesPositioningRepository | None = None,
        gateway: Any | None = None,
        pressure_weights: Mapping[str, float] | None = None,
        put_call_basis: str = "volume",
        max_alignment_seconds: int | None = None,
    ) -> None:
        self.repository = repository or InMemoryDerivativesPositioningRepository()
        self.gateway = gateway
        self.pressure_weights = dict(pressure_weights or DEFAULT_PRESSURE_WEIGHTS)
        if put_call_basis not in {"volume", "open_interest"}:
            raise DerivativesPositioningError("put_call_basis must be volume or open_interest")
        self.put_call_basis = put_call_basis
        self.max_alignment_seconds = max_alignment_seconds

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> DerivativesPositioningExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> DerivativesPositioningExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> DerivativesPositioningExecutionResult:
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
            return DerivativesPositioningExecutionResult(result, (), (), (), ())

        try:
            self.validate_module_job(job)
            derivatives_input = DerivativesPositioningInput.from_dict(payload, job)
            profiles = self.repository.list_instrument_profiles(job.universe_id, derivatives_input.instrument_ids)
            futures = self.repository.list_futures_data(
                futures_refs=derivatives_input.futures_refs,
                universe_id=job.universe_id,
                instrument_ids=derivatives_input.instrument_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            options = self.repository.list_options_data(
                options_refs=derivatives_input.options_refs,
                universe_id=job.universe_id,
                instrument_ids=derivatives_input.instrument_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            spots = self.repository.list_spot_market_data(
                spot_refs=derivatives_input.spot_refs,
                universe_id=job.universe_id,
                instrument_ids=derivatives_input.instrument_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )

            warnings: list[str] = []
            external_requests = self.create_external_requests_for_missing_raw_data(
                derivatives_input=derivatives_input,
                job=job,
                profiles=profiles,
                futures=futures,
                options=options,
                spots=spots,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self._gateway_process(request)
            warnings.extend(f"external_request_created:{request.request_id}" for request in external_requests)

            computations = self.compute_outputs(
                derivatives_input=derivatives_input,
                job=job,
                profiles=profiles,
                futures=futures,
                options=options,
                spots=spots,
            )
            for computation in computations:
                warnings.extend(computation.warnings)

            availability_records = tuple(computation.availability_record for computation in computations)
            feature_records = tuple(
                record
                for computation in computations
                for metric_value in computation.metric_values
                for record in self.build_feature_records(
                    metric_value=metric_value,
                    instrument_id=computation.availability_record.instrument_id,
                    job=job,
                    timestamp=computation.timestamp,
                    horizons=self.output_horizons(job),
                )
            )
            availability_refs = tuple(
                self.write_derivatives_availability_record(record) for record in availability_records
            )
            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            output_refs = availability_refs + feature_refs

            if not feature_records:
                status = "skipped"
            elif warnings or len(feature_records) < len(derivatives_input.instrument_ids):
                status = "partial_success"
            else:
                status = "success"

            return DerivativesPositioningExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(feature_records),
                ),
                derivatives_availability_records=availability_records,
                feature_records=feature_records,
                derivatives_availability_refs=availability_refs,
                feature_record_refs=feature_refs,
            )
        except (DerivativesPositioningError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise DerivativesPositioningError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise DerivativesPositioningError("module_job.module_name must be Derivatives & Positioning Module")
        if job.contour not in VALID_CONTOURS:
            raise DerivativesPositioningError("module_job.contour must be intraday_contour or daily_contour")
        if not job.universe_id:
            raise DerivativesPositioningError("module_job.universe_id is required")
        if not job.instrument_ids:
            raise DerivativesPositioningError("module_job.instrument_ids is required")
        if not job.horizons:
            raise DerivativesPositioningError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise DerivativesPositioningError(f"Derivatives metrics only support intraday/swing horizons: {invalid_horizons}")
        if not job.run_mode:
            raise DerivativesPositioningError("module_job.run_mode is required")

    def compute_outputs(
        self,
        *,
        derivatives_input: DerivativesPositioningInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...],
        futures: tuple[RawFuturesPoint, ...],
        options: tuple[RawOptionPoint, ...],
        spots: tuple[SpotMarketPoint, ...],
    ) -> tuple[InstrumentComputation, ...]:
        computations: list[InstrumentComputation] = []
        profile_by_id = {profile.instrument_id: profile for profile in profiles}
        futures_by_instrument = _group_futures(futures)
        options_by_instrument = _group_options(options)
        spots_by_instrument = _group_spots(spots)

        for instrument_id in derivatives_input.instrument_ids:
            profile = profile_by_id.get(instrument_id)
            instrument_futures = futures_by_instrument.get(instrument_id, ())
            instrument_options = options_by_instrument.get(instrument_id, ())
            instrument_spots = spots_by_instrument.get(instrument_id, ())
            warnings: list[str] = []

            if profile is None:
                computations.append(
                    self._unsupported_computation(
                        instrument_id,
                        job,
                        "instrument_profile_missing",
                    )
                )
                continue
            if not profile.is_active:
                computations.append(self._unsupported_computation(instrument_id, job, "instrument_inactive"))
                continue
            if not (profile.derivatives_enabled or profile.is_index_level_context):
                computations.append(
                    self._unsupported_computation(
                        instrument_id,
                        job,
                        "derivatives_not_enabled",
                    )
                )
                continue

            if not instrument_futures and not instrument_options:
                computations.append(
                    self._availability_only_computation(
                        instrument_id=instrument_id,
                        job=job,
                        skip_reason="no_data",
                        has_liquid_futures=False,
                        has_liquid_options=False,
                        warnings=(f"derivatives_data_missing:{instrument_id}",),
                        payload={"profile_derivatives_enabled": profile.derivatives_enabled},
                    )
                )
                continue

            latest_futures = instrument_futures[-1] if instrument_futures else None
            latest_option_chain = _latest_option_chain(instrument_options)
            futures_turnover = _effective_turnover(latest_futures)
            futures_open_interest = latest_futures.open_interest if latest_futures else None
            options_turnover = sum(_effective_turnover(option) or 0.0 for option in latest_option_chain) if latest_option_chain else None
            options_open_interest = sum(option.open_interest or 0.0 for option in latest_option_chain) if latest_option_chain else None
            has_liquid_futures = check_derivatives_liquidity(
                futures_turnover,
                futures_open_interest,
                derivatives_input.liquidity_thresholds.min_turnover,
                derivatives_input.liquidity_thresholds.min_open_interest,
            )
            has_liquid_options = check_derivatives_liquidity(
                options_turnover,
                options_open_interest,
                derivatives_input.liquidity_thresholds.min_turnover,
                derivatives_input.liquidity_thresholds.min_open_interest,
            )
            fresh_liquid_futures = has_liquid_futures and latest_futures is not None and not _futures_stale(
                (latest_futures,),
                job,
                self._alignment_seconds(job),
            )
            fresh_liquid_options = has_liquid_options and bool(latest_option_chain) and not _options_stale(
                latest_option_chain,
                job,
                self._alignment_seconds(job),
            )
            if has_liquid_options and not fresh_liquid_options:
                warnings.append(f"stale_option_chain:{instrument_id}")

            if not has_liquid_futures and not has_liquid_options:
                computations.append(
                    self._availability_only_computation(
                        instrument_id=instrument_id,
                        job=job,
                        skip_reason="low_liquidity",
                        has_liquid_futures=False,
                        has_liquid_options=False,
                        warnings=(f"derivatives_liquidity_below_threshold:{instrument_id}",),
                        payload={
                            "futures_turnover": futures_turnover,
                            "futures_open_interest": futures_open_interest,
                            "options_turnover": options_turnover,
                            "options_open_interest": options_open_interest,
                            "min_turnover": derivatives_input.liquidity_thresholds.min_turnover,
                            "min_open_interest": derivatives_input.liquidity_thresholds.min_open_interest,
                        },
                    )
                )
                continue

            if not fresh_liquid_futures and not fresh_liquid_options:
                computations.append(
                    self._availability_only_computation(
                        instrument_id=instrument_id,
                        job=job,
                        skip_reason="no_data",
                        has_liquid_futures=has_liquid_futures,
                        has_liquid_options=has_liquid_options,
                        warnings=tuple(dict.fromkeys((*warnings, f"fresh_derivatives_data_missing:{instrument_id}"))),
                        payload={
                            "latest_futures_ts": latest_futures.timestamp if latest_futures else None,
                            "latest_options_ts": latest_option_chain[-1].timestamp if latest_option_chain else None,
                            "alignment_seconds": self._alignment_seconds(job),
                        },
                    )
                )
                continue

            latest_derivative_ts = _latest_timestamp(
                (latest_futures,) if fresh_liquid_futures and latest_futures is not None else (),
                latest_option_chain if fresh_liquid_options else (),
            )
            latest_spot = self._aligned_spot_point(instrument_spots, latest_derivative_ts, job)
            if latest_spot is None:
                computations.append(
                    self._availability_only_computation(
                        instrument_id=instrument_id,
                        job=job,
                        skip_reason="no_data",
                        has_liquid_futures=has_liquid_futures,
                        has_liquid_options=has_liquid_options,
                        warnings=(f"spot_derivative_timestamp_misaligned:{instrument_id}",),
                        payload={
                            "latest_derivative_ts": latest_derivative_ts,
                            "alignment_seconds": self._alignment_seconds(job),
                        },
                    )
                )
                continue

            metric_values, metric_warnings = self.compute_metric_values(
                derivatives_input=derivatives_input,
                job=job,
                instrument_id=instrument_id,
                futures=instrument_futures if fresh_liquid_futures else (),
                options=instrument_options if fresh_liquid_options else (),
                spots=instrument_spots,
                latest_spot=latest_spot,
                has_liquid_futures=fresh_liquid_futures,
                has_liquid_options=fresh_liquid_options,
            )
            warnings.extend(metric_warnings)
            computations.append(
                InstrumentComputation(
                    availability_record=self.build_derivatives_availability_record(
                        instrument_id=instrument_id,
                        job=job,
                        derivatives_enabled=bool(metric_values),
                        skip_reason="none" if metric_values else "no_data",
                        has_liquid_futures=has_liquid_futures,
                        has_liquid_options=has_liquid_options,
                        payload={
                            "profile_derivatives_enabled": profile.derivatives_enabled,
                            "index_level_context": profile.is_index_level_context,
                            "futures_turnover": futures_turnover,
                            "futures_open_interest": futures_open_interest,
                            "options_turnover": options_turnover,
                            "options_open_interest": options_open_interest,
                            "aligned_spot_ts": latest_spot.timestamp,
                            "fresh_liquid_futures": fresh_liquid_futures,
                            "fresh_liquid_options": fresh_liquid_options,
                        },
                    ),
                    metric_values=metric_values,
                    timestamp=latest_derivative_ts or latest_spot.timestamp or job.time_range.to_ts,
                    warnings=tuple(dict.fromkeys(warnings)),
                )
            )

        return tuple(computations)

    def compute_metric_values(
        self,
        *,
        derivatives_input: DerivativesPositioningInput,
        job: ModuleJob,
        instrument_id: str,
        futures: tuple[RawFuturesPoint, ...],
        options: tuple[RawOptionPoint, ...],
        spots: tuple[SpotMarketPoint, ...],
        latest_spot: SpotMarketPoint,
        has_liquid_futures: bool,
        has_liquid_options: bool,
    ) -> tuple[tuple[MetricValue, ...], tuple[str, ...]]:
        values: list[MetricValue] = []
        warnings: list[str] = []
        source_refs = _context_refs(derivatives_input)
        component_values: dict[str, float | None] = {}
        current_basis = None
        basis_change = None
        open_interest_change = None
        volume_oi_ratio = None
        put_call_ratio = None
        options_skew = None

        if has_liquid_futures and futures:
            latest_futures = futures[-1]
            previous_futures = _previous_point(futures)
            current_basis = compute_futures_basis(latest_futures.price, latest_spot.price)
            previous_spot = self._aligned_spot_point(
                spots,
                previous_futures.timestamp if previous_futures else "",
                job,
            )
            previous_basis = (
                compute_futures_basis(previous_futures.price, previous_spot.price)
                if previous_futures is not None and previous_spot is not None
                else None
            )
            basis_change = compute_basis_change(current_basis, previous_basis)
            open_interest_change = compute_open_interest_change(
                latest_futures.open_interest,
                previous_futures.open_interest if previous_futures else None,
            )
            volume_oi_ratio = compute_volume_oi_ratio(latest_futures.volume, latest_futures.open_interest)
            self._append_metric(
                values,
                "futures_basis",
                "derived_metric",
                current_basis,
                signed_to_unit(current_basis, 0.1),
                "ratio",
                INTRADAY_TTL_SECONDS,
                source_refs + _point_refs(latest_futures, latest_spot),
                {
                    "formula": "(futures_price - spot_price) / spot_price",
                    "futures_price": latest_futures.price,
                    "spot_price": latest_spot.price,
                    "derivative_id": latest_futures.derivative_id,
                },
            )
            self._append_metric(
                values,
                "basis_change",
                "derived_metric",
                basis_change,
                signed_to_unit(basis_change, 0.05),
                "ratio_diff",
                INTRADAY_TTL_SECONDS,
                source_refs + _point_refs(latest_futures, latest_spot),
                {
                    "formula": "futures_basis_t - futures_basis_{t-1}",
                    "previous_basis": previous_basis,
                    "derivative_id": latest_futures.derivative_id,
                },
            )
            self._append_metric(
                values,
                "open_interest_change",
                "derived_metric",
                open_interest_change,
                signed_to_unit(open_interest_change, 0.5),
                "ratio",
                DAILY_TTL_SECONDS,
                source_refs + _point_refs(latest_futures),
                {
                    "formula": "open_interest_t / open_interest_{t-1} - 1",
                    "previous_open_interest": previous_futures.open_interest if previous_futures else None,
                    "derivative_id": latest_futures.derivative_id,
                },
            )
            self._append_metric(
                values,
                "volume_oi_ratio",
                "derived_metric",
                volume_oi_ratio,
                _scale_positive_to_unit(volume_oi_ratio, 5.0),
                "ratio",
                INTRADAY_TTL_SECONDS,
                source_refs + _point_refs(latest_futures),
                {
                    "formula": "derivative_volume / open_interest",
                    "derivative_id": latest_futures.derivative_id,
                },
            )
            component_values.update(
                {
                    "basis_change_z": zscore(basis_change, _basis_change_history(futures, spots, job, self._alignment_seconds(job))),
                    "open_interest_change_z": zscore(open_interest_change, _open_interest_change_history(futures)),
                    "volume_oi_ratio_z": zscore(volume_oi_ratio, _volume_oi_ratio_history(futures)),
                }
            )

        if has_liquid_options and options:
            latest_chain = _latest_option_chain(options)
            if not latest_chain:
                warnings.append(f"stale_option_chain:{instrument_id}")
            else:
                implied_volatility = self._chain_implied_volatility(latest_chain, latest_spot)
                realized_volatility = _realized_volatility_from_spots(spots)
                iv_rv_spread = compute_iv_rv_spread(implied_volatility, realized_volatility)
                put_call_ratio = self._put_call_ratio(latest_chain)
                options_skew = self._options_skew(latest_chain, latest_spot)
                self._append_metric(
                    values,
                    "implied_volatility",
                    "raw_metric",
                    implied_volatility,
                    _scale_positive_to_unit(implied_volatility, 2.0),
                    "annualized_volatility",
                    INTRADAY_TTL_SECONDS,
                    source_refs + _option_refs(latest_chain),
                    {"rule": "provider IV or solved IV from option price using configured model"},
                )
                self._append_metric(
                    values,
                    "iv_rv_spread",
                    "derived_metric",
                    iv_rv_spread,
                    signed_to_unit(iv_rv_spread, 1.0),
                    "volatility_diff",
                    INTRADAY_TTL_SECONDS,
                    source_refs + _option_refs(latest_chain),
                    {
                        "formula": "implied_volatility - realized_volatility",
                        "realized_volatility": realized_volatility,
                    },
                )
                self._append_metric(
                    values,
                    "put_call_ratio",
                    "derived_metric",
                    put_call_ratio,
                    _scale_positive_to_unit(put_call_ratio, 5.0),
                    "ratio",
                    INTRADAY_TTL_SECONDS,
                    source_refs + _option_refs(latest_chain),
                    {"formula": "put_volume / call_volume or put_oi / call_oi by config", "basis": self.put_call_basis},
                )
                self._append_metric(
                    values,
                    "options_skew",
                    "derived_metric",
                    options_skew,
                    signed_to_unit(options_skew, 1.0),
                    "volatility_diff",
                    INTRADAY_TTL_SECONDS,
                    source_refs + _option_refs(latest_chain),
                    {"formula": "IV_put_25delta - IV_call_25delta or nearest available proxy"},
                )
                component_values.update(
                    {
                        "put_call_ratio_z": zscore(put_call_ratio, self._put_call_ratio_history(options)),
                        "options_skew_z": zscore(options_skew, self._options_skew_history(options, latest_spot)),
                    }
                )

        pressure = compute_derivatives_pressure_score(component_values, self.pressure_weights)
        self._append_metric(
            values,
            "derivatives_pressure_score",
            "composite_score",
            pressure,
            signed_to_unit(pressure, 3.0),
            "score",
            INTRADAY_TTL_SECONDS,
            source_refs,
            {
                "formula": "WAvg([basis_change_z, open_interest_change_z, volume_oi_ratio_z, put_call_ratio_z, options_skew_z], active_weights)",
                "component_values": {name: value for name, value in component_values.items() if value is not None},
                "active_weights": dict(self.pressure_weights),
            },
        )
        return tuple(values), tuple(dict.fromkeys(warnings))

    def build_feature_records(
        self,
        *,
        metric_value: MetricValue,
        instrument_id: str,
        job: ModuleJob,
        timestamp: str,
        horizons: tuple[str, ...],
    ) -> tuple[FeatureRecord, ...]:
        records: list[FeatureRecord] = []
        for horizon in horizons:
            feature_payload = {
                "instrument_id": instrument_id,
                "metric_name": metric_value.metric_name,
                "horizon": horizon,
                "timestamp": timestamp,
                "calculation_version": CALCULATION_VERSION,
            }
            records.append(
                FeatureRecord(
                    feature_id=stable_record_id("feature", feature_payload),
                    instrument_id=instrument_id,
                    metric_name=metric_value.metric_name,
                    metric_group="derivative",
                    metric_type=metric_value.metric_type,
                    raw_value=float(metric_value.raw_value),
                    normalized_value=None if metric_value.normalized_value is None else float(metric_value.normalized_value),
                    unit=metric_value.unit,
                    horizon=horizon,
                    contour=job.contour,
                    timestamp=timestamp,
                    ttl_seconds=metric_value.ttl_seconds,
                    confidence_score=0.8 if metric_value.quality_flags else 1.0,
                    source_module=self.module_name,
                    source_refs=metric_value.source_refs,
                    calculation_version=CALCULATION_VERSION,
                    quality_flags=metric_value.quality_flags,
                    payload={
                        "calculation_version": CALCULATION_VERSION,
                        "formula_version": CALCULATION_VERSION,
                        **dict(metric_value.payload),
                    },
                )
            )
        return tuple(records)

    def build_derivatives_availability_record(
        self,
        *,
        instrument_id: str,
        job: ModuleJob,
        derivatives_enabled: bool,
        skip_reason: str,
        has_liquid_futures: bool,
        has_liquid_options: bool,
        payload: Mapping[str, Any],
    ) -> DerivativesAvailabilityRecord:
        if skip_reason not in VALID_SKIP_REASONS:
            raise DerivativesPositioningError(f"invalid skip_reason: {skip_reason}")
        availability_payload = {
            "instrument_id": instrument_id,
            "as_of_ts": job.time_range.to_ts,
            "calculation_version": CALCULATION_VERSION,
        }
        return DerivativesAvailabilityRecord(
            availability_id=stable_uuid_id(availability_payload),
            instrument_id=instrument_id,
            derivatives_enabled=derivatives_enabled,
            skip_reason=skip_reason,
            as_of_ts=job.time_range.to_ts,
            has_liquid_futures=has_liquid_futures,
            has_liquid_options=has_liquid_options,
            source_module=self.module_name,
            calculation_version=CALCULATION_VERSION,
            payload={
                "skip_reason": skip_reason,
                "derivatives_enabled": derivatives_enabled,
                "job_id": job.job_id,
                **dict(payload),
            },
        )

    def create_external_requests_for_missing_raw_data(
        self,
        *,
        derivatives_input: DerivativesPositioningInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...],
        futures: tuple[RawFuturesPoint, ...],
        options: tuple[RawOptionPoint, ...],
        spots: tuple[SpotMarketPoint, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        futures_expected = bool(derivatives_input.futures_refs) or not derivatives_input.options_refs
        options_expected = bool(derivatives_input.options_refs) or not derivatives_input.futures_refs
        if futures_expected and (not futures or _futures_stale(futures, job, self._alignment_seconds(job))):
            requests.append(self._external_request(job, derivatives_input, "market_data", "futures_open_interest"))
        if options_expected and (not options or _options_stale(options, job, self._alignment_seconds(job))):
            requests.append(self._external_request(job, derivatives_input, "market_data", "options_chain"))
        if (derivatives_input.spot_refs or futures_expected or options_expected) and (
            not spots or _spots_stale(spots, job, self._alignment_seconds(job))
        ):
            requests.append(self._external_request(job, derivatives_input, "market_data", "spot_reference"))
        return tuple(requests)

    def output_horizons(self, job: ModuleJob) -> tuple[str, ...]:
        return tuple(horizon for horizon in job.horizons if horizon in VALID_HORIZONS)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_derivatives_availability_record(self, record: DerivativesAvailabilityRecord) -> str:
        return self.repository.save_derivatives_availability_record(record)

    def _append_metric(
        self,
        values: list[MetricValue],
        metric_name: str,
        metric_type: str,
        raw_value: float | None,
        normalized_value: float | None,
        unit: str,
        ttl_seconds: int,
        source_refs: tuple[str, ...],
        payload: Mapping[str, Any],
        quality_flags: tuple[str, ...] = (),
    ) -> None:
        if raw_value is None:
            return
        values.append(
            MetricValue(
                metric_name=metric_name,
                metric_type=metric_type,
                raw_value=float(raw_value),
                normalized_value=None if normalized_value is None else float(normalized_value),
                unit=unit,
                ttl_seconds=ttl_seconds,
                source_refs=tuple(dict.fromkeys(source_refs)),
                payload=payload,
                quality_flags=quality_flags,
            )
        )

    def _chain_implied_volatility(
        self,
        chain: tuple[RawOptionPoint, ...],
        latest_spot: SpotMarketPoint,
    ) -> float | None:
        weighted_values: list[tuple[float, float]] = []
        for option in chain:
            iv = compute_implied_volatility_if_available(
                option.implied_volatility,
                option_price=option.price,
                spot_price=latest_spot.price,
                strike_price=option.strike_price,
                time_to_expiry_years=years_to_expiry(option.expiry_date, option.timestamp),
                risk_free_rate=option.risk_free_rate or 0.0,
                option_type=option.option_type or "call",
                dividend_yield=option.dividend_yield or 0.0,
            )
            if iv is None:
                continue
            weight = option.open_interest or option.volume or 1.0
            weighted_values.append((iv, max(float(weight), 0.0)))
        if not weighted_values:
            return None
        denominator = sum(weight for _, weight in weighted_values)
        if denominator <= 0:
            return sum(value for value, _ in weighted_values) / len(weighted_values)
        return sum(value * weight for value, weight in weighted_values) / denominator

    def _put_call_ratio(self, chain: tuple[RawOptionPoint, ...]) -> float | None:
        if self.put_call_basis == "open_interest":
            put_value = sum(option.open_interest or 0.0 for option in chain if option.option_type == "put")
            call_value = sum(option.open_interest or 0.0 for option in chain if option.option_type == "call")
        else:
            put_value = sum(option.volume or 0.0 for option in chain if option.option_type == "put")
            call_value = sum(option.volume or 0.0 for option in chain if option.option_type == "call")
        return compute_put_call_ratio(put_value, call_value)

    def _options_skew(self, chain: tuple[RawOptionPoint, ...], latest_spot: SpotMarketPoint) -> float | None:
        put_iv = self._nearest_delta_iv(chain, latest_spot, "put")
        call_iv = self._nearest_delta_iv(chain, latest_spot, "call")
        return compute_options_skew(put_iv, call_iv)

    def _nearest_delta_iv(
        self,
        chain: tuple[RawOptionPoint, ...],
        latest_spot: SpotMarketPoint,
        option_type: str,
    ) -> float | None:
        candidates: list[tuple[float, float]] = []
        for option in chain:
            if option.option_type != option_type:
                continue
            iv = compute_implied_volatility_if_available(
                option.implied_volatility,
                option_price=option.price,
                spot_price=latest_spot.price,
                strike_price=option.strike_price,
                time_to_expiry_years=years_to_expiry(option.expiry_date, option.timestamp),
                risk_free_rate=option.risk_free_rate or 0.0,
                option_type=option.option_type or option_type,
                dividend_yield=option.dividend_yield or 0.0,
            )
            if iv is None:
                continue
            delta_distance = abs(abs(option.delta if option.delta is not None else 0.25) - 0.25)
            candidates.append((delta_distance, iv))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0])[0][1]

    def _put_call_ratio_history(self, options: tuple[RawOptionPoint, ...]) -> tuple[float, ...]:
        values: list[float] = []
        for _, chain in _option_chains_by_timestamp(options):
            ratio = self._put_call_ratio(chain)
            if ratio is not None:
                values.append(ratio)
        return tuple(values)

    def _options_skew_history(
        self,
        options: tuple[RawOptionPoint, ...],
        latest_spot: SpotMarketPoint,
    ) -> tuple[float, ...]:
        values: list[float] = []
        for _, chain in _option_chains_by_timestamp(options):
            skew = self._options_skew(chain, latest_spot)
            if skew is not None:
                values.append(skew)
        return tuple(values)

    def _aligned_spot_point(
        self,
        spots: tuple[SpotMarketPoint, ...],
        derivative_ts: str,
        job: ModuleJob,
    ) -> SpotMarketPoint | None:
        if not derivative_ts:
            return None
        try:
            derivative_dt = parse_utc_iso(derivative_ts)
        except Exception:
            return None
        max_age = self._alignment_seconds(job)
        candidates = []
        for spot in spots:
            if spot.price is None or spot.price <= 0 or not spot.timestamp:
                continue
            try:
                spot_dt = parse_utc_iso(spot.timestamp)
            except Exception:
                continue
            age = abs((derivative_dt - spot_dt).total_seconds())
            if age <= max_age:
                candidates.append((age, spot.timestamp, spot))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]

    def _alignment_seconds(self, job: ModuleJob) -> int:
        if self.max_alignment_seconds is not None:
            return int(self.max_alignment_seconds)
        return DAILY_ALIGNMENT_SECONDS if job.contour == "daily_contour" else DEFAULT_ALIGNMENT_SECONDS

    def _external_request(
        self,
        job: ModuleJob,
        derivatives_input: DerivativesPositioningInput,
        request_type: str,
        data_kind: str = "",
    ) -> ExternalRequest:
        suffix = data_kind or request_type
        idempotency_key = f"{job.idempotency_key}:{suffix}"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="moex_iss",
            request_type=request_type,
            universe_id=job.universe_id,
            instrument_ids=derivatives_input.instrument_ids,
            payload={
                "futures_refs": list(derivatives_input.futures_refs),
                "options_refs": list(derivatives_input.options_refs),
                "spot_refs": list(derivatives_input.spot_refs),
                "liquidity_thresholds": {
                    "min_turnover": derivatives_input.liquidity_thresholds.min_turnover,
                    "min_open_interest": derivatives_input.liquidity_thresholds.min_open_interest,
                },
                "data_kind": data_kind,
                "time_range": job.time_range.to_dict(),
            },
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=60, write_cache=True),
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
        raise DerivativesPositioningError("gateway does not expose process/execute")

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
            events_written=0,
            data_quality_score=1.0 if not warnings and not errors else 0.8 if metrics_written else 0.0,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> DerivativesPositioningExecutionResult:
        return DerivativesPositioningExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
            ),
            derivatives_availability_records=(),
            feature_records=(),
            derivatives_availability_refs=(),
            feature_record_refs=(),
        )

    def _unsupported_computation(
        self,
        instrument_id: str,
        job: ModuleJob,
        reason_code: str,
    ) -> InstrumentComputation:
        return self._availability_only_computation(
            instrument_id=instrument_id,
            job=job,
            skip_reason="unsupported",
            has_liquid_futures=False,
            has_liquid_options=False,
            warnings=(f"{reason_code}:{instrument_id}",),
            payload={"reason_code": reason_code},
        )

    def _availability_only_computation(
        self,
        *,
        instrument_id: str,
        job: ModuleJob,
        skip_reason: str,
        has_liquid_futures: bool,
        has_liquid_options: bool,
        warnings: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> InstrumentComputation:
        return InstrumentComputation(
            availability_record=self.build_derivatives_availability_record(
                instrument_id=instrument_id,
                job=job,
                derivatives_enabled=False,
                skip_reason=skip_reason,
                has_liquid_futures=has_liquid_futures,
                has_liquid_options=has_liquid_options,
                payload=payload,
            ),
            metric_values=(),
            timestamp=job.time_range.to_ts,
            warnings=warnings,
        )


def _context_refs(derivatives_input: DerivativesPositioningInput) -> tuple[str, ...]:
    return tuple(
        ref
        for ref in (
            *derivatives_input.futures_refs,
            *derivatives_input.options_refs,
            *derivatives_input.spot_refs,
        )
        if ref
    )


def _group_futures(points: tuple[RawFuturesPoint, ...]) -> dict[str, tuple[RawFuturesPoint, ...]]:
    grouped: dict[str, list[RawFuturesPoint]] = {}
    for point in points:
        if point.timestamp:
            grouped.setdefault(point.instrument_id, []).append(point)
    return {key: tuple(sorted(value, key=lambda item: item.timestamp)) for key, value in grouped.items()}


def _group_options(points: tuple[RawOptionPoint, ...]) -> dict[str, tuple[RawOptionPoint, ...]]:
    grouped: dict[str, list[RawOptionPoint]] = {}
    for point in points:
        if point.timestamp:
            grouped.setdefault(point.instrument_id, []).append(point)
    return {key: tuple(sorted(value, key=lambda item: (item.timestamp, item.option_id))) for key, value in grouped.items()}


def _group_spots(points: tuple[SpotMarketPoint, ...]) -> dict[str, tuple[SpotMarketPoint, ...]]:
    grouped: dict[str, list[SpotMarketPoint]] = {}
    for point in points:
        if point.timestamp:
            grouped.setdefault(point.instrument_id, []).append(point)
    return {key: tuple(sorted(value, key=lambda item: item.timestamp)) for key, value in grouped.items()}


def _latest_option_chain(options: tuple[RawOptionPoint, ...]) -> tuple[RawOptionPoint, ...]:
    chains = _option_chains_by_timestamp(options)
    return chains[-1][1] if chains else ()


def _option_chains_by_timestamp(options: tuple[RawOptionPoint, ...]) -> tuple[tuple[str, tuple[RawOptionPoint, ...]], ...]:
    grouped: dict[str, list[RawOptionPoint]] = {}
    for option in options:
        if option.timestamp:
            grouped.setdefault(option.timestamp, []).append(option)
    return tuple((timestamp, tuple(items)) for timestamp, items in sorted(grouped.items()))


def _latest_timestamp(
    futures: tuple[RawFuturesPoint, ...],
    options: tuple[RawOptionPoint, ...],
) -> str:
    timestamps = [point.timestamp for point in futures if point.timestamp]
    timestamps.extend(point.timestamp for point in options if point.timestamp)
    return max(timestamps) if timestamps else ""


def _previous_point(futures: tuple[RawFuturesPoint, ...]) -> RawFuturesPoint | None:
    if len(futures) < 2:
        return None
    latest_contract = futures[-1].derivative_id
    same_contract = [point for point in futures[:-1] if point.derivative_id == latest_contract]
    return same_contract[-1] if same_contract else futures[-2]


def _effective_turnover(point: RawFuturesPoint | RawOptionPoint | None) -> float | None:
    if point is None:
        return None
    if point.turnover is not None:
        return point.turnover
    if point.price is None or point.volume is None:
        return None
    return float(point.price) * float(point.volume)


def _point_refs(*points: RawFuturesPoint | SpotMarketPoint | None) -> tuple[str, ...]:
    refs = []
    for point in points:
        if point is None or not point.raw_id:
            continue
        if isinstance(point, RawFuturesPoint):
            refs.append(f"raw_market.raw_candle:{point.raw_id}")
        else:
            refs.append(f"raw_market.raw_candle:{point.raw_id}")
    return tuple(refs)


def _option_refs(options: tuple[RawOptionPoint, ...]) -> tuple[str, ...]:
    return tuple(f"raw_market.raw_candle:{option.raw_id}" for option in options if option.raw_id)


def _basis_change_history(
    futures: tuple[RawFuturesPoint, ...],
    spots: tuple[SpotMarketPoint, ...],
    job: ModuleJob,
    max_alignment_seconds: int,
) -> tuple[float, ...]:
    basis_values: list[tuple[str, float]] = []
    for future in futures:
        spot = _closest_spot(spots, future.timestamp, max_alignment_seconds)
        basis = compute_futures_basis(future.price, spot.price if spot else None)
        if basis is not None:
            basis_values.append((future.timestamp, basis))
    changes = [
        current_basis - previous_basis
        for (_, previous_basis), (_, current_basis) in zip(basis_values, basis_values[1:], strict=False)
    ]
    del job
    return tuple(changes)


def _open_interest_change_history(futures: tuple[RawFuturesPoint, ...]) -> tuple[float, ...]:
    values = []
    for previous, current in zip(futures, futures[1:], strict=False):
        value = compute_open_interest_change(current.open_interest, previous.open_interest)
        if value is not None:
            values.append(value)
    return tuple(values)


def _volume_oi_ratio_history(futures: tuple[RawFuturesPoint, ...]) -> tuple[float, ...]:
    values = []
    for future in futures:
        value = compute_volume_oi_ratio(future.volume, future.open_interest)
        if value is not None:
            values.append(value)
    return tuple(values)


def _closest_spot(
    spots: tuple[SpotMarketPoint, ...],
    timestamp: str,
    max_alignment_seconds: int,
) -> SpotMarketPoint | None:
    if not timestamp:
        return None
    derivative_dt = parse_utc_iso(timestamp)
    candidates = []
    for spot in spots:
        if spot.price is None or spot.price <= 0 or not spot.timestamp:
            continue
        age = abs((derivative_dt - parse_utc_iso(spot.timestamp)).total_seconds())
        if age <= max_alignment_seconds:
            candidates.append((age, spot.timestamp, spot))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]


def _realized_volatility_from_spots(spots: tuple[SpotMarketPoint, ...]) -> float | None:
    prices = tuple(point.price for point in spots if point.price is not None and point.price > 0)
    return compute_realized_volatility(prices, 20)


def _futures_stale(
    futures: tuple[RawFuturesPoint, ...],
    job: ModuleJob,
    max_alignment_seconds: int,
) -> bool:
    timestamps = tuple(parse_utc_iso(point.timestamp) for point in futures if point.timestamp)
    if not timestamps:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(timestamps)).total_seconds() > max_alignment_seconds


def _options_stale(
    options: tuple[RawOptionPoint, ...],
    job: ModuleJob,
    max_alignment_seconds: int,
) -> bool:
    timestamps = tuple(parse_utc_iso(point.timestamp) for point in options if point.timestamp)
    if not timestamps:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(timestamps)).total_seconds() > max_alignment_seconds


def _spots_stale(
    spots: tuple[SpotMarketPoint, ...],
    job: ModuleJob,
    max_alignment_seconds: int,
) -> bool:
    timestamps = tuple(parse_utc_iso(point.timestamp) for point in spots if point.timestamp)
    if not timestamps:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(timestamps)).total_seconds() > max_alignment_seconds


def _required_non_negative_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise DerivativesPositioningError(f"{field_name} must be numeric") from error
    if parsed < 0:
        raise DerivativesPositioningError(f"{field_name} must be non-negative")
    return parsed


def _scale_positive_to_unit(value: float | None, upper_reference: float) -> float | None:
    if value is None:
        return None
    if upper_reference <= 0:
        return None
    return clip(float(value) / float(upper_reference), 0.0, 1.0)
