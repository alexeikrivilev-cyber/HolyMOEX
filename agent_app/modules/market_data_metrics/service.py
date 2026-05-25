from __future__ import annotations

import hashlib
import json
import math
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
    compute_distance_to_high,
    compute_distance_to_low,
    compute_excess_return,
    compute_gap_open_pct,
    compute_gap_persistence_score,
    compute_intraday_return,
    compute_log_returns,
    compute_momentum_acceleration,
    compute_returns,
    compute_rolling_momentum,
    compute_trend_slope,
    compute_trend_t_stat,
    compute_vwap_deviation,
    percentile_rank,
    zscore,
)
from .repository import (
    AuditRecord,
    FeatureRecord,
    InMemoryMarketDataMetricsRepository,
    InstrumentProfile,
    MarketDataMetricsRepository,
    RawCandle,
    RawIndexValue,
    RawTrade,
)


MODULE_NAME = "Market Data Metrics Module"
CALCULATION_VERSION = "market_data_metrics_v1"

VALID_CONTOURS = {"realtime_contour", "daily_contour"}
VALID_HORIZONS = {"intraday", "swing", "position"}
VALID_TIMEFRAMES = {"1m", "5m", "15m", "1d"}
INPUT_FIELDS = {
    "instrument_ids",
    "candles_ref",
    "trades_ref",
    "market_index_ref",
    "sector_index_ref",
    "timeframes",
    "horizons",
}
INTRADAY_TIMEFRAME_PRIORITY = ("1m", "5m", "15m")
DAILY_TTL_SECONDS = 86400
INTRADAY_TTL_SECONDS = 300


class MarketDataMetricsError(ValueError):
    """Raised when market data metrics execution would violate module documentation."""


