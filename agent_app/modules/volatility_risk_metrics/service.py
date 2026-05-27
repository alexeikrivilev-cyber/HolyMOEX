from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from statistics import pstdev
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
    compute_atr,
    compute_beta_to_market,
    compute_correlation,
    compute_downside_volatility,
    compute_gap_open_pct,
    compute_gap_risk,
    compute_intraday_range,
    compute_jump_risk,
    compute_log_return_series,
    compute_max_drawdown,
    compute_realized_volatility,
    compute_recovery_ratio,
    compute_systematic_risk_share,
    percentile_rank,
    weighted_average,
    zscore,
)
from .repository import (
    FeatureRecord,
    InMemoryVolatilityRiskMetricsRepository,
    RawCandle,
    RawIndexValue,
    RawMacroPoint,
    RiskContextRecord,
    VolatilityRiskMetricsRepository,
    stable_record_id,
)


MODULE_NAME = "Volatility & Risk Metrics Module"
CALCULATION_VERSION = "volatility_risk_metrics_v1"

VALID_CONTOURS = {"realtime_contour", "daily_contour"}
VALID_HORIZONS = {"intraday", "swing", "position"}
DOCUMENTED_WINDOWS = {5, 20, 60, 120}
REALIZED_VOL_WINDOWS = (5, 20, 60)
DEFAULT_RISK_WINDOW = 20
DEFAULT_BETA_WINDOW = 60
ATR_WINDOW = 14
HISTORY_WINDOW = 252
JUMP_SIGMA_MULTIPLIER = 3.0
INTRADAY_TTL_SECONDS = 300
DAILY_TTL_SECONDS = 86400
CORRELATION_TTL_SECONDS = 86400
INPUT_FIELDS = {
    "instrument_ids",
    "candles_ref",
    "market_index_ref",
    "sector_index_ref",
    "macro_refs",
    "windows",
    "horizons",
}
DEFAULT_VOLATILITY_COMPONENT_WEIGHTS = {
    "volatility_percentile": 1.0,
    "gap_risk_score": 1.0,
    "jump_risk_score": 1.0,
    "downside_volatility_z": 1.0,
}


class VolatilityRiskMetricsError(ValueError):
    """Raised when volatility risk processing violates the module contract."""


