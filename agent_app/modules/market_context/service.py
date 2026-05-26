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
from agent_app.runtime_calendar import current_market_session

from .metrics import (
    average_pairwise_correlation,
    classify_correlation_regime,
    classify_liquidity_regime,
    classify_market_regime,
    classify_volatility_regime,
    clip,
    compute_log_return_series,
    compute_realized_volatility,
    compute_return,
    market_breadth,
    percentile_rank,
    regime_numeric_value,
    rolling_realized_volatility,
    weighted_average,
    z_to_unit,
    zscore,
    zscore_latest,
)
from .repository import (
    FeatureRecord,
    InMemoryMarketContextRepository,
    MarketContextRepository,
    MarketStateRecord,
    RawCandle,
    RawIndexValue,
    RawMacroPoint,
    StructuredEvent,
    stable_record_id,
)


MODULE_NAME = "Market Context Module"
CALCULATION_VERSION = "market_context_v1"

VALID_CONTOURS = {"global_contour", "daily_contour", "event_contour"}
VALID_HORIZONS = {"intraday", "swing", "position"}
INPUT_FIELDS = {
    "universe_id",
    "instrument_ids",
    "macro_refs",
    "index_refs",
    "sector_refs",
    "event_refs",
    "windows",
}
DOCUMENTED_WINDOWS = {5, 20, 60}

GLOBAL_TTL_SECONDS = 6 * 60 * 60
DAILY_TTL_SECONDS = 24 * 60 * 60
MACRO_TTL_SECONDS = 24 * 60 * 60
HISTORY_WINDOW = 252
DEFAULT_SECTOR_WINDOW = 5
DEFAULT_CORRELATION_WINDOW = 20
DEFAULT_INDEX_VOL_WINDOW = 20

DEFAULT_RISK_ON_WEIGHTS = {
    "market_return_z": 1.0,
    "breadth_z": 1.0,
    "negative_volatility_percentile": 1.0,
    "negative_ofz_change_z": 1.0,
}
DEFAULT_MACRO_PRESSURE_WEIGHTS = {
    "key_rate_change_z": 1.0,
    "ofz_change_z": 1.0,
    "currency_return_z": 1.0,
    "oil_return_z": 1.0,
}
DEFAULT_SECTOR_PRESSURE_WEIGHTS = {
    "sector_strength_rank": 1.0,
    "sector_news_score": 1.0,
    "sector_volatility_regime": 1.0,
}


class MarketContextError(ValueError):
    """Raised when market context processing violates module documentation."""