@dataclass(frozen=True)
class MarketDataMetricsInput:
    instrument_ids: tuple[str, ...]
    candles_ref: str
    trades_ref: str
    market_index_ref: str
    sector_index_ref: str
    timeframes: tuple[str, ...]
    horizons: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "MarketDataMetricsInput":
        input_payload = payload.get("market_data_metrics_input")
        if not isinstance(input_payload, Mapping):
            raise MarketDataMetricsError("payload must contain market_data_metrics_input")
        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise MarketDataMetricsError(f"market_data_metrics_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise MarketDataMetricsError(f"market_data_metrics_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise MarketDataMetricsError("market_data_metrics_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise MarketDataMetricsError("market_data_metrics_input.instrument_ids must match module_job.instrument_ids")

        timeframes = tuple(str(item) for item in (input_payload.get("timeframes") or ()))
        invalid_timeframes = sorted(set(timeframes) - VALID_TIMEFRAMES)
        if invalid_timeframes:
            raise MarketDataMetricsError(f"invalid timeframes: {invalid_timeframes}")
        if not timeframes:
            raise MarketDataMetricsError("market_data_metrics_input.timeframes is required")

        horizons = tuple(str(item) for item in (input_payload.get("horizons") or ()))
        invalid_horizons = sorted(set(horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise MarketDataMetricsError(f"invalid horizons: {invalid_horizons}")
        if not horizons:
            raise MarketDataMetricsError("market_data_metrics_input.horizons is required")
        if tuple(job.horizons) and horizons != tuple(job.horizons):
            raise MarketDataMetricsError("market_data_metrics_input.horizons must match module_job.horizons")

        return cls(
            instrument_ids=instrument_ids,
            candles_ref=str(input_payload.get("candles_ref") or ""),
            trades_ref=str(input_payload.get("trades_ref") or ""),
            market_index_ref=str(input_payload.get("market_index_ref") or ""),
            sector_index_ref=str(input_payload.get("sector_index_ref") or ""),
            timeframes=timeframes,
            horizons=horizons,
        )


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_type: str
    raw_value: float
    normalized_value: float | None
    unit: str
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketDataMetricsExecutionResult:
    module_job_result: ModuleJobResult
    feature_records: tuple[FeatureRecord, ...]
    feature_record_refs: tuple[str, ...]
    audit_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "feature_records": [record.to_dict() for record in self.feature_records],
            "feature_record_refs": list(self.feature_record_refs),
            "audit_ref": self.audit_ref,
        }


class MarketDataMetricsService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: MarketDataMetricsRepository | None = None,
        gateway: Any | None = None,
    ) -> None:
        self.repository = repository or InMemoryMarketDataMetricsRepository()
        self.gateway = gateway

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> MarketDataMetricsExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> MarketDataMetricsExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> MarketDataMetricsExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    severity="error",
                    event_type="module_job_missing",
                    message="Market Data Metrics Module requires module_job",
                    object_type="module_job",
                    reason_codes=("module_job_required",),
                    payload={"source_module": self.module_name},
                )
            )
            raise MarketDataMetricsError("Market Data Metrics Module requires module_job")

        try:
            self.validate_module_job(job)
            metrics_input = MarketDataMetricsInput.from_dict(payload, job)
            profiles = self.repository.list_active_instrument_profiles(job.universe_id, metrics_input.instrument_ids)
            active_ids = tuple(profile.instrument_id for profile in profiles)
            inactive_ids = tuple(item for item in metrics_input.instrument_ids if item not in set(active_ids))
            warnings = [f"instrument_not_active:{instrument_id}" for instrument_id in inactive_ids]

            candles = self.repository.list_candles(
                candles_ref=metrics_input.candles_ref,
                universe_id=job.universe_id,
                instrument_ids=active_ids,
                timeframes=metrics_input.timeframes,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            trades = self.repository.list_trades(
                trades_ref=metrics_input.trades_ref,
                universe_id=job.universe_id,
                instrument_ids=active_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            market_index_values = self.repository.list_index_values(
                metrics_input.market_index_ref,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )
            sector_index_values = self.repository.list_index_values(
                metrics_input.sector_index_ref,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )

            external_requests = self.create_external_requests_for_missing_raw_data(
                metrics_input=metrics_input,
                job=job,
                profiles=profiles,
                candles=candles,
                trades=trades,
                market_index_values=market_index_values,
                sector_index_values=sector_index_values,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self.gateway.process(request)
            warnings.extend(f"external_request_created:{request.request_id}" for request in external_requests)
            if external_requests and self.gateway is not None:
                candles = self.repository.list_candles(
                    candles_ref=metrics_input.candles_ref,
                    universe_id=job.universe_id,
                    instrument_ids=active_ids,
                    timeframes=metrics_input.timeframes,
                    from_ts=job.time_range.from_ts,
                    to_ts=job.time_range.to_ts,
                )
                trades = self.repository.list_trades(
                    trades_ref=metrics_input.trades_ref,
                    universe_id=job.universe_id,
                    instrument_ids=active_ids,
                    from_ts=job.time_range.from_ts,
                    to_ts=job.time_range.to_ts,
                )
                market_index_values = self.repository.list_index_values(
                    metrics_input.market_index_ref,
                    job.time_range.from_ts,
                    job.time_range.to_ts,
                )
                sector_index_values = self.repository.list_index_values(
                    metrics_input.sector_index_ref,
                    job.time_range.from_ts,
                    job.time_range.to_ts,
                )

            if not profiles or not candles:
                return self._empty_result(
                    job=job,
                    started_at=started_at,
                    warnings=tuple(warnings or ("raw_market_data_missing",)),
                    audit_event_type="market_data_metrics_skipped",
                )

            feature_records = self.compute_feature_records(
                metrics_input=metrics_input,
                job=job,
                profiles=profiles,
                candles=candles,
                trades=trades,
                market_index_values=market_index_values,
                sector_index_values=sector_index_values,
            )
            refs = tuple(self.write_feature_record(record) for record in feature_records)
            if not feature_records:
                warnings.append("no_closed_candles_with_sufficient_history")

            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="info" if feature_records and not warnings else "warning",
                    event_type="market_data_metrics_written",
                    message="Market data metrics computed",
                    object_type="feature_record",
                    object_ref=",".join(refs),
                    reason_codes=("feature_records_written",) if feature_records else ("no_feature_records",),
                    payload={
                        "feature_record_refs": list(refs),
                        "warnings": warnings,
                        "external_request_ids": [request.request_id for request in external_requests],
                    },
                )
            )
            status = "success" if feature_records and not warnings else "partial_success" if feature_records else "skipped"
            return MarketDataMetricsExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=refs,
                    warnings=tuple(warnings),
                    errors=(),
                    metrics_written=len(feature_records),
                ),
                feature_records=feature_records,
                feature_record_refs=refs,
                audit_ref=audit_ref,
            )
        except (MarketDataMetricsError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise MarketDataMetricsError("Market Data Metrics Module requires module_job")
        if job.module_name != self.module_name:
            raise MarketDataMetricsError("module_job.module_name must be Market Data Metrics Module")
        if job.contour not in VALID_CONTOURS:
            raise MarketDataMetricsError("module_job.contour must be realtime_contour or daily_contour")
        if not job.universe_id:
            raise MarketDataMetricsError("module_job.universe_id is required")
        if not job.instrument_ids:
            raise MarketDataMetricsError("module_job.instrument_ids is required")
        if not job.horizons:
            raise MarketDataMetricsError("module_job.horizons is required")
        if not job.run_mode:
            raise MarketDataMetricsError("module_job.run_mode is required")

    def compute_feature_records(
        self,
        *,
        metrics_input: MarketDataMetricsInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...],
        candles: tuple[RawCandle, ...],
        trades: tuple[RawTrade, ...],
        market_index_values: tuple[RawIndexValue, ...],
        sector_index_values: tuple[RawIndexValue, ...],
    ) -> tuple[FeatureRecord, ...]:
        records: list[FeatureRecord] = []
        metric_values_by_instrument: dict[str, list[MetricValue]] = {}
        market_excess_by_instrument: dict[str, float] = {}
        sector_excess_by_instrument: dict[str, float] = {}
        profile_by_id = {profile.instrument_id: profile for profile in profiles}
        market_context_missing = not market_index_values
        sector_context_missing = not sector_index_values
        quality_flags = ("low_context_coverage",) if market_context_missing or sector_context_missing else ()

        for profile in profiles:
            daily_candles = _candles_for(candles, profile.instrument_id, "1d")
            intraday_candles = self._preferred_intraday_candles(candles, profile.instrument_id)
            instrument_trades = tuple(trade for trade in trades if trade.instrument_id == profile.instrument_id)
            metric_values = self.compute_metric_values(
                metrics_input=metrics_input,
                profile=profile,
                daily_candles=daily_candles,
                intraday_candles=intraday_candles,
                trades=instrument_trades,
                market_index_values=market_index_values,
                sector_index_values=sector_index_values,
            )
            metric_values_by_instrument[profile.instrument_id] = list(metric_values)
            for metric_value in metric_values:
                if metric_value.metric_name == "excess_return_vs_market":
                    market_excess_by_instrument[profile.instrument_id] = metric_value.raw_value
                elif metric_value.metric_name == "excess_return_vs_sector":
                    sector_excess_by_instrument[profile.instrument_id] = metric_value.raw_value

        for instrument_id, excess_value in market_excess_by_instrument.items():
            rank = percentile_rank(excess_value, market_excess_by_instrument.values())
            if rank is not None:
                metric_values_by_instrument[instrument_id].append(
                    MetricValue(
                        metric_name="relative_strength_rank_market",
                        metric_type="derived_metric",
                        raw_value=rank,
                        normalized_value=rank,
                        unit="rank_0_1",
                        source_refs=_context_refs(metrics_input),
                        payload={"formula": "RankMarket(excess_return_vs_market)"},
                    )
                )

        for instrument_id, excess_value in sector_excess_by_instrument.items():
            sector = profile_by_id[instrument_id].sector or ""
            sector_values = [
                value
                for item_id, value in sector_excess_by_instrument.items()
                if (profile_by_id[item_id].sector or "") == sector
            ]
            rank = percentile_rank(excess_value, sector_values)
            if rank is not None:
                metric_values_by_instrument[instrument_id].append(
                    MetricValue(
                        metric_name="relative_strength_rank_sector",
                        metric_type="derived_metric",
                        raw_value=rank,
                        normalized_value=rank,
                        unit="rank_0_1",
                        source_refs=_context_refs(metrics_input),
                        payload={"formula": "RankSector(excess_return_vs_sector)", "sector": sector},
                    )
                )

        for profile in profiles:
            timestamp = self._instrument_timestamp(candles, profile.instrument_id)
            if timestamp is None:
                continue
            for metric_value in metric_values_by_instrument.get(profile.instrument_id, ()):
                for horizon in metrics_input.horizons:
                    records.append(
                        self.build_feature_record(
                            profile=profile,
                            metric_value=metric_value,
                            horizon=horizon,
                            contour=job.contour,
                            timestamp=timestamp,
                            quality_flags=quality_flags,
                        )
                    )
        return tuple(records)

    def compute_metric_values(
        self,
        *,
        metrics_input: MarketDataMetricsInput,
        profile: InstrumentProfile,
        daily_candles: tuple[RawCandle, ...],
        intraday_candles: tuple[RawCandle, ...],
        trades: tuple[RawTrade, ...],
        market_index_values: tuple[RawIndexValue, ...],
        sector_index_values: tuple[RawIndexValue, ...],
    ) -> tuple[MetricValue, ...]:
        values: list[MetricValue] = []
        source_refs = _context_refs(metrics_input)
        daily_prices = tuple(_required_float(candle.close_price) for candle in daily_candles if candle.close_price)
        daily_highs = tuple(_required_float(candle.high_price) for candle in daily_candles if candle.high_price)
        daily_lows = tuple(_required_float(candle.low_price) for candle in daily_candles if candle.low_price)
        market_values = tuple(item.value for item in market_index_values if item.value is not None)
        sector_values = tuple(item.value for item in sector_index_values if item.value is not None)

        latest_intraday = intraday_candles[-1] if intraday_candles else None
        latest_daily = daily_candles[-1] if daily_candles else None
        latest_price = (
            float(latest_intraday.close_price)
            if latest_intraday is not None and latest_intraday.close_price is not None
            else float(latest_daily.close_price)
            if latest_daily is not None and latest_daily.close_price is not None
            else None
        )
        self._append_metric(
            values,
            "latest_price",
            "raw_metric",
            latest_price,
            "rub",
            source_refs,
        )

        return_1d = compute_returns(daily_prices, 1)
        return_5d = compute_returns(daily_prices, 5)
        return_20d = compute_returns(daily_prices, 20)
        return_60d = compute_returns(daily_prices, 60)
        for metric_name, metric_value in (
            ("return_1d", return_1d),
            ("return_5d", return_5d),
            ("return_20d", return_20d),
            ("return_60d", return_60d),
        ):
            self._append_metric(values, metric_name, "raw_metric", metric_value, "ratio", source_refs)

        intraday_return = None
        if intraday_candles:
            latest = intraday_candles[-1]
            session_open = self._session_open(intraday_candles)
            if latest.close_price is not None and session_open is not None:
                intraday_return = compute_intraday_return(float(latest.close_price), session_open)
        self._append_metric(values, "intraday_return", "raw_metric", intraday_return, "ratio", source_refs)
        self._append_metric(values, "log_return", "raw_metric", compute_log_returns(daily_prices), "log_ratio", source_refs)

        momentum_5d = compute_rolling_momentum(daily_prices, 5)
        momentum_20d = compute_rolling_momentum(daily_prices, 20)
        self._append_metric(values, "momentum_5d", "derived_metric", momentum_5d, "zscore", source_refs, normalized=True)
        self._append_metric(values, "momentum_20d", "derived_metric", momentum_20d, "zscore", source_refs, normalized=True)
        self._append_metric(
            values,
            "momentum_acceleration",
            "derived_metric",
            compute_momentum_acceleration(momentum_5d, momentum_20d),
            "zscore_diff",
            source_refs,
        )

        log_prices = tuple(math.log(price) for price in daily_prices[-60:] if price > 0)
        self._append_metric(values, "trend_slope", "derived_metric", compute_trend_slope(log_prices), "log_price_slope", source_refs)
        self._append_metric(values, "trend_t_stat", "derived_metric", compute_trend_t_stat(log_prices), "t_stat", source_refs)

        if daily_prices:
            current_price = daily_prices[-1]
            self._append_metric(
                values,
                "distance_to_20d_high",
                "raw_metric",
                compute_distance_to_high(current_price, daily_highs, 20),
                "ratio",
                source_refs,
            )
            self._append_metric(
                values,
                "distance_to_60d_high",
                "raw_metric",
                compute_distance_to_high(current_price, daily_highs, 60),
                "ratio",
                source_refs,
            )
            self._append_metric(
                values,
                "distance_to_20d_low",
                "raw_metric",
                compute_distance_to_low(current_price, daily_lows, 20),
                "ratio",
                source_refs,
            )

        if len(daily_candles) >= 2:
            latest = daily_candles[-1]
            previous = daily_candles[-2]
            if latest.open_price is not None and latest.close_price is not None and previous.close_price is not None:
                self._append_metric(
                    values,
                    "gap_open_pct",
                    "raw_metric",
                    compute_gap_open_pct(float(latest.open_price), float(previous.close_price)),
                    "ratio",
                    source_refs,
                )
                self._append_metric(
                    values,
                    "gap_persistence_score",
                    "derived_metric",
                    compute_gap_persistence_score(
                        float(latest.close_price),
                        float(latest.open_price),
                        float(previous.close_price),
                    ),
                    "score",
                    source_refs,
                    normalized=True,
                )

        price_vs_vwap_zscore = self.compute_price_vs_vwap_zscore(daily_candles, intraday_candles, trades)
        self._append_metric(
            values,
            "price_vs_vwap_zscore",
            "derived_metric",
            price_vs_vwap_zscore,
            "zscore",
            source_refs,
            normalized=True,
        )

        market_return = compute_returns(tuple(float(value) for value in market_values), 1)
        sector_return = compute_returns(tuple(float(value) for value in sector_values), 1)
        self._append_metric(
            values,
            "excess_return_vs_market",
            "derived_metric",
            compute_excess_return(return_1d, market_return),
            "ratio",
            source_refs,
        )
        self._append_metric(
            values,
            "excess_return_vs_sector",
            "derived_metric",
            compute_excess_return(return_1d, sector_return),
            "ratio",
            source_refs,
        )
        return tuple(values)

    def compute_price_vs_vwap_zscore(
        self,
        daily_candles: tuple[RawCandle, ...],
        intraday_candles: tuple[RawCandle, ...],
        trades: tuple[RawTrade, ...],
    ) -> float | None:
        deviations: list[float] = []
        for candle in daily_candles[-20:]:
            if (
                candle.close_price is None
                or candle.turnover is None
                or candle.volume is None
                or candle.volume <= 0
            ):
                continue
            deviation = compute_vwap_deviation(float(candle.close_price), float(candle.turnover) / float(candle.volume))
            if deviation is not None:
                deviations.append(deviation)
        if trades and intraday_candles:
            trade_vwap = _trade_vwap(trades)
            latest_price = intraday_candles[-1].close_price
            if trade_vwap is not None and latest_price is not None:
                deviation = compute_vwap_deviation(float(latest_price), trade_vwap)
                if deviation is not None:
                    deviations.append(deviation)
        if len(deviations) < 2:
            return None
        return zscore(deviations[-1], tuple(deviations))

    def build_feature_record(
        self,
        *,
        profile: InstrumentProfile,
        metric_value: MetricValue,
        horizon: str,
        contour: str,
        timestamp: str,
        quality_flags: tuple[str, ...],
    ) -> FeatureRecord:
        payload = {
            **dict(metric_value.payload),
            "formula_version": CALCULATION_VERSION,
            "selected_universe_filter": "is_active=true",
        }
        feature_payload = {
            "instrument_id": profile.instrument_id,
            "metric_name": metric_value.metric_name,
            "horizon": horizon,
            "timestamp": timestamp,
            "calculation_version": CALCULATION_VERSION,
        }
        feature_id = f"feature_{_stable_hash(feature_payload)[:24]}"
        return FeatureRecord(
            feature_id=feature_id,
            instrument_id=profile.instrument_id,
            metric_name=metric_value.metric_name,
            metric_group="price",
            metric_type=metric_value.metric_type,
            raw_value=float(metric_value.raw_value),
            normalized_value=metric_value.normalized_value,
            unit=metric_value.unit,
            horizon=horizon,
            contour=contour,
            timestamp=timestamp,
            ttl_seconds=INTRADAY_TTL_SECONDS if horizon == "intraday" else DAILY_TTL_SECONDS,
            confidence_score=0.8 if quality_flags else 1.0,
            source_module=self.module_name,
            source_refs=metric_value.source_refs,
            calculation_version=CALCULATION_VERSION,
            quality_flags=quality_flags,
            payload=payload,
        )

    def create_external_requests_for_missing_raw_data(
        self,
        *,
        metrics_input: MarketDataMetricsInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...],
        candles: tuple[RawCandle, ...],
        trades: tuple[RawTrade, ...],
        market_index_values: tuple[RawIndexValue, ...],
        sector_index_values: tuple[RawIndexValue, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if len(profiles) < len(metrics_input.instrument_ids):
            active_set = {profile.instrument_id for profile in profiles}
            for instrument_id in metrics_input.instrument_ids:
                if instrument_id not in active_set:
                    requests.append(self._instrument_metadata_request(job, instrument_id))
        for profile in profiles:
            for timeframe in metrics_input.timeframes:
                instrument_candles = _candles_for(candles, profile.instrument_id, timeframe)
                if not instrument_candles or _candles_stale(instrument_candles, job):
                    requests.append(self._instrument_market_request(job, profile, timeframe))
        if metrics_input.market_index_ref and (not market_index_values or _index_values_stale(market_index_values, job)):
            requests.append(self._index_market_request(job, metrics_input.market_index_ref))
        if metrics_input.sector_index_ref and (not sector_index_values or _index_values_stale(sector_index_values, job)):
            requests.append(self._index_market_request(job, metrics_input.sector_index_ref))
        if (
            _env_bool("MARKET_DATA_FETCH_RAW_TRADES", False)
            and any(timeframe in metrics_input.timeframes for timeframe in INTRADAY_TIMEFRAME_PRIORITY)
            and (not trades or _trades_stale(trades, job))
        ):
            for profile in profiles:
                instrument_trades = tuple(trade for trade in trades if trade.instrument_id == profile.instrument_id)
                if not instrument_trades or _trades_stale(instrument_trades, job):
                    requests.append(self._instrument_trades_request(job, profile))
        return tuple(requests)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

    def _append_metric(
        self,
        values: list[MetricValue],
        metric_name: str,
        metric_type: str,
        raw_value: float | None,
        unit: str,
        source_refs: tuple[str, ...],
        normalized: bool = False,
    ) -> None:
        if raw_value is None:
            return
        values.append(
            MetricValue(
                metric_name=metric_name,
                metric_type=metric_type,
                raw_value=float(raw_value),
                normalized_value=float(raw_value) if normalized else None,
                unit=unit,
                source_refs=source_refs,
                payload={"formula": metric_name, "calculation_version": CALCULATION_VERSION},
            )
        )

    def _preferred_intraday_candles(self, candles: tuple[RawCandle, ...], instrument_id: str) -> tuple[RawCandle, ...]:
        for timeframe in INTRADAY_TIMEFRAME_PRIORITY:
            selected = _candles_for(candles, instrument_id, timeframe)
            if selected:
                return selected
        return ()

    def _instrument_timestamp(self, candles: tuple[RawCandle, ...], instrument_id: str) -> str | None:
        instrument_candles = tuple(candle for candle in candles if candle.instrument_id == instrument_id and candle.close_ts)
        if not instrument_candles:
            return None
        return max(str(candle.close_ts) for candle in instrument_candles)

    def _session_open(self, intraday_candles: tuple[RawCandle, ...]) -> float | None:
        latest_ts = intraday_candles[-1].close_ts or intraday_candles[-1].open_ts
        latest_date = parse_utc_iso(latest_ts).date()
        same_session = [
            candle for candle in intraday_candles if parse_utc_iso(candle.open_ts).date() == latest_date
        ]
        if not same_session or same_session[0].open_price is None:
            return None
        return float(same_session[0].open_price)

    def _instrument_market_request(self, job: ModuleJob, profile: InstrumentProfile, timeframe: str) -> ExternalRequest:
        board_id = profile.board_id or "TQBR"
        secid = profile.ticker or _strip_moex_prefix(profile.instrument_id)
        payload = {
            "secid": secid,
            "board_id": board_id,
            "timeframe": timeframe,
            "timeframes": [timeframe],
            "time_range": job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:market_data:{profile.instrument_id}:{board_id}:{timeframe}"
        return ExternalRequest(
            request_id=f"request_{_stable_hash({'idempotency_key': idempotency_key})[:24]}",
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="market_data",
            universe_id=job.universe_id,
            instrument_ids=(profile.instrument_id,),
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=60, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _instrument_trades_request(self, job: ModuleJob, profile: InstrumentProfile) -> ExternalRequest:
        board_id = profile.board_id or "TQBR"
        secid = profile.ticker or _strip_moex_prefix(profile.instrument_id)
        payload = {
            "secid": secid,
            "board_id": board_id,
            "time_range": job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:trades:{profile.instrument_id}:{board_id}"
        return ExternalRequest(
            request_id=f"request_{_stable_hash({'idempotency_key': idempotency_key})[:24]}",
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="trades",
            universe_id=job.universe_id,
            instrument_ids=(profile.instrument_id,),
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=60, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _index_market_request(self, job: ModuleJob, index_ref: str) -> ExternalRequest:
        index_id = _ref_tail(index_ref).upper()
        payload = {
            "index_id": index_id,
            "secid": index_id,
            "board_id": "SNDX",
            "timeframe": "1d",
            "timeframes": ["1d"],
            "endpoint": "/engines/stock/markets/index/boards/SNDX/securities/{secid}/candles.json",
            "time_range": job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:market_data:{index_id}:SNDX:1d"
        return ExternalRequest(
            request_id=f"request_{_stable_hash({'idempotency_key': idempotency_key})[:24]}",
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="market_data",
            universe_id=job.universe_id,
            instrument_ids=(f"moex:{index_id}",),
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=300, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _instrument_metadata_request(self, job: ModuleJob, instrument_id: str) -> ExternalRequest:
        secid = _strip_moex_prefix(instrument_id)
        payload = {
            "secid": secid,
            "board_id": "TQBR",
        }
        idempotency_key = f"{job.idempotency_key}:instruments:{instrument_id}:TQBR"
        return ExternalRequest(
            request_id=f"request_{_stable_hash({'idempotency_key': idempotency_key})[:24]}",
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="instruments",
            universe_id=job.universe_id,
            instrument_ids=(instrument_id,),
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=86400, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

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

    def _empty_result(
        self,
        *,
        job: ModuleJob,
        started_at: str,
        warnings: tuple[str, ...],
        audit_event_type: str,
    ) -> MarketDataMetricsExecutionResult:
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="warning",
                event_type=audit_event_type,
                message="Market data metrics were not written",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=warnings,
                payload={"warnings": list(warnings)},
            )
        )
        return MarketDataMetricsExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="skipped",
                output_refs=(),
                warnings=warnings,
                errors=(),
                metrics_written=0,
            ),
            feature_records=(),
            feature_record_refs=(),
            audit_ref=audit_ref,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> MarketDataMetricsExecutionResult:
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="market_data_metrics_failed",
                message="Market data metrics failed",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=("market_data_metrics_failed",),
                payload={"error": str(error)},
            )
        )
        return MarketDataMetricsExecutionResult(
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
            feature_record_refs=(),
            audit_ref=audit_ref,
        )


def _candles_for(candles: tuple[RawCandle, ...], instrument_id: str, timeframe: str) -> tuple[RawCandle, ...]:
    selected = [
        candle
        for candle in candles
        if candle.instrument_id == instrument_id
        and candle.timeframe == timeframe
        and candle.close_ts
        and candle.close_price is not None
    ]
    return tuple(sorted(selected, key=lambda candle: str(candle.close_ts)))


def _context_refs(metrics_input: MarketDataMetricsInput) -> tuple[str, ...]:
    return tuple(
        ref
        for ref in (
            metrics_input.candles_ref,
            metrics_input.trades_ref,
            metrics_input.market_index_ref,
            metrics_input.sector_index_ref,
        )
        if ref
    )


def _trade_vwap(trades: tuple[RawTrade, ...]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for trade in trades:
        if trade.price is None or trade.quantity is None or trade.quantity <= 0:
            continue
        numerator += float(trade.trade_value) if trade.trade_value is not None else float(trade.price) * float(trade.quantity)
        denominator += float(trade.quantity)
    if denominator <= 0:
        return None
    return numerator / denominator


def _candles_stale(candles: tuple[RawCandle, ...], job: ModuleJob) -> bool:
    closed_at = tuple(parse_utc_iso(candle.close_ts) for candle in candles if candle.close_ts)
    if not closed_at:
        return True
    max_age_seconds = INTRADAY_TTL_SECONDS if job.contour == "realtime_contour" else DAILY_TTL_SECONDS
    return (parse_utc_iso(job.time_range.to_ts) - max(closed_at)).total_seconds() > max_age_seconds


def _trades_stale(trades: tuple[RawTrade, ...], job: ModuleJob) -> bool:
    trade_times = tuple(parse_utc_iso(trade.trade_ts) for trade in trades if trade.trade_ts)
    if not trade_times:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(trade_times)).total_seconds() > INTRADAY_TTL_SECONDS


def _index_values_stale(index_values: tuple[RawIndexValue, ...], job: ModuleJob) -> bool:
    value_times = tuple(parse_utc_iso(item.value_ts) for item in index_values if item.value_ts)
    if not value_times:
        return True
    max_age_seconds = INTRADAY_TTL_SECONDS if job.contour == "realtime_contour" else DAILY_TTL_SECONDS
    return (parse_utc_iso(job.time_range.to_ts) - max(value_times)).total_seconds() > max_age_seconds


def _required_float(value: float | None) -> float:
    if value is None:
        raise MarketDataMetricsError("required market data value is missing")
    return float(value)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text