@dataclass(frozen=True)
class VolatilityRiskMetricsInput:
    instrument_ids: tuple[str, ...]
    candles_ref: str
    market_index_ref: str
    sector_index_ref: str
    macro_refs: tuple[str, ...]
    windows: tuple[int, ...]
    horizons: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "VolatilityRiskMetricsInput":
        input_payload = payload.get("risk_metrics_input")
        if not isinstance(input_payload, Mapping):
            raise VolatilityRiskMetricsError("payload must contain risk_metrics_input")
        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise VolatilityRiskMetricsError(f"risk_metrics_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise VolatilityRiskMetricsError(f"risk_metrics_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise VolatilityRiskMetricsError("risk_metrics_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise VolatilityRiskMetricsError("risk_metrics_input.instrument_ids must match module_job.instrument_ids")

        horizons = tuple(str(item) for item in (input_payload.get("horizons") or ()))
        invalid_horizons = sorted(set(horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise VolatilityRiskMetricsError(f"invalid horizons: {invalid_horizons}")
        if not horizons:
            raise VolatilityRiskMetricsError("risk_metrics_input.horizons is required")
        if tuple(job.horizons) and horizons != tuple(job.horizons):
            raise VolatilityRiskMetricsError("risk_metrics_input.horizons must match module_job.horizons")

        windows = tuple(int(item) for item in (input_payload.get("windows") or ()))
        invalid_windows = sorted(set(windows) - DOCUMENTED_WINDOWS)
        if invalid_windows:
            raise VolatilityRiskMetricsError(f"invalid windows: {invalid_windows}")
        if not windows:
            raise VolatilityRiskMetricsError("risk_metrics_input.windows is required")

        return cls(
            instrument_ids=instrument_ids,
            candles_ref=str(input_payload.get("candles_ref") or ""),
            market_index_ref=str(input_payload.get("market_index_ref") or ""),
            sector_index_ref=str(input_payload.get("sector_index_ref") or ""),
            macro_refs=tuple(str(item) for item in (input_payload.get("macro_refs") or ())),
            windows=windows,
            horizons=horizons,
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
class VolatilityRiskMetricsExecutionResult:
    module_job_result: ModuleJobResult
    feature_records: tuple[FeatureRecord, ...]
    risk_context_records: tuple[RiskContextRecord, ...]
    feature_record_refs: tuple[str, ...]
    risk_context_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "feature_records": [record.to_dict() for record in self.feature_records],
            "risk_context_records": [record.to_dict() for record in self.risk_context_records],
            "feature_record_refs": list(self.feature_record_refs),
            "risk_context_refs": list(self.risk_context_refs),
        }
        if len(self.risk_context_records) == 1:
            payload["risk_context_record"] = self.risk_context_records[0].to_dict()
        return payload


class VolatilityRiskMetricsService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: VolatilityRiskMetricsRepository | None = None,
        gateway: Any | None = None,
        component_weights: Mapping[str, float] | None = None,
    ) -> None:
        self.repository = repository or InMemoryVolatilityRiskMetricsRepository()
        self.gateway = gateway
        self.component_weights = dict(component_weights or DEFAULT_VOLATILITY_COMPONENT_WEIGHTS)

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> VolatilityRiskMetricsExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> VolatilityRiskMetricsExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> VolatilityRiskMetricsExecutionResult:
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
            return VolatilityRiskMetricsExecutionResult(result, (), (), (), ())

        try:
            self.validate_module_job(job)
            metrics_input = VolatilityRiskMetricsInput.from_dict(payload, job)
            history_from_ts = _history_from_ts(job.time_range.to_ts)
            candles = self.repository.list_candles(
                candles_ref=metrics_input.candles_ref,
                universe_id=job.universe_id,
                instrument_ids=metrics_input.instrument_ids,
                from_ts=history_from_ts,
                to_ts=job.time_range.to_ts,
            )
            market_index_values = self.repository.list_index_values(
                metrics_input.market_index_ref,
                history_from_ts,
                job.time_range.to_ts,
            )
            sector_index_values = self.repository.list_index_values(
                metrics_input.sector_index_ref,
                history_from_ts,
                job.time_range.to_ts,
            )
            macro_points = self.repository.list_macro_points(
                metrics_input.macro_refs,
                history_from_ts,
                job.time_range.to_ts,
            )

            warnings: list[str] = []
            external_requests = self.create_external_requests_for_missing_raw_data(
                metrics_input=metrics_input,
                job=job,
                candles=candles,
                market_index_values=market_index_values,
                sector_index_values=sector_index_values,
                macro_points=macro_points,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self.gateway.process(request)
            warnings.extend(f"external_request_created:{request.request_id}" for request in external_requests)
            if external_requests and self.gateway is not None:
                candles = self.repository.list_candles(
                    candles_ref=metrics_input.candles_ref,
                    universe_id=job.universe_id,
                    instrument_ids=metrics_input.instrument_ids,
                    from_ts=history_from_ts,
                    to_ts=job.time_range.to_ts,
                )
                market_index_values = self.repository.list_index_values(
                    metrics_input.market_index_ref,
                    history_from_ts,
                    job.time_range.to_ts,
                )
                sector_index_values = self.repository.list_index_values(
                    metrics_input.sector_index_ref,
                    history_from_ts,
                    job.time_range.to_ts,
                )
                macro_points = self.repository.list_macro_points(
                    metrics_input.macro_refs,
                    history_from_ts,
                    job.time_range.to_ts,
                )

            feature_records, risk_context_records, compute_warnings = self.compute_outputs(
                metrics_input=metrics_input,
                job=job,
                candles=candles,
                market_index_values=market_index_values,
                sector_index_values=sector_index_values,
                macro_points=macro_points,
            )
            warnings.extend(compute_warnings)

            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            context_refs = tuple(self.write_risk_context_record(record) for record in risk_context_records)
            output_refs = feature_refs + context_refs
            if not output_refs:
                warnings.append("raw_risk_data_missing")

            status = "success" if feature_records and risk_context_records and not warnings else "partial_success" if output_refs else "skipped"
            return VolatilityRiskMetricsExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(warnings),
                    errors=(),
                    metrics_written=len(feature_records),
                ),
                feature_records=feature_records,
                risk_context_records=risk_context_records,
                feature_record_refs=feature_refs,
                risk_context_refs=context_refs,
            )
        except (VolatilityRiskMetricsError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise VolatilityRiskMetricsError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise VolatilityRiskMetricsError("module_job.module_name must be Volatility & Risk Metrics Module")
        if job.contour not in VALID_CONTOURS:
            raise VolatilityRiskMetricsError("module_job.contour must be realtime_contour or daily_contour")
        if not job.universe_id:
            raise VolatilityRiskMetricsError("module_job.universe_id is required")
        if not job.instrument_ids:
            raise VolatilityRiskMetricsError("module_job.instrument_ids is required")
        if not job.horizons:
            raise VolatilityRiskMetricsError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise VolatilityRiskMetricsError(f"invalid module_job.horizons: {invalid_horizons}")
        if not job.run_mode:
            raise VolatilityRiskMetricsError("module_job.run_mode is required")

    def compute_outputs(
        self,
        *,
        metrics_input: VolatilityRiskMetricsInput,
        job: ModuleJob,
        candles: tuple[RawCandle, ...],
        market_index_values: tuple[RawIndexValue, ...],
        sector_index_values: tuple[RawIndexValue, ...],
        macro_points: tuple[RawMacroPoint, ...],
    ) -> tuple[tuple[FeatureRecord, ...], tuple[RiskContextRecord, ...], tuple[str, ...]]:
        feature_records: list[FeatureRecord] = []
        risk_context_records: list[RiskContextRecord] = []
        warnings: list[str] = []
        candles_by_instrument = _group_daily_candles(candles)
        market_returns_by_date = _value_log_returns_by_date(
            tuple((item.value_ts, item.value) for item in market_index_values if item.value is not None)
        )
        sector_returns_by_date = _value_log_returns_by_date(
            tuple((item.value_ts, item.value) for item in sector_index_values if item.value is not None)
        )
        macro_factor_returns = _macro_factor_returns(macro_points)
        stale_correlations = (
            _index_values_stale(market_index_values, job)
            or _index_values_stale(sector_index_values, job)
            or _macro_points_stale(macro_points, job)
        )

        for instrument_id in metrics_input.instrument_ids:
            daily_candles = candles_by_instrument.get(instrument_id, ())
            metric_values, context_payload, metric_warnings = self.compute_metric_values(
                metrics_input=metrics_input,
                job=job,
                instrument_id=instrument_id,
                daily_candles=daily_candles,
                market_returns_by_date=market_returns_by_date,
                sector_returns_by_date=sector_returns_by_date,
                macro_factor_returns=macro_factor_returns,
                stale_correlations=stale_correlations,
            )
            warnings.extend(metric_warnings)
            timestamp = daily_candles[-1].close_ts if daily_candles and daily_candles[-1].close_ts else job.time_range.to_ts
            quality_flags = tuple(dict.fromkeys(metric_warnings))
            for metric_value in metric_values:
                for horizon in metrics_input.horizons:
                    feature_records.append(
                        self.build_feature_record(
                            instrument_id=instrument_id,
                            metric_value=metric_value,
                            horizon=horizon,
                            contour=job.contour,
                            timestamp=timestamp,
                            quality_flags=tuple(dict.fromkeys(quality_flags + metric_value.quality_flags)),
                        )
                    )
            risk_context_records.append(
                self.build_risk_context_record(
                    job=job,
                    instrument_id=instrument_id,
                    as_of_ts=timestamp,
                    payload={
                        **context_payload,
                        "feature_metric_names": [metric.metric_name for metric in metric_values],
                        "quality_flags": list(quality_flags),
                    },
                )
            )

        return tuple(feature_records), tuple(risk_context_records), tuple(dict.fromkeys(warnings))

    def compute_metric_values(
        self,
        *,
        metrics_input: VolatilityRiskMetricsInput,
        job: ModuleJob,
        instrument_id: str,
        daily_candles: tuple[RawCandle, ...],
        market_returns_by_date: Mapping[str, float],
        sector_returns_by_date: Mapping[str, float],
        macro_factor_returns: Mapping[str, Mapping[str, float]],
        stale_correlations: bool,
    ) -> tuple[tuple[MetricValue, ...], Mapping[str, Any], tuple[str, ...]]:
        values: list[MetricValue] = []
        warnings: list[str] = []
        source_refs = _context_refs(metrics_input)
        close_points = _adjusted_close_points(daily_candles)
        adjusted_close_points = _adjusted_close_points(daily_candles, require_adjusted=True)
        raw_close_prices = tuple(float(candle.close_price) for candle in daily_candles if candle.close_price is not None)
        high_prices = tuple(float(candle.high_price) for candle in daily_candles if candle.high_price is not None)
        low_prices = tuple(float(candle.low_price) for candle in daily_candles if candle.low_price is not None)
        adjusted_prices = tuple(point[1] for point in close_points)
        log_returns = compute_log_return_series(adjusted_prices)
        returns_by_date = _returns_by_date_from_price_points(close_points)
        adjusted_returns_by_date = _returns_by_date_from_price_points(adjusted_close_points)
        adjustment_sources = tuple(sorted({candle.adjustment_source for candle in daily_candles}))
        adjusted_proxy_used = "close_price_as_adjusted_proxy" in adjustment_sources
        timeframes = tuple(sorted({str(candle.timeframe or "") for candle in daily_candles if candle.timeframe}))

        context_payload: dict[str, Any] = {
            "calculation_version": CALCULATION_VERSION,
            "requested_windows": list(metrics_input.windows),
            "observed_history_count": len(adjusted_prices),
            "observed_return_count": len(log_returns),
            "return_basis": "adjusted_close_price" if adjusted_close_points else "close_price",
            "adjustment_sources": list(adjustment_sources),
            "timeframes": list(timeframes),
            "stale_correlations": stale_correlations,
            "macro_factors_available": sorted(macro_factor_returns),
        }
        if stale_correlations:
            warnings.append(f"stale_correlations:{instrument_id}")
        daily_history_count = sum(1 for candle in daily_candles if candle.timeframe == "1d")
        if daily_candles and daily_history_count < min(REALIZED_VOL_WINDOWS):
            warnings.append(f"intraday_candle_volatility_proxy:{instrument_id}")
            context_payload["volatility_history_proxy"] = "intraday_candles"
        if len(log_returns) < min(metrics_input.windows):
            warnings.append(f"insufficient_history:{instrument_id}")
            context_payload["insufficient_history"] = True

        metric_lookup: dict[str, float] = {}
        for window in REALIZED_VOL_WINDOWS:
            if window not in metrics_input.windows:
                continue
            realized_volatility = compute_realized_volatility(log_returns, window)
            self._append_metric(
                values,
                "realized_vol_{window}d".format(window=window),
                "derived_metric",
                realized_volatility,
                None,
                "annualized_volatility",
                DAILY_TTL_SECONDS,
                source_refs,
                {"formula": f"std(log_return,{window}d) * sqrt(252)", "window_length": window},
            )
            if realized_volatility is not None:
                metric_lookup[f"realized_vol_{window}d"] = realized_volatility

        rolling_vol_20d = _rolling_realized_volatility(log_returns, DEFAULT_RISK_WINDOW)
        volatility_percentile = None
        if rolling_vol_20d:
            volatility_percentile = percentile_rank(rolling_vol_20d[-1], rolling_vol_20d[-HISTORY_WINDOW:])
        self._append_metric(
            values,
            "volatility_percentile",
            "derived_metric",
            volatility_percentile,
            volatility_percentile,
            "rank_0_1",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "PctRank(realized_vol_20d, 252d)", "window_length": DEFAULT_RISK_WINDOW},
        )
        if volatility_percentile is not None:
            metric_lookup["volatility_percentile"] = volatility_percentile

        intraday_ranges = _intraday_range_history(daily_candles)
        intraday_range_percentile = None
        if intraday_ranges:
            intraday_range_percentile = percentile_rank(intraday_ranges[-1], intraday_ranges[-HISTORY_WINDOW:])
        self._append_metric(
            values,
            "intraday_range_percentile",
            "derived_metric",
            intraday_range_percentile,
            intraday_range_percentile,
            "rank_0_1",
            INTRADAY_TTL_SECONDS,
            source_refs,
            {"formula": "PctRank((H_t-L_t)/P_{t-1}, 252d)", "window_length": min(len(intraday_ranges), HISTORY_WINDOW)},
        )

        atr_14 = compute_atr(high_prices, low_prices, raw_close_prices, ATR_WINDOW)
        self._append_metric(
            values,
            "atr_14",
            "derived_metric",
            atr_14,
            None,
            "price",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "mean(TrueRange,14)", "window_length": ATR_WINDOW},
        )

        risk_window = _select_window(metrics_input.windows, DEFAULT_RISK_WINDOW, len(log_returns))
        downside_volatility = compute_downside_volatility(log_returns, risk_window) if risk_window else None
        self._append_metric(
            values,
            "downside_volatility",
            "derived_metric",
            downside_volatility,
            None,
            "annualized_volatility",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "std(min(log_return,0), window) * sqrt(252)", "window_length": risk_window},
        )
        if downside_volatility is not None:
            metric_lookup["downside_volatility"] = downside_volatility

        latest_log_return = log_returns[-1] if log_returns else None
        jump_history = log_returns[:-1] if len(log_returns) > 1 else ()
        jump_risk_score = compute_jump_risk(latest_log_return, jump_history, risk_window or DEFAULT_RISK_WINDOW, JUMP_SIGMA_MULTIPLIER)
        self._append_metric(
            values,
            "jump_risk_score",
            "derived_metric",
            jump_risk_score,
            jump_risk_score,
            "score_0_1",
            INTRADAY_TTL_SECONDS,
            source_refs,
            {
                "formula": "1 if abs(log_return) > k*std(log_return,window), else scaled exceedance",
                "window_length": risk_window,
                "k": JUMP_SIGMA_MULTIPLIER,
            },
        )
        if jump_risk_score is not None:
            metric_lookup["jump_risk_score"] = jump_risk_score

        gaps = _gap_history(daily_candles)
        gap_risk_score = compute_gap_risk(gaps[-1], gaps[-HISTORY_WINDOW:]) if gaps else None
        self._append_metric(
            values,
            "gap_risk_score",
            "derived_metric",
            gap_risk_score,
            gap_risk_score,
            "rank_0_1",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "PctRank(abs(gap_open_pct), 252d)", "window_length": min(len(gaps), HISTORY_WINDOW)},
        )
        if gap_risk_score is not None:
            metric_lookup["gap_risk_score"] = gap_risk_score

        max_drawdown_60d = compute_max_drawdown(adjusted_prices, 60)
        self._append_metric(
            values,
            "max_drawdown_60d",
            "derived_metric",
            max_drawdown_60d,
            None,
            "ratio",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "min(P_t / rolling_max(P,60d) - 1)", "window_length": 60},
        )
        if max_drawdown_60d is not None:
            metric_lookup["max_drawdown_60d"] = max_drawdown_60d

        recovery_ratio = compute_recovery_ratio(adjusted_prices, 60)
        self._append_metric(
            values,
            "recovery_ratio",
            "derived_metric",
            recovery_ratio,
            recovery_ratio,
            "ratio_0_1",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "(P_t - drawdown_low) / (pre_drawdown_high - drawdown_low)", "window_length": 60},
        )

        beta_window, instrument_returns, market_returns = _aligned_return_window(
            adjusted_returns_by_date,
            market_returns_by_date,
            metrics_input.windows,
            DEFAULT_BETA_WINDOW,
        )
        if market_returns_by_date and beta_window is None:
            warnings.append(f"adjusted_returns_missing_for_beta:{instrument_id}")
        beta_to_market = compute_beta_to_market(instrument_returns, market_returns, beta_window) if beta_window else None
        self._append_metric(
            values,
            "beta_to_market",
            "derived_metric",
            beta_to_market,
            None,
            "beta",
            DAILY_TTL_SECONDS,
            source_refs,
            {
                "formula": "cov(ret_instrument, ret_market) / var(ret_market)",
                "window_length": beta_window,
                "return_basis": "adjusted_close_price",
                "synchronization": "date_intersection",
            },
            ("partial_adjusted_history",) if adjusted_proxy_used and adjusted_returns_by_date else (),
        )
        if beta_to_market is not None:
            metric_lookup["beta_to_market"] = beta_to_market

        beta_stability = _compute_beta_stability(adjusted_returns_by_date, market_returns_by_date, beta_window or DEFAULT_RISK_WINDOW)
        self._append_metric(
            values,
            "beta_stability",
            "derived_metric",
            beta_stability,
            beta_stability,
            "score_0_1",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "1 - PctRank(std(rolling_beta, window), history_window)", "window_length": beta_window},
        )

        correlation_window, instrument_sector_returns, sector_returns = _aligned_return_window(
            returns_by_date,
            sector_returns_by_date,
            metrics_input.windows,
            DEFAULT_BETA_WINDOW,
        )
        correlation_to_sector = compute_correlation(instrument_sector_returns, sector_returns, correlation_window) if correlation_window else None
        self._append_metric(
            values,
            "correlation_to_sector",
            "derived_metric",
            correlation_to_sector,
            correlation_to_sector,
            "correlation",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "corr(ret_instrument, ret_sector)", "window_length": correlation_window, "synchronization": "date_intersection"},
            ("stale_correlations",) if stale_correlations else (),
        )
        if correlation_to_sector is not None:
            metric_lookup["correlation_to_sector"] = correlation_to_sector

        factor_series_for_regression: dict[str, Mapping[str, float]] = {}
        if market_returns_by_date:
            factor_series_for_regression["market"] = market_returns_by_date
        if sector_returns_by_date:
            factor_series_for_regression["sector"] = sector_returns_by_date
        for factor_name, factor_returns in macro_factor_returns.items():
            metric_name = {
                "currency": "correlation_to_currency",
                "oil": "correlation_to_oil",
                "rates": "correlation_to_rates",
            }.get(factor_name)
            if metric_name is None:
                continue
            factor_window, instrument_factor_returns, factor_values = _aligned_return_window(
                returns_by_date,
                factor_returns,
                metrics_input.windows,
                DEFAULT_BETA_WINDOW,
            )
            correlation_value = compute_correlation(instrument_factor_returns, factor_values, factor_window) if factor_window else None
            self._append_metric(
                values,
                metric_name,
                "derived_metric",
                correlation_value,
                correlation_value,
                "correlation",
                DAILY_TTL_SECONDS,
                source_refs,
                {
                    "formula": f"corr(ret_instrument, ret_{factor_name})",
                    "window_length": factor_window,
                    "synchronization": "date_intersection",
                },
                ("stale_correlations",) if stale_correlations else (),
            )
            if correlation_value is not None:
                metric_lookup[metric_name] = correlation_value
            factor_series_for_regression[factor_name] = factor_returns

        expected_macro_factors = _expected_macro_factors(metrics_input.macro_refs)
        missing_macro_factors = tuple(factor for factor in expected_macro_factors if factor not in macro_factor_returns)
        if missing_macro_factors:
            warnings.extend(f"missing_macro_series:{factor}:{instrument_id}" for factor in missing_macro_factors)

        regression_window, y_values, factor_columns = _aligned_factor_matrix(
            returns_by_date,
            factor_series_for_regression,
            metrics_input.windows,
            DEFAULT_BETA_WINDOW,
        )
        systematic_risk_share = compute_systematic_risk_share(y_values, factor_columns, regression_window) if regression_window else None
        self._append_metric(
            values,
            "systematic_risk_share",
            "derived_metric",
            systematic_risk_share,
            systematic_risk_share,
            "ratio_0_1",
            DAILY_TTL_SECONDS,
            source_refs,
            {"formula": "R^2 from regression on market/sector/macro factors", "window_length": regression_window},
        )
        if systematic_risk_share is not None:
            metric_lookup["systematic_risk_share"] = systematic_risk_share
            idiosyncratic_risk_share = 1.0 - systematic_risk_share
            self._append_metric(
                values,
                "idiosyncratic_risk_share",
                "derived_metric",
                idiosyncratic_risk_share,
                idiosyncratic_risk_share,
                "ratio_0_1",
                DAILY_TTL_SECONDS,
                source_refs,
                {"formula": "1 - systematic_risk_share", "window_length": regression_window},
            )
            metric_lookup["idiosyncratic_risk_share"] = idiosyncratic_risk_share

        downside_volatility_z = None
        rolling_downside = _rolling_downside_volatility(log_returns, risk_window or DEFAULT_RISK_WINDOW)
        if downside_volatility is not None and rolling_downside:
            downside_z = zscore(downside_volatility, rolling_downside[-HISTORY_WINDOW:])
            if downside_z is not None:
                downside_volatility_z = clip((downside_z + 3.0) / 6.0, 0.0, 1.0)
        component_values = {
            "volatility_percentile": volatility_percentile,
            "gap_risk_score": gap_risk_score,
            "jump_risk_score": jump_risk_score,
            "downside_volatility_z": downside_volatility_z,
        }
        volatility_risk_score = weighted_average(component_values, self.component_weights)
        self._append_metric(
            values,
            "volatility_risk_score",
            "composite_score",
            volatility_risk_score,
            volatility_risk_score,
            "score",
            DAILY_TTL_SECONDS,
            source_refs,
            {
                "formula": "WAvg([volatility_percentile, gap_risk_score, jump_risk_score, downside_volatility_z], active_weights)",
                "component_values": {key: value for key, value in component_values.items() if value is not None},
                "weight_source": "active_weights",
            },
        )
        if volatility_risk_score is not None:
            metric_lookup["volatility_risk_score"] = volatility_risk_score

        context_payload.update(
            {
                "insufficient_history": context_payload.get("insufficient_history", False),
                "metrics": metric_lookup,
                "missing_macro_factors": list(missing_macro_factors),
                "synchronization_policy": "date_intersection",
                "window_lengths": {
                    "risk_window": risk_window,
                    "beta_window": beta_window,
                    "correlation_window": correlation_window,
                    "regression_window": regression_window,
                },
            }
        )
        return tuple(values), context_payload, tuple(dict.fromkeys(warnings))

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
            "calculation_version": CALCULATION_VERSION,
            "formula_version": CALCULATION_VERSION,
        }
        feature_payload = {
            "instrument_id": instrument_id,
            "metric_name": metric_value.metric_name,
            "horizon": horizon,
            "timestamp": timestamp,
            "calculation_version": CALCULATION_VERSION,
        }
        return FeatureRecord(
            feature_id=stable_record_id("feature", feature_payload),
            instrument_id=instrument_id,
            metric_name=metric_value.metric_name,
            metric_group="volatility",
            metric_type=metric_value.metric_type,
            raw_value=float(metric_value.raw_value),
            normalized_value=metric_value.normalized_value,
            unit=metric_value.unit,
            horizon=horizon,
            contour=contour,
            timestamp=timestamp,
            ttl_seconds=metric_value.ttl_seconds,
            confidence_score=0.8 if quality_flags else 1.0,
            source_module=self.module_name,
            source_refs=metric_value.source_refs,
            calculation_version=CALCULATION_VERSION,
            quality_flags=quality_flags,
            payload=payload,
        )

    def build_risk_context_record(
        self,
        *,
        job: ModuleJob,
        instrument_id: str,
        as_of_ts: str,
        payload: Mapping[str, Any],
    ) -> RiskContextRecord:
        context_payload = {
            "job_id": job.job_id,
            "horizons": list(job.horizons),
            **dict(payload),
        }
        record_payload = {
            "universe_id": job.universe_id,
            "instrument_id": instrument_id,
            "as_of_ts": as_of_ts,
            "calculation_version": CALCULATION_VERSION,
        }
        return RiskContextRecord(
            risk_context_record_id=stable_record_id("risk_context", record_payload),
            universe_id=job.universe_id,
            instrument_id=instrument_id,
            as_of_ts=as_of_ts,
            payload=context_payload,
            source_module=self.module_name,
            calculation_version=CALCULATION_VERSION,
        )

    def create_external_requests_for_missing_raw_data(
        self,
        *,
        metrics_input: VolatilityRiskMetricsInput,
        job: ModuleJob,
        candles: tuple[RawCandle, ...],
        market_index_values: tuple[RawIndexValue, ...],
        sector_index_values: tuple[RawIndexValue, ...],
        macro_points: tuple[RawMacroPoint, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if (
            not candles
            or _candles_stale(candles, job)
            or not market_index_values
            or not sector_index_values
            or _index_values_stale(market_index_values, job)
            or _index_values_stale(sector_index_values, job)
        ):
            for instrument_id in metrics_input.instrument_ids:
                instrument_candles = tuple(candle for candle in candles if candle.instrument_id == instrument_id)
                if not instrument_candles or _candles_stale(instrument_candles, job):
                    requests.append(self._moex_market_data_request(job, instrument_id, "1d"))
            if metrics_input.market_index_ref and (not market_index_values or _index_values_stale(market_index_values, job)):
                requests.append(self._moex_index_request(job, metrics_input.market_index_ref))
            if metrics_input.sector_index_ref and (not sector_index_values or _index_values_stale(sector_index_values, job)):
                requests.append(self._moex_index_request(job, metrics_input.sector_index_ref))
        if metrics_input.macro_refs and (not macro_points or _macro_points_stale(macro_points, job)):
            requests.append(self._external_request(job, metrics_input, "macro_api", "macro_series"))
        return tuple(requests)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_risk_context_record(self, record: RiskContextRecord) -> str:
        return self.repository.save_risk_context_record(record)

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
                source_refs=source_refs,
                payload=payload,
                quality_flags=quality_flags,
            )
        )

    def _external_request(
        self,
        job: ModuleJob,
        metrics_input: VolatilityRiskMetricsInput,
        provider: str,
        request_type: str,
    ) -> ExternalRequest:
        payload = {
            "candles_ref": metrics_input.candles_ref,
            "market_index_ref": metrics_input.market_index_ref,
            "sector_index_ref": metrics_input.sector_index_ref,
            "macro_refs": list(metrics_input.macro_refs),
            "windows": list(metrics_input.windows),
            "horizons": list(metrics_input.horizons),
            "time_range": job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:{request_type}"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider=provider,
            request_type=request_type,
            universe_id=job.universe_id,
            instrument_ids=metrics_input.instrument_ids,
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=60, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _moex_market_data_request(self, job: ModuleJob, instrument_id: str, timeframe: str) -> ExternalRequest:
        secid = _strip_moex_prefix(instrument_id)
        payload = {
            "secid": secid,
            "board_id": "TQBR",
            "timeframe": timeframe,
            "timeframes": [timeframe],
            "time_range": _history_time_range(job, days=HISTORY_WINDOW + 10) if timeframe in {"1d", "daily"} else job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:market_data:{instrument_id}:TQBR:{timeframe}"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="market_data",
            universe_id=job.universe_id,
            instrument_ids=(instrument_id,),
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=60, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def _moex_index_request(self, job: ModuleJob, index_ref: str) -> ExternalRequest:
        index_id = _ref_tail(index_ref).upper()
        payload = {
            "index_id": index_id,
            "secid": index_id,
            "board_id": "SNDX",
            "timeframe": "1d",
            "timeframes": ["1d"],
            "endpoint": "/engines/stock/markets/index/boards/SNDX/securities/{secid}/candles.json",
            "time_range": _history_time_range(job, days=HISTORY_WINDOW + 10),
        }
        idempotency_key = f"{job.idempotency_key}:market_data:{index_id}:SNDX:1d"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
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
    ) -> VolatilityRiskMetricsExecutionResult:
        return VolatilityRiskMetricsExecutionResult(
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
            risk_context_records=(),
            feature_record_refs=(),
            risk_context_refs=(),
        )


def _context_refs(metrics_input: VolatilityRiskMetricsInput) -> tuple[str, ...]:
    return tuple(
        ref
        for ref in (
            metrics_input.candles_ref,
            metrics_input.market_index_ref,
            metrics_input.sector_index_ref,
            *metrics_input.macro_refs,
        )
        if ref
    )


def _group_daily_candles(candles: tuple[RawCandle, ...]) -> dict[str, tuple[RawCandle, ...]]:
    grouped: dict[str, list[RawCandle]] = {}
    for candle in candles:
        if not candle.close_ts or candle.close_price is None:
            continue
        grouped.setdefault(candle.instrument_id, []).append(candle)
    result: dict[str, tuple[RawCandle, ...]] = {}
    for instrument_id, items in grouped.items():
        daily_items = [item for item in items if item.timeframe == "1d"]
        selected = daily_items if len(daily_items) >= min(REALIZED_VOL_WINDOWS) else items
        result[instrument_id] = tuple(sorted(selected, key=lambda item: item.close_ts or ""))
    return result


def _adjusted_close_points(candles: tuple[RawCandle, ...], require_adjusted: bool = False) -> tuple[tuple[str, float], ...]:
    points: list[tuple[str, float]] = []
    for candle in candles:
        if not candle.close_ts:
            continue
        if require_adjusted and not candle.has_adjusted_close_price:
            continue
        price = candle.adjusted_close_price
        if price is None or price <= 0:
            continue
        points.append((_date_key(candle.close_ts), float(price)))
    return tuple(points)


def _returns_by_date_from_price_points(points: tuple[tuple[str, float], ...]) -> dict[str, float]:
    returns: dict[str, float] = {}
    for (previous_date, previous_price), (current_date, current_price) in zip(points, points[1:], strict=False):
        del previous_date
        if previous_price > 0 and current_price > 0:
            returns[current_date] = current_price / previous_price - 1.0
    return returns


def _value_log_returns_by_date(points: tuple[tuple[str, float | None], ...]) -> dict[str, float]:
    clean_points = tuple((ts, float(value)) for ts, value in points if value is not None and value > 0)
    returns: dict[str, float] = {}
    for (previous_ts, previous_value), (current_ts, current_value) in zip(clean_points, clean_points[1:], strict=False):
        del previous_ts
        if previous_value > 0 and current_value > 0:
            returns[_date_key(current_ts)] = current_value / previous_value - 1.0
    return returns


def _value_delta_by_date(points: tuple[tuple[str, float | None], ...]) -> dict[str, float]:
    clean_points = tuple((ts, float(value)) for ts, value in points if value is not None)
    deltas: dict[str, float] = {}
    for (previous_ts, previous_value), (current_ts, current_value) in zip(clean_points, clean_points[1:], strict=False):
        del previous_ts
        deltas[_date_key(current_ts)] = current_value - previous_value
    return deltas


def _macro_factor_returns(macro_points: tuple[RawMacroPoint, ...]) -> dict[str, Mapping[str, float]]:
    by_factor: dict[str, dict[str, list[tuple[str, float]]]] = {}
    for point in macro_points:
        if point.value is None:
            continue
        factor = _macro_factor_name(point.series_id)
        if factor is None:
            continue
        by_factor.setdefault(factor, {}).setdefault(point.series_id, []).append((point.point_ts, point.value))
    result: dict[str, Mapping[str, float]] = {}
    for factor, series_map in by_factor.items():
        series_id = sorted(series_map)[0]
        points = tuple(sorted(series_map[series_id], key=lambda item: item[0]))
        if factor == "rates":
            result[factor] = _value_delta_by_date(points)
        else:
            result[factor] = _value_log_returns_by_date(points)
    return result


def _macro_factor_name(series_id: str) -> str | None:
    lowered = series_id.lower()
    if any(token in lowered for token in ("oil", "brent")):
        return "oil"
    if any(token in lowered for token in ("currency", "fx", "usd", "usdrub", "cny", "rub")):
        return "currency"
    if any(token in lowered for token in ("ofz", "yield", "rate", "key_rate")):
        return "rates"
    return None


def _expected_macro_factors(macro_refs: tuple[str, ...]) -> tuple[str, ...]:
    factors: list[str] = []
    for ref in macro_refs:
        factor = _macro_factor_name(ref)
        if factor is not None and factor not in factors:
            factors.append(factor)
    return tuple(factors)


def _intraday_range_history(candles: tuple[RawCandle, ...]) -> tuple[float, ...]:
    ranges: list[float] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        intraday_range = compute_intraday_range(current.high_price, current.low_price, previous.close_price)
        if intraday_range is not None:
            ranges.append(intraday_range)
    return tuple(ranges)


def _gap_history(candles: tuple[RawCandle, ...]) -> tuple[float, ...]:
    gaps: list[float] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        gap = compute_gap_open_pct(current.open_price, previous.close_price)
        if gap is not None:
            gaps.append(abs(gap))
    return tuple(gaps)


def _rolling_realized_volatility(log_returns: tuple[float, ...], window: int) -> tuple[float, ...]:
    values: list[float] = []
    for end_index in range(window, len(log_returns) + 1):
        value = compute_realized_volatility(log_returns[:end_index], window)
        if value is not None:
            values.append(value)
    return tuple(values)


def _rolling_downside_volatility(log_returns: tuple[float, ...], window: int) -> tuple[float, ...]:
    values: list[float] = []
    for end_index in range(window, len(log_returns) + 1):
        value = compute_downside_volatility(log_returns[:end_index], window)
        if value is not None:
            values.append(value)
    return tuple(values)


def _select_window(configured_windows: tuple[int, ...], preferred_window: int, available_count: int) -> int | None:
    eligible = sorted(window for window in configured_windows if window <= available_count)
    if not eligible:
        return None
    if preferred_window in eligible:
        return preferred_window
    return max(eligible)


def _aligned_return_window(
    primary: Mapping[str, float],
    factor: Mapping[str, float],
    configured_windows: tuple[int, ...],
    preferred_window: int,
) -> tuple[int | None, tuple[float, ...], tuple[float, ...]]:
    dates = sorted(set(primary) & set(factor))
    window = _select_window(configured_windows, preferred_window, len(dates))
    if window is None:
        return None, (), ()
    selected_dates = dates[-window:]
    return window, tuple(primary[date] for date in selected_dates), tuple(factor[date] for date in selected_dates)


def _aligned_factor_matrix(
    primary: Mapping[str, float],
    factors: Mapping[str, Mapping[str, float]],
    configured_windows: tuple[int, ...],
    preferred_window: int,
) -> tuple[int | None, tuple[float, ...], tuple[tuple[float, ...], ...]]:
    active_factors = {name: series for name, series in factors.items() if series}
    if not active_factors:
        return None, (), ()
    dates = set(primary)
    for factor_values in active_factors.values():
        dates &= set(factor_values)
    sorted_dates = sorted(dates)
    window = _select_window(configured_windows, preferred_window, len(sorted_dates))
    if window is None:
        return None, (), ()
    selected_dates = sorted_dates[-window:]
    y_values = tuple(primary[date] for date in selected_dates)
    factor_columns = tuple(tuple(series[date] for date in selected_dates) for series in active_factors.values())
    return window, y_values, factor_columns


def _compute_beta_stability(
    instrument_returns_by_date: Mapping[str, float],
    market_returns_by_date: Mapping[str, float],
    window: int,
) -> float | None:
    dates = sorted(set(instrument_returns_by_date) & set(market_returns_by_date))
    if len(dates) < window + 2:
        return None
    rolling_betas: list[float] = []
    for end_index in range(window, len(dates) + 1):
        selected_dates = dates[end_index - window : end_index]
        instrument_returns = tuple(instrument_returns_by_date[date] for date in selected_dates)
        market_returns = tuple(market_returns_by_date[date] for date in selected_dates)
        beta = compute_beta_to_market(instrument_returns, market_returns, window)
        if beta is not None:
            rolling_betas.append(beta)
    if len(rolling_betas) < 2:
        return None
    stability_window = min(DEFAULT_RISK_WINDOW, len(rolling_betas))
    rolling_std: list[float] = []
    for end_index in range(2, len(rolling_betas) + 1):
        selected = rolling_betas[max(0, end_index - stability_window) : end_index]
        if len(selected) >= 2:
            rolling_std.append(pstdev(selected))
    if not rolling_std:
        return None
    rank = percentile_rank(rolling_std[-1], rolling_std[-HISTORY_WINDOW:])
    if rank is None:
        return None
    return clip(1.0 - rank, 0.0, 1.0)


def _candles_stale(candles: tuple[RawCandle, ...], job: ModuleJob) -> bool:
    close_times = tuple(parse_utc_iso(candle.close_ts) for candle in candles if candle.close_ts)
    if not close_times:
        return True
    max_age_seconds = INTRADAY_TTL_SECONDS if job.contour == "realtime_contour" else DAILY_TTL_SECONDS
    return (parse_utc_iso(job.time_range.to_ts) - max(close_times)).total_seconds() > max_age_seconds


def _index_values_stale(values: tuple[RawIndexValue, ...], job: ModuleJob) -> bool:
    value_times = tuple(parse_utc_iso(item.value_ts) for item in values if item.value_ts)
    if not value_times:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(value_times)).total_seconds() > CORRELATION_TTL_SECONDS


def _macro_points_stale(points: tuple[RawMacroPoint, ...], job: ModuleJob) -> bool:
    if not points:
        return False
    point_times = tuple(parse_utc_iso(item.point_ts) for item in points if item.point_ts)
    if not point_times:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(point_times)).total_seconds() > CORRELATION_TTL_SECONDS


def _history_from_ts(to_ts: str, days: int = HISTORY_WINDOW + 10) -> str:
    return _iso_utc(parse_utc_iso(to_ts) - timedelta(days=days))


def _history_time_range(job: ModuleJob, days: int) -> Mapping[str, str]:
    payload = dict(job.time_range.to_dict())
    payload["from_ts"] = _history_from_ts(job.time_range.to_ts, days=days)
    payload["to_ts"] = job.time_range.to_ts
    return payload


def _iso_utc(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _date_key(timestamp: str) -> str:
    return parse_utc_iso(timestamp).date().isoformat()


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text