@dataclass(frozen=True)
class MarketContextInput:
    universe_id: str
    instrument_ids: tuple[str, ...]
    macro_refs: tuple[str, ...]
    index_refs: tuple[str, ...]
    sector_refs: tuple[str, ...]
    event_refs: tuple[str, ...]
    windows: tuple[int, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "MarketContextInput":
        input_payload = payload.get("market_context_input")
        if not isinstance(input_payload, Mapping):
            raise MarketContextError("payload must contain market_context_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise MarketContextError(f"market_context_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise MarketContextError(f"market_context_input has undocumented fields: {extra_fields}")

        universe_id = str(input_payload.get("universe_id") or "")
        if not universe_id:
            raise MarketContextError("market_context_input.universe_id is required")
        if universe_id != job.universe_id:
            raise MarketContextError("market_context_input.universe_id must match module_job.universe_id")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise MarketContextError("market_context_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise MarketContextError("market_context_input.instrument_ids must match module_job.instrument_ids")

        windows = tuple(int(item) for item in (input_payload.get("windows") or ()))
        if not windows:
            raise MarketContextError("market_context_input.windows is required")
        invalid_windows = sorted(set(windows) - DOCUMENTED_WINDOWS)
        if invalid_windows:
            raise MarketContextError(f"invalid windows: {invalid_windows}")

        return cls(
            universe_id=universe_id,
            instrument_ids=instrument_ids,
            macro_refs=tuple(str(item) for item in (input_payload.get("macro_refs") or ())),
            index_refs=tuple(str(item) for item in (input_payload.get("index_refs") or ())),
            sector_refs=tuple(str(item) for item in (input_payload.get("sector_refs") or ())),
            event_refs=tuple(str(item) for item in (input_payload.get("event_refs") or ())),
            windows=windows,
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
class MarketContextExecutionResult:
    module_job_result: ModuleJobResult
    feature_records: tuple[FeatureRecord, ...]
    market_state_records: tuple[MarketStateRecord, ...]
    feature_record_refs: tuple[str, ...]
    market_state_refs: tuple[str, ...]

    @property
    def market_state_record(self) -> MarketStateRecord | None:
        if len(self.market_state_records) == 1:
            return self.market_state_records[0]
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "feature_records": [record.to_dict() for record in self.feature_records],
            "market_state_records": [record.to_dict() for record in self.market_state_records],
            "feature_record_refs": list(self.feature_record_refs),
            "market_state_refs": list(self.market_state_refs),
        }
        if len(self.market_state_records) == 1:
            payload["market_state_record"] = self.market_state_records[0].to_dict()
        return payload


class MarketContextService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: MarketContextRepository | None = None,
        gateway: Any | None = None,
        risk_on_weights: Mapping[str, float] | None = None,
        macro_pressure_weights: Mapping[str, float] | None = None,
        sector_pressure_weights: Mapping[str, float] | None = None,
    ) -> None:
        self.repository = repository or InMemoryMarketContextRepository()
        self.gateway = gateway
        self.risk_on_weights = dict(risk_on_weights or DEFAULT_RISK_ON_WEIGHTS)
        self.macro_pressure_weights = dict(macro_pressure_weights or DEFAULT_MACRO_PRESSURE_WEIGHTS)
        self.sector_pressure_weights = dict(sector_pressure_weights or DEFAULT_SECTOR_PRESSURE_WEIGHTS)

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> MarketContextExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> MarketContextExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> MarketContextExecutionResult:
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
            return MarketContextExecutionResult(result, (), (), (), ())

        try:
            self.validate_module_job(job)
            market_input = MarketContextInput.from_dict(payload, job)
            macro_points = self.repository.list_macro_points(
                market_input.macro_refs,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )
            index_values = self.repository.list_index_values(
                market_input.index_refs + market_input.sector_refs,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )
            candles = self.repository.list_candles(
                universe_id=job.universe_id,
                instrument_ids=market_input.instrument_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            structured_events = self.repository.list_structured_events(
                event_refs=market_input.event_refs,
                universe_id=job.universe_id,
                instrument_ids=market_input.instrument_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            sector_mappings = self.repository.list_sector_mappings(job.universe_id, market_input.instrument_ids)

            warnings: list[str] = []
            external_requests = self.create_external_requests_for_missing_raw_data(
                market_input=market_input,
                job=job,
                macro_points=macro_points,
                index_values=index_values,
                candles=candles,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self.gateway.process(request)
            warnings.extend(f"external_request_created:{request.request_id}" for request in external_requests)

            if not macro_points and not index_values and not candles and not structured_events:
                warnings.append("raw_market_context_data_missing")
                return MarketContextExecutionResult(
                    module_job_result=self._module_job_result(
                        job=job,
                        started_at=started_at,
                        status="skipped",
                        output_refs=(),
                        warnings=tuple(dict.fromkeys(warnings)),
                        errors=(),
                        metrics_written=0,
                    ),
                    feature_records=(),
                    market_state_records=(),
                    feature_record_refs=(),
                    market_state_refs=(),
                )

            feature_records, market_state_records, compute_warnings = self.compute_outputs(
                market_input=market_input,
                job=job,
                macro_points=macro_points,
                index_values=index_values,
                candles=candles,
                structured_events=structured_events,
                sector_mappings=sector_mappings,
            )
            warnings.extend(compute_warnings)

            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            market_state_refs = tuple(self.write_market_state_record(record) for record in market_state_records)
            output_refs = feature_refs + market_state_refs
            if not output_refs:
                warnings.append("raw_market_context_data_missing")

            status = "success" if feature_records and market_state_records and not warnings else "partial_success" if output_refs else "skipped"
            return MarketContextExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(feature_records),
                ),
                feature_records=feature_records,
                market_state_records=market_state_records,
                feature_record_refs=feature_refs,
                market_state_refs=market_state_refs,
            )
        except (MarketContextError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise MarketContextError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise MarketContextError("module_job.module_name must be Market Context Module")
        if job.contour not in VALID_CONTOURS:
            raise MarketContextError("module_job.contour must be global_contour, daily_contour or event_contour")
        if not job.universe_id:
            raise MarketContextError("module_job.universe_id is required")
        if not job.horizons:
            raise MarketContextError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise MarketContextError(f"invalid module_job.horizons: {invalid_horizons}")
        if not job.run_mode:
            raise MarketContextError("module_job.run_mode is required")

    def compute_outputs(
        self,
        *,
        market_input: MarketContextInput,
        job: ModuleJob,
        macro_points: tuple[RawMacroPoint, ...],
        index_values: tuple[RawIndexValue, ...],
        candles: tuple[RawCandle, ...],
        structured_events: tuple[StructuredEvent, ...],
        sector_mappings: Mapping[str, str],
    ) -> tuple[tuple[FeatureRecord, ...], tuple[MarketStateRecord, ...], tuple[str, ...]]:
        warnings: list[str] = []
        if not macro_points:
            warnings.append("macro_points_missing")
        if not index_values:
            warnings.append("index_values_missing")
        if not candles:
            warnings.append("raw_candles_missing")

        source_refs = _context_refs(market_input)
        stale_macro = _macro_points_stale(macro_points, job)
        if stale_macro:
            warnings.append("stale_macro_downweighted")

        macro_metrics, macro_context, macro_warnings = self.compute_macro_context(
            market_input=market_input,
            macro_points=macro_points,
            source_refs=source_refs,
            stale_macro=stale_macro,
        )
        warnings.extend(macro_warnings)

        market_context = self.compute_market_context(
            market_input=market_input,
            index_values=index_values,
            candles=candles,
            sector_mappings=sector_mappings,
        )
        warnings.extend(market_context["warnings"])

        event_context = self.compute_event_context(
            events=structured_events,
            sector_mappings=sector_mappings,
            instrument_ids=market_input.instrument_ids,
        )

        volatility_regime = classify_volatility_regime(market_context.get("index_volatility_percentile"))
        correlation_regime = classify_correlation_regime(market_context.get("average_pairwise_correlation"))
        liquidity_regime = classify_liquidity_regime(
            market_context.get("market_turnover_percentile"),
            stress=volatility_regime == "extreme",
        )
        market_regime = classify_market_regime(
            market_return_z=market_context.get("market_return_z"),
            market_breadth_value=market_context.get("market_breadth"),
            volatility_regime=volatility_regime,
        )

        risk_on_risk_off_score = self.compute_risk_on_risk_off_score(
            market_context=market_context,
            macro_context=macro_context,
        )

        quality_flags = tuple(dict.fromkeys(warnings))
        has_context_evidence = self._has_context_evidence(macro_metrics, market_context, event_context)
        confidence_score = self._confidence_score(quality_flags, has_outputs=has_context_evidence)
        as_of_ts = self._as_of_timestamp(job, macro_points, index_values, candles)

        market_state = self.build_market_state_record(
            job=job,
            as_of_ts=as_of_ts,
            market_regime=market_regime,
            volatility_regime=volatility_regime,
            liquidity_regime=liquidity_regime,
            correlation_regime=correlation_regime,
            risk_on_risk_off_score=risk_on_risk_off_score,
            confidence_score=confidence_score,
            source_refs=source_refs,
            quality_flags=quality_flags,
            payload={
                "market_context": _jsonable_metrics(market_context),
                "macro_context": _jsonable_metrics(macro_context),
                "event_context": _jsonable_metrics(event_context),
                "windows": list(market_input.windows),
                "freshness_status": "stale" if stale_macro else "fresh",
                "calculation_version": CALCULATION_VERSION,
            },
        )

        feature_records: list[FeatureRecord] = []
        sector_rank_by_instrument = market_context["sector_strength_rank_by_instrument"]
        sector_volatility_by_instrument = market_context["sector_volatility_percentile_by_instrument"]
        sector_news_score_by_instrument = event_context["sector_news_score_by_instrument"]

        for instrument_id in market_input.instrument_ids:
            sector = sector_mappings.get(instrument_id, "")
            instrument_metrics = list(macro_metrics)
            instrument_metrics.extend(
                self.market_metric_values(
                    source_refs=source_refs,
                    market_context=market_context,
                    macro_context=macro_context,
                    market_state=market_state,
                )
            )
            sector_strength_rank = sector_rank_by_instrument.get(instrument_id)
            if sector_strength_rank is not None:
                instrument_metrics.append(
                    MetricValue(
                        metric_name="sector_strength_rank",
                        metric_type="derived_metric",
                        raw_value=sector_strength_rank,
                        normalized_value=sector_strength_rank,
                        unit="rank_0_1",
                        ttl_seconds=DAILY_TTL_SECONDS,
                        source_refs=source_refs,
                        payload={
                            "formula": "cross-sectional rank of sector returns over configured window",
                            "sector": sector,
                            "sector_window": market_context.get("sector_window"),
                            "calculation_version": CALCULATION_VERSION,
                        },
                    )
                )

            macro_pressure_score = self.compute_macro_pressure_score(
                macro_context=macro_context,
                sector=sector,
            )
            if macro_pressure_score is not None:
                instrument_metrics.append(
                    MetricValue(
                        metric_name="macro_pressure_score",
                        metric_type="composite_score",
                        raw_value=macro_pressure_score,
                        normalized_value=macro_pressure_score,
                        unit="score",
                        ttl_seconds=DAILY_TTL_SECONDS,
                        source_refs=source_refs,
                        confidence_score=0.55 if stale_macro else 1.0,
                        quality_flags=("stale_macro_downweighted",) if stale_macro else (),
                        payload={
                            "formula": "WAvg([key_rate_change_z, ofz_change_z, currency_return_z, oil_return_z], active_weights) with sector-specific signs",
                            "sector": sector,
                            "component_values": _jsonable_metrics(macro_context.get("macro_pressure_components_by_sector", {}).get(sector, {})),
                            "weight_source": "calculation_config",
                            "component_weights": dict(self.macro_pressure_weights),
                            "calculation_version": CALCULATION_VERSION,
                        },
                    )
                )

            sector_pressure_score = self.compute_sector_pressure_score(
                sector_strength_rank=sector_strength_rank,
                sector_news_score=sector_news_score_by_instrument.get(instrument_id),
                sector_volatility_percentile=sector_volatility_by_instrument.get(instrument_id),
            )
            if sector_pressure_score is not None:
                instrument_metrics.append(
                    MetricValue(
                        metric_name="sector_pressure_score",
                        metric_type="composite_score",
                        raw_value=sector_pressure_score,
                        normalized_value=sector_pressure_score,
                        unit="score",
                        ttl_seconds=DAILY_TTL_SECONDS,
                        source_refs=source_refs,
                        payload={
                            "formula": "WAvg([sector_strength_rank, sector_news_score, sector_volatility_regime], active_weights)",
                            "sector": sector,
                            "component_values": {
                                "sector_strength_rank": sector_strength_rank,
                                "sector_news_score": sector_news_score_by_instrument.get(instrument_id),
                                "sector_volatility_regime": sector_volatility_by_instrument.get(instrument_id),
                            },
                            "weight_source": "calculation_config",
                            "component_weights": dict(self.sector_pressure_weights),
                            "calculation_version": CALCULATION_VERSION,
                        },
                    )
                )

            for metric_value in instrument_metrics:
                for horizon in job.horizons:
                    feature_records.append(
                        self.build_feature_record(
                            instrument_id=instrument_id,
                            metric_value=metric_value,
                            horizon=horizon,
                            contour=job.contour,
                            timestamp=as_of_ts,
                            quality_flags=tuple(dict.fromkeys(quality_flags + metric_value.quality_flags)),
                        )
                    )

        return tuple(feature_records), (market_state,), tuple(dict.fromkeys(warnings))

    def compute_macro_context(
        self,
        *,
        market_input: MarketContextInput,
        macro_points: tuple[RawMacroPoint, ...],
        source_refs: tuple[str, ...],
        stale_macro: bool,
    ) -> tuple[tuple[MetricValue, ...], dict[str, Any], tuple[str, ...]]:
        del market_input
        warnings: list[str] = []
        metrics: list[MetricValue] = []
        by_series = _group_macro_points(macro_points)

        key_rate = _select_macro_series(by_series, "key_rate")
        ofz_1y = _select_macro_series(by_series, "ofz_1y")
        ofz_2y = _select_macro_series(by_series, "ofz_2y")
        ofz_10y = _select_macro_series(by_series, "ofz_10y")
        currency = _select_macro_series(by_series, "currency")
        oil = _select_macro_series(by_series, "oil")
        market_earnings_yield = _select_macro_series(by_series, "market_earnings_yield")

        key_rate_level = _latest_value(key_rate)
        key_rate_change = _latest_delta(key_rate)
        ofz_1y_yield = _latest_value(ofz_1y)
        ofz_2y_yield = _latest_value(ofz_2y)
        ofz_10y_yield = _latest_value(ofz_10y)
        yield_curve_slope = None
        if ofz_10y_yield is not None and ofz_2y_yield is not None:
            yield_curve_slope = ofz_10y_yield - ofz_2y_yield
        equity_risk_premium_proxy = None
        earnings_yield = _latest_value(market_earnings_yield)
        if earnings_yield is not None and ofz_10y_yield is not None:
            equity_risk_premium_proxy = earnings_yield - ofz_10y_yield

        currency_values = _series_values(currency)
        oil_values = _series_values(oil)
        currency_return_1d = compute_return(currency_values, 1)
        currency_return_5d = compute_return(currency_values, 5)
        oil_return_1d = compute_return(oil_values, 1)
        oil_return_5d = compute_return(oil_values, 5)

        key_rate_deltas = _series_deltas(key_rate)
        ofz_10y_deltas = _series_deltas(ofz_10y)
        currency_returns = _simple_return_series(currency_values)
        oil_returns = _simple_return_series(oil_values)
        key_rate_change_z = zscore_latest(key_rate_deltas)
        ofz_change_z = zscore_latest(ofz_10y_deltas)
        currency_return_z = zscore_latest(currency_returns)
        oil_return_z = zscore_latest(oil_returns)

        self._append_metric(metrics, "key_rate_level", "raw_metric", key_rate_level, None, "percent", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "key_rate_change", "derived_metric", key_rate_change, None, "percentage_points", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "ofz_1y_yield", "raw_metric", ofz_1y_yield, None, "percent", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "ofz_2y_yield", "raw_metric", ofz_2y_yield, None, "percent", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "ofz_10y_yield", "raw_metric", ofz_10y_yield, None, "percent", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "yield_curve_slope", "derived_metric", yield_curve_slope, None, "percentage_points", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "equity_risk_premium_proxy", "derived_metric", equity_risk_premium_proxy, None, "percentage_points", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "currency_return_1d", "derived_metric", currency_return_1d, None, "ratio", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "currency_return_5d", "derived_metric", currency_return_5d, None, "ratio", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "oil_return_1d", "derived_metric", oil_return_1d, None, "ratio", source_refs, stale_macro=stale_macro)
        self._append_metric(metrics, "oil_return_5d", "derived_metric", oil_return_5d, None, "ratio", source_refs, stale_macro=stale_macro)

        for name, series in (
            ("key_rate", key_rate),
            ("ofz_2y", ofz_2y),
            ("ofz_10y", ofz_10y),
            ("currency", currency),
            ("oil", oil),
        ):
            if not series:
                warnings.append(f"missing_macro_series:{name}")

        macro_context: dict[str, Any] = {
            "key_rate_level": key_rate_level,
            "key_rate_change": key_rate_change,
            "ofz_1y_yield": ofz_1y_yield,
            "ofz_2y_yield": ofz_2y_yield,
            "ofz_10y_yield": ofz_10y_yield,
            "yield_curve_slope": yield_curve_slope,
            "equity_risk_premium_proxy": equity_risk_premium_proxy,
            "currency_return_1d": currency_return_1d,
            "currency_return_5d": currency_return_5d,
            "oil_return_1d": oil_return_1d,
            "oil_return_5d": oil_return_5d,
            "key_rate_change_z": key_rate_change_z,
            "ofz_change_z": ofz_change_z,
            "currency_return_z": currency_return_z,
            "oil_return_z": oil_return_z,
            "stale_macro": stale_macro,
            "macro_pressure_components_by_sector": {},
        }
        return tuple(metrics), macro_context, tuple(dict.fromkeys(warnings))

    def compute_market_context(
        self,
        *,
        market_input: MarketContextInput,
        index_values: tuple[RawIndexValue, ...],
        candles: tuple[RawCandle, ...],
        sector_mappings: Mapping[str, str],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        market_index_values = _select_index_series(index_values, market_input.index_refs)
        market_prices = _index_price_values(market_index_values)
        market_return_1d = compute_return(market_prices, 1)
        market_returns = _simple_return_series(market_prices)
        market_return_z = zscore_latest(market_returns)

        index_volatility_percentile = None
        index_volatility_regime_value = None
        index_log_returns = compute_log_return_series(market_prices)
        rolling_index_vol = rolling_realized_volatility(index_log_returns, DEFAULT_INDEX_VOL_WINDOW)
        if rolling_index_vol:
            index_volatility_percentile = percentile_rank(rolling_index_vol[-1], rolling_index_vol[-HISTORY_WINDOW:])
            index_volatility_regime_value = index_volatility_percentile

        candles_by_instrument = _group_daily_candles(candles, market_input.instrument_ids)
        latest_returns = _latest_returns_by_instrument(candles_by_instrument)
        breadth = market_breadth(latest_returns)
        if breadth is None:
            warnings.append("market_breadth_missing")

        breadth_history = _breadth_history(candles_by_instrument)
        breadth_z = zscore_latest(breadth_history)
        if breadth is not None and breadth_z is None:
            breadth_z = (breadth - 0.5) / 0.25

        sector_window = _preferred_window(market_input.windows, DEFAULT_SECTOR_WINDOW)
        sector_returns = _sector_returns(candles_by_instrument, sector_mappings, sector_window)
        sector_strength_ranks = _rank_mapping(sector_returns)
        sector_strength_rank_by_instrument = {
            instrument_id: sector_strength_ranks.get(sector)
            for instrument_id, sector in sector_mappings.items()
            if instrument_id in set(market_input.instrument_ids)
        }
        if not sector_strength_rank_by_instrument:
            warnings.append("sector_mapping_missing")

        max_return_count = max(
            (len(compute_log_return_series(_close_values(items))) for items in candles_by_instrument.values()),
            default=0,
        )
        risk_window = _available_window(market_input.windows, DEFAULT_CORRELATION_WINDOW, max_return_count)
        sector_volatility = _sector_volatility(candles_by_instrument, sector_mappings, risk_window)
        sector_volatility_ranks = _rank_mapping(sector_volatility)
        sector_volatility_percentile_by_instrument = {
            instrument_id: sector_volatility_ranks.get(sector)
            for instrument_id, sector in sector_mappings.items()
            if instrument_id in set(market_input.instrument_ids)
        }

        correlation_window = risk_window
        instrument_return_series = [
            compute_log_return_series(_close_values(items))
            for items in candles_by_instrument.values()
            if len(_close_values(items)) > correlation_window
        ]
        average_correlation = average_pairwise_correlation(instrument_return_series, correlation_window)
        if average_correlation is None and len(market_input.instrument_ids) > 1:
            warnings.append("insufficient_correlation_history")

        market_turnover_percentile = _market_turnover_percentile(candles_by_instrument)

        return {
            "market_return_1d": market_return_1d,
            "market_return_z": market_return_z,
            "market_returns": market_returns,
            "market_breadth": breadth,
            "breadth_z": breadth_z,
            "index_volatility_percentile": index_volatility_percentile,
            "index_volatility_regime_value": index_volatility_regime_value,
            "average_pairwise_correlation": average_correlation,
            "market_turnover_percentile": market_turnover_percentile,
            "sector_window": sector_window,
            "sector_returns": sector_returns,
            "sector_strength_ranks": sector_strength_ranks,
            "sector_strength_rank_by_instrument": sector_strength_rank_by_instrument,
            "sector_volatility": sector_volatility,
            "sector_volatility_percentile_by_instrument": sector_volatility_percentile_by_instrument,
            "correlation_window": correlation_window,
            "warnings": tuple(dict.fromkeys(warnings)),
        }

    def compute_event_context(
        self,
        *,
        events: tuple[StructuredEvent, ...],
        sector_mappings: Mapping[str, str],
        instrument_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        market_scores: list[float] = []
        sector_scores: dict[str, list[float]] = {}
        sector_by_instrument = dict(sector_mappings)
        requested = set(instrument_ids)
        for event in events:
            pressure = _event_pressure_score(event)
            if pressure is None:
                continue
            sectors = _event_sectors(event, sector_by_instrument, requested)
            if sectors:
                for sector in sectors:
                    sector_scores.setdefault(sector, []).append(pressure)
            else:
                market_scores.append(pressure)

        sector_news_by_sector = {
            sector: sum(scores) / len(scores)
            for sector, scores in sector_scores.items()
            if scores
        }
        market_news_score = sum(market_scores) / len(market_scores) if market_scores else None
        sector_news_score_by_instrument = {
            instrument_id: sector_news_by_sector.get(sector_by_instrument.get(instrument_id, ""), market_news_score)
            for instrument_id in instrument_ids
        }
        return {
            "market_news_score": market_news_score,
            "sector_news_by_sector": sector_news_by_sector,
            "sector_news_score_by_instrument": sector_news_score_by_instrument,
        }

    def market_metric_values(
        self,
        *,
        source_refs: tuple[str, ...],
        market_context: Mapping[str, Any],
        macro_context: Mapping[str, Any],
        market_state: MarketStateRecord,
    ) -> tuple[MetricValue, ...]:
        metrics: list[MetricValue] = []
        self._append_metric(
            metrics,
            "market_breadth",
            "derived_metric",
            market_context.get("market_breadth"),
            market_context.get("market_breadth"),
            "ratio",
            source_refs,
            ttl_seconds=GLOBAL_TTL_SECONDS,
            payload={"formula": "advancing_instruments / active_instruments", "calculation_version": CALCULATION_VERSION},
        )
        self._append_metric(
            metrics,
            "index_volatility_regime",
            "derived_metric",
            regime_numeric_value(market_state.volatility_regime),
            market_context.get("index_volatility_percentile"),
            "regime_bucket",
            source_refs,
            ttl_seconds=GLOBAL_TTL_SECONDS,
            payload={
                "formula": "bucket from PctRank(index_realized_vol_20d, 252d)",
                "regime": market_state.volatility_regime,
                "volatility_percentile": market_context.get("index_volatility_percentile"),
                "calculation_version": CALCULATION_VERSION,
            },
        )
        for metric_name, regime in (
            ("market_regime", market_state.market_regime),
            ("volatility_regime", market_state.volatility_regime),
            ("liquidity_regime", market_state.liquidity_regime),
            ("correlation_regime", market_state.correlation_regime),
        ):
            self._append_metric(
                metrics,
                metric_name,
                "derived_metric",
                regime_numeric_value(regime),
                regime_numeric_value(regime),
                "regime_bucket",
                source_refs,
                ttl_seconds=GLOBAL_TTL_SECONDS,
                payload={
                    "formula": f"{metric_name} documented rule classifier",
                    "regime": regime,
                    "calculation_version": CALCULATION_VERSION,
                },
            )
        self._append_metric(
            metrics,
            "risk_on_risk_off_score",
            "composite_score",
            market_state.risk_on_risk_off_score,
            market_state.risk_on_risk_off_score,
            "score",
            source_refs,
            ttl_seconds=GLOBAL_TTL_SECONDS,
            payload={
                "formula": "WAvg([market_return_z, breadth_z, -volatility_percentile, -ofz_change_z], active_weights)",
                "component_values": {
                    "market_return_z": z_to_unit(market_context.get("market_return_z")),
                    "breadth_z": z_to_unit(market_context.get("breadth_z")),
                    "negative_volatility_percentile": None
                    if market_context.get("index_volatility_percentile") is None
                    else 1.0 - float(market_context["index_volatility_percentile"]),
                    "negative_ofz_change_z": None
                    if macro_context.get("ofz_change_z") is None
                    else 1.0 - (z_to_unit(macro_context.get("ofz_change_z")) or 0.5),
                },
                "weight_source": "calculation_config",
                "component_weights": dict(self.risk_on_weights),
                "calculation_version": CALCULATION_VERSION,
            },
        )
        return tuple(metrics)

    def compute_risk_on_risk_off_score(
        self,
        *,
        market_context: Mapping[str, Any],
        macro_context: Mapping[str, Any],
    ) -> float | None:
        volatility_percentile = market_context.get("index_volatility_percentile")
        ofz_change_z = macro_context.get("ofz_change_z")
        components = {
            "market_return_z": z_to_unit(market_context.get("market_return_z")),
            "breadth_z": z_to_unit(market_context.get("breadth_z")),
            "negative_volatility_percentile": None if volatility_percentile is None else 1.0 - float(volatility_percentile),
            "negative_ofz_change_z": None if ofz_change_z is None else 1.0 - (z_to_unit(ofz_change_z) or 0.5),
        }
        return weighted_average(components, self.risk_on_weights)

    def compute_macro_pressure_score(
        self,
        *,
        macro_context: dict[str, Any],
        sector: str,
    ) -> float | None:
        oil_z = macro_context.get("oil_return_z")
        oil_pressure = None
        if oil_z is not None:
            oil_pressure = z_to_unit(float(oil_z) * _sector_oil_pressure_sign(sector))
        components = {
            "key_rate_change_z": z_to_unit(macro_context.get("key_rate_change_z")),
            "ofz_change_z": z_to_unit(macro_context.get("ofz_change_z")),
            "currency_return_z": z_to_unit(macro_context.get("currency_return_z")),
            "oil_return_z": oil_pressure,
        }
        sector_components = macro_context.setdefault("macro_pressure_components_by_sector", {})
        sector_components[sector] = components
        return weighted_average(components, self.macro_pressure_weights)

    def compute_sector_pressure_score(
        self,
        *,
        sector_strength_rank: float | None,
        sector_news_score: float | None,
        sector_volatility_percentile: float | None,
    ) -> float | None:
        components = {
            "sector_strength_rank": None if sector_strength_rank is None else 1.0 - float(sector_strength_rank),
            "sector_news_score": sector_news_score,
            "sector_volatility_regime": sector_volatility_percentile,
        }
        return weighted_average(components, self.sector_pressure_weights)

    def build_feature_record(
        self,
        *,
        instrument_id: str,
        metric_value: MetricValue,
        horizon: str,
        contour: str,
        timestamp: str,
        quality_flags: tuple[str, ...],
    ) -> FeatureRecord:
        payload = {
            **dict(metric_value.payload),
            "formula_version": CALCULATION_VERSION,
            "selected_universe_filter": "module_job.instrument_ids",
        }
        feature_payload = {
            "instrument_id": instrument_id,
            "metric_name": metric_value.metric_name,
            "horizon": horizon,
            "timestamp": timestamp,
            "calculation_version": CALCULATION_VERSION,
        }
        confidence_score = min(metric_value.confidence_score, self._confidence_score(quality_flags, has_outputs=True))
        return FeatureRecord(
            feature_id=stable_record_id("feature", feature_payload),
            instrument_id=instrument_id,
            metric_name=metric_value.metric_name,
            metric_group="market_context",
            metric_type=metric_value.metric_type,
            raw_value=float(metric_value.raw_value),
            normalized_value=metric_value.normalized_value,
            unit=metric_value.unit,
            horizon=horizon,
            contour=contour,
            timestamp=timestamp,
            ttl_seconds=metric_value.ttl_seconds,
            confidence_score=confidence_score,
            source_module=self.module_name,
            source_refs=metric_value.source_refs,
            calculation_version=CALCULATION_VERSION,
            quality_flags=quality_flags,
            payload=payload,
        )

    def build_market_state_record(
        self,
        *,
        job: ModuleJob,
        as_of_ts: str,
        market_regime: str,
        volatility_regime: str,
        liquidity_regime: str,
        correlation_regime: str,
        risk_on_risk_off_score: float | None,
        confidence_score: float,
        source_refs: tuple[str, ...],
        quality_flags: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> MarketStateRecord:
        record_payload = {
            "universe_id": job.universe_id,
            "as_of_ts": as_of_ts,
            "market_regime": market_regime,
            "volatility_regime": volatility_regime,
            "liquidity_regime": liquidity_regime,
            "correlation_regime": correlation_regime,
            "calculation_version": CALCULATION_VERSION,
        }
        runtime_session = current_market_session()
        return MarketStateRecord(
            market_state_id=stable_record_id("market_state", record_payload),
            universe_id=job.universe_id,
            as_of_ts=as_of_ts,
            market_regime=market_regime,
            volatility_regime=volatility_regime,
            liquidity_regime=liquidity_regime,
            correlation_regime=correlation_regime,
            risk_on_risk_off_score=None if risk_on_risk_off_score is None else float(risk_on_risk_off_score),
            confidence_score=confidence_score,
            source_refs=source_refs,
            market_session_status=runtime_session.market_session_status,
            source_module=self.module_name,
            calculation_version=CALCULATION_VERSION,
            ttl_seconds=GLOBAL_TTL_SECONDS if job.contour == "global_contour" else DAILY_TTL_SECONDS,
            quality_flags=quality_flags,
            payload={
                **dict(payload),
                "job_id": job.job_id,
                "horizons": list(job.horizons),
                "ttl_seconds": GLOBAL_TTL_SECONDS if job.contour == "global_contour" else DAILY_TTL_SECONDS,
                "market_session_reason": runtime_session.reason,
                "agent_runtime_phase": runtime_session.agent_runtime_phase,
            },
        )

    def create_external_requests_for_missing_raw_data(
        self,
        *,
        market_input: MarketContextInput,
        job: ModuleJob,
        macro_points: tuple[RawMacroPoint, ...],
        index_values: tuple[RawIndexValue, ...],
        candles: tuple[RawCandle, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if market_input.macro_refs and (not macro_points or _macro_points_stale(macro_points, job)):
            requests.append(self._external_request(job, market_input, "macro_api", "macro_series"))
        if (
            ((market_input.index_refs or market_input.sector_refs) and not index_values)
            or not candles
            or _index_values_stale(index_values, job)
            or _candles_stale(candles, job)
        ):
            requests.append(self._external_request(job, market_input, "moex_iss", "market_data"))
        return tuple(requests)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_market_state_record(self, record: MarketStateRecord) -> str:
        return self.repository.save_market_state_record(record)

    def _append_metric(
        self,
        metrics: list[MetricValue],
        metric_name: str,
        metric_type: str,
        raw_value: float | None,
        normalized_value: float | None,
        unit: str,
        source_refs: tuple[str, ...],
        *,
        ttl_seconds: int = DAILY_TTL_SECONDS,
        payload: Mapping[str, Any] | None = None,
        stale_macro: bool = False,
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
                confidence_score=0.55 if stale_macro else 1.0,
                quality_flags=("stale_macro_downweighted",) if stale_macro else (),
                payload={
                    "formula": metric_name,
                    "calculation_version": CALCULATION_VERSION,
                    **dict(payload or {}),
                },
            )
        )

    def _external_request(
        self,
        job: ModuleJob,
        market_input: MarketContextInput,
        provider: str,
        request_type: str,
    ) -> ExternalRequest:
        payload = {
            "macro_refs": list(market_input.macro_refs),
            "index_refs": list(market_input.index_refs),
            "sector_refs": list(market_input.sector_refs),
            "event_refs": list(market_input.event_refs),
            "windows": list(market_input.windows),
            "time_range": job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:{request_type}"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider=provider,
            request_type=request_type,
            universe_id=job.universe_id,
            instrument_ids=market_input.instrument_ids,
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=GLOBAL_TTL_SECONDS, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _as_of_timestamp(
        self,
        job: ModuleJob,
        macro_points: tuple[RawMacroPoint, ...],
        index_values: tuple[RawIndexValue, ...],
        candles: tuple[RawCandle, ...],
    ) -> str:
        candidates: list[str] = []
        candidates.extend(point.point_ts for point in macro_points if point.point_ts)
        candidates.extend(value.value_ts for value in index_values if value.value_ts)
        candidates.extend(candle.close_ts for candle in candles if candle.close_ts)
        if not candidates:
            return job.time_range.to_ts
        return max(candidates, key=lambda ts: parse_utc_iso(ts))

    def _confidence_score(self, quality_flags: tuple[str, ...], *, has_outputs: bool) -> float:
        if not has_outputs:
            return 0.0
        if "stale_macro_downweighted" in quality_flags:
            return 0.55
        if any(flag.endswith("_missing") or flag.startswith("missing_") for flag in quality_flags):
            return 0.75
        if quality_flags:
            return 0.85
        return 1.0

    def _has_context_evidence(
        self,
        macro_metrics: tuple[MetricValue, ...],
        market_context: Mapping[str, Any],
        event_context: Mapping[str, Any],
    ) -> bool:
        if macro_metrics:
            return True
        market_keys = (
            "market_return_1d",
            "market_return_z",
            "market_breadth",
            "index_volatility_percentile",
            "average_pairwise_correlation",
            "market_turnover_percentile",
        )
        if any(market_context.get(key) is not None for key in market_keys):
            return True
        if event_context.get("market_news_score") is not None:
            return True
        sector_scores = event_context.get("sector_news_score_by_instrument")
        return isinstance(sector_scores, Mapping) and any(value is not None for value in sector_scores.values())

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
            data_quality_score=1.0 if not warnings and not errors else 0.75 if metrics_written or output_refs else 0.0,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> MarketContextExecutionResult:
        return MarketContextExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
            ),
            feature_records=(),
            market_state_records=(),
            feature_record_refs=(),
            market_state_refs=(),
        )


def _context_refs(market_input: MarketContextInput) -> tuple[str, ...]:
    return tuple(
        ref
        for ref in (
            *market_input.macro_refs,
            *market_input.index_refs,
            *market_input.sector_refs,
            *market_input.event_refs,
        )
        if ref
    )


def _group_macro_points(points: tuple[RawMacroPoint, ...]) -> dict[str, tuple[RawMacroPoint, ...]]:
    grouped: dict[str, list[RawMacroPoint]] = {}
    for point in points:
        if point.value is None or not point.point_ts:
            continue
        grouped.setdefault(point.series_id, []).append(point)
    return {
        series_id: tuple(sorted(items, key=lambda item: item.point_ts))
        for series_id, items in grouped.items()
    }


def _select_macro_series(
    grouped: Mapping[str, tuple[RawMacroPoint, ...]],
    target: str,
) -> tuple[RawMacroPoint, ...]:
    candidates: list[tuple[int, str, tuple[RawMacroPoint, ...]]] = []
    for series_id, points in grouped.items():
        score = _macro_series_score(series_id, points, target)
        if score > 0:
            candidates.append((score, series_id, points))
    if not candidates:
        return ()
    return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][2]


def _macro_series_score(series_id: str, points: tuple[RawMacroPoint, ...], target: str) -> int:
    text_values = [series_id.lower()]
    for point in points[-3:]:
        for key in ("series_type", "kind", "name", "metric_name", "maturity"):
            value = point.source_payload.get(key)
            if value is not None:
                text_values.append(str(value).lower())
    text = " ".join(text_values).replace("-", "_")
    if target == "key_rate":
        return 10 if "key_rate" in text or "central_bank_key" in text or "cbr_key" in text else 0
    if target == "ofz_1y":
        return 10 if ("ofz" in text or "yield" in text) and ("1y" in text or "1_y" in text or "1 year" in text) else 0
    if target == "ofz_2y":
        return 10 if ("ofz" in text or "yield" in text) and ("2y" in text or "2_y" in text or "2 year" in text) else 0
    if target == "ofz_10y":
        return 10 if ("ofz" in text or "yield" in text) and ("10y" in text or "10_y" in text or "10 year" in text) else 0
    if target == "currency":
        return 10 if any(token in text for token in ("usdrub", "cnyrub", "currency", "fx", "usd_rub", "rub_fx")) else 0
    if target == "oil":
        return 10 if any(token in text for token in ("oil", "brent", "urals")) else 0
    if target == "market_earnings_yield":
        return 10 if "market_earnings_yield" in text or "earnings_yield" in text else 0
    return 0


def _series_values(points: tuple[RawMacroPoint, ...]) -> tuple[float, ...]:
    return tuple(float(point.value) for point in points if point.value is not None)


def _latest_value(points: tuple[RawMacroPoint, ...]) -> float | None:
    values = _series_values(points)
    if not values:
        return None
    return values[-1]


def _latest_delta(points: tuple[RawMacroPoint, ...]) -> float | None:
    values = _series_values(points)
    if len(values) < 2:
        return None
    return values[-1] - values[-2]


def _series_deltas(points: tuple[RawMacroPoint, ...]) -> tuple[float, ...]:
    values = _series_values(points)
    return tuple(current - previous for previous, current in zip(values, values[1:], strict=False))


def _simple_return_series(values: tuple[float, ...]) -> tuple[float, ...]:
    returns: list[float] = []
    for previous, current in zip(values, values[1:], strict=False):
        if previous > 0 and current > 0:
            returns.append(current / previous - 1.0)
    return tuple(returns)


def _select_index_series(
    values: tuple[RawIndexValue, ...],
    refs: tuple[str, ...],
) -> tuple[RawIndexValue, ...]:
    if not values:
        return ()
    grouped: dict[str, list[RawIndexValue]] = {}
    for value in values:
        grouped.setdefault(value.index_id, []).append(value)
    requested = [_ref_tail(ref) for ref in refs]
    for index_id in requested:
        if index_id in grouped:
            return tuple(sorted(grouped[index_id], key=lambda item: item.value_ts))
    first_key = sorted(grouped)[0]
    return tuple(sorted(grouped[first_key], key=lambda item: item.value_ts))


def _index_price_values(values: tuple[RawIndexValue, ...]) -> tuple[float, ...]:
    return tuple(float(value.value) for value in values if value.value is not None)


def _group_daily_candles(
    candles: tuple[RawCandle, ...],
    instrument_ids: tuple[str, ...],
) -> dict[str, tuple[RawCandle, ...]]:
    requested = set(instrument_ids)
    grouped: dict[str, list[RawCandle]] = {}
    for candle in candles:
        if candle.close_price is None or not candle.close_ts:
            continue
        if requested and candle.instrument_id not in requested:
            continue
        grouped.setdefault(candle.instrument_id, []).append(candle)
    result: dict[str, tuple[RawCandle, ...]] = {}
    for instrument_id, items in grouped.items():
        daily_items = [item for item in items if item.timeframe == "1d"] or items
        result[instrument_id] = tuple(sorted(daily_items, key=lambda item: item.close_ts or ""))
    return result


def _close_values(candles: tuple[RawCandle, ...]) -> tuple[float, ...]:
    return tuple(float(candle.close_price) for candle in candles if candle.close_price is not None)


def _latest_returns_by_instrument(candles_by_instrument: Mapping[str, tuple[RawCandle, ...]]) -> dict[str, float]:
    returns: dict[str, float] = {}
    for instrument_id, candles in candles_by_instrument.items():
        values = _close_values(candles)
        value = compute_return(values, 1)
        if value is not None:
            returns[instrument_id] = value
    return returns


def _breadth_history(candles_by_instrument: Mapping[str, tuple[RawCandle, ...]]) -> tuple[float, ...]:
    returns_by_date: dict[str, list[float]] = {}
    for candles in candles_by_instrument.values():
        points = [(str(candle.close_ts), float(candle.close_price)) for candle in candles if candle.close_ts and candle.close_price]
        for (previous_ts, previous_price), (current_ts, current_price) in zip(points, points[1:], strict=False):
            del previous_ts
            if previous_price > 0 and current_price > 0:
                returns_by_date.setdefault(_date_key(current_ts), []).append(current_price / previous_price - 1.0)
    breadth_values: list[float] = []
    for date_key in sorted(returns_by_date):
        values = returns_by_date[date_key]
        if values:
            breadth_values.append(sum(1 for value in values if value > 0) / len(values))
    return tuple(breadth_values)


def _sector_returns(
    candles_by_instrument: Mapping[str, tuple[RawCandle, ...]],
    sector_mappings: Mapping[str, str],
    window: int,
) -> dict[str, float]:
    by_sector: dict[str, list[float]] = {}
    for instrument_id, candles in candles_by_instrument.items():
        sector = sector_mappings.get(instrument_id)
        if not sector:
            continue
        value = compute_return(_close_values(candles), window)
        if value is not None:
            by_sector.setdefault(sector, []).append(value)
    return {
        sector: sum(values) / len(values)
        for sector, values in by_sector.items()
        if values
    }


def _sector_volatility(
    candles_by_instrument: Mapping[str, tuple[RawCandle, ...]],
    sector_mappings: Mapping[str, str],
    window: int,
) -> dict[str, float]:
    by_sector: dict[str, list[float]] = {}
    for instrument_id, candles in candles_by_instrument.items():
        sector = sector_mappings.get(instrument_id)
        if not sector:
            continue
        returns = compute_log_return_series(_close_values(candles))
        value = compute_realized_volatility(returns, window)
        if value is not None:
            by_sector.setdefault(sector, []).append(value)
    return {
        sector: sum(values) / len(values)
        for sector, values in by_sector.items()
        if values
    }


def _rank_mapping(values: Mapping[str, float]) -> dict[str, float]:
    return {
        key: rank
        for key, value in values.items()
        if (rank := percentile_rank(value, values.values())) is not None
    }


def _market_turnover_percentile(candles_by_instrument: Mapping[str, tuple[RawCandle, ...]]) -> float | None:
    turnover_by_date: dict[str, float] = {}
    for candles in candles_by_instrument.values():
        for candle in candles:
            if candle.close_ts and candle.turnover is not None:
                turnover_by_date[_date_key(candle.close_ts)] = turnover_by_date.get(_date_key(candle.close_ts), 0.0) + float(candle.turnover)
    values = tuple(turnover_by_date[date_key] for date_key in sorted(turnover_by_date))
    if not values:
        return None
    return percentile_rank(values[-1], values[-HISTORY_WINDOW:])


def _event_pressure_score(event: StructuredEvent) -> float | None:
    sentiment = event.sentiment_score
    if sentiment is None:
        return None
    if -1.0 <= sentiment <= 1.0:
        base_pressure = (1.0 - sentiment) / 2.0
    else:
        base_pressure = 1.0 - clip(sentiment, 0.0, 1.0)
    modifiers = [
        value
        for value in (event.materiality_score, event.relevance_score, event.confidence_score)
        if value is not None
    ]
    modifier = sum(modifiers) / len(modifiers) if modifiers else 1.0
    return clip(base_pressure * modifier, 0.0, 1.0)


def _event_sectors(
    event: StructuredEvent,
    sector_by_instrument: Mapping[str, str],
    requested_instruments: set[str],
) -> tuple[str, ...]:
    explicit_sector = event.payload.get("sector") or event.payload.get("sector_name")
    if explicit_sector:
        return (str(explicit_sector),)
    sectors = {
        sector_by_instrument[instrument_id]
        for instrument_id in event.instrument_ids
        if instrument_id in requested_instruments and instrument_id in sector_by_instrument
    }
    return tuple(sorted(sectors))


def _sector_oil_pressure_sign(sector: str) -> float:
    normalized = sector.lower()
    if any(token in normalized for token in ("energy", "oil", "gas", "materials", "metals", "mining")):
        return -1.0
    return 1.0


def _preferred_window(windows: tuple[int, ...], preferred: int) -> int:
    eligible = sorted(window for window in windows if window <= preferred)
    if preferred in windows:
        return preferred
    if eligible:
        return max(eligible)
    return min(windows)


def _available_window(windows: tuple[int, ...], preferred: int, available_count: int) -> int:
    eligible = sorted(window for window in windows if window <= available_count)
    if preferred in eligible:
        return preferred
    if eligible:
        return max(eligible)
    return min(windows)


def _macro_points_stale(points: tuple[RawMacroPoint, ...], job: ModuleJob) -> bool:
    if not points:
        return False
    point_times = tuple(parse_utc_iso(point.point_ts) for point in points if point.point_ts)
    if not point_times:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(point_times)).total_seconds() > MACRO_TTL_SECONDS


def _index_values_stale(values: tuple[RawIndexValue, ...], job: ModuleJob) -> bool:
    if not values:
        return False
    value_times = tuple(parse_utc_iso(value.value_ts) for value in values if value.value_ts)
    if not value_times:
        return True
    max_age_seconds = GLOBAL_TTL_SECONDS if job.contour == "global_contour" else DAILY_TTL_SECONDS
    return (parse_utc_iso(job.time_range.to_ts) - max(value_times)).total_seconds() > max_age_seconds


def _candles_stale(candles: tuple[RawCandle, ...], job: ModuleJob) -> bool:
    if not candles:
        return False
    close_times = tuple(parse_utc_iso(candle.close_ts) for candle in candles if candle.close_ts)
    if not close_times:
        return True
    max_age_seconds = GLOBAL_TTL_SECONDS if job.contour == "global_contour" else DAILY_TTL_SECONDS
    return (parse_utc_iso(job.time_range.to_ts) - max(close_times)).total_seconds() > max_age_seconds


def _date_key(timestamp: str) -> str:
    return parse_utc_iso(timestamp).date().isoformat()


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _jsonable_metrics(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {str(key): _jsonable_metrics(value) for key, value in payload.items()}
    if isinstance(payload, tuple):
        return [_jsonable_metrics(item) for item in payload]
    if isinstance(payload, list):
        return [_jsonable_metrics(item) for item in payload]
    return payload
