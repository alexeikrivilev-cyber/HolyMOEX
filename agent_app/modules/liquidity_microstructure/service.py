from __future__ import annotations

import hashlib
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
    OrderBookSnapshot,
    build_order_book_snapshot,
    classify_aggressive_trade,
    compute_aggressive_ratio,
    compute_amihud_illiquidity,
    compute_bid_ask_spread,
    compute_depth_notional,
    compute_order_book_depth,
    compute_order_book_imbalance,
    compute_order_flow_imbalance,
    compute_quote_velocity,
    compute_spread_widening_flag,
    compute_trade_imbalance,
    estimate_slippage,
    percentile_rank,
    weighted_average,
    zscore,
)
from .repository import (
    AuditRecord,
    ExecutionConstraintHint,
    FeatureRecord,
    InMemoryLiquidityMicrostructureRepository,
    InstrumentProfile,
    LiquidityMicrostructureRepository,
    RawCandle,
    RawOrderBook,
    RawTrade,
)


MODULE_NAME = "Liquidity & Microstructure Module"
CALCULATION_VERSION = "liquidity_microstructure_v1"

VALID_CONTOURS = {"realtime_contour", "intraday_contour"}
VALID_HORIZONS = {"intraday"}
INPUT_FIELDS = {
    "instrument_ids",
    "orderbook_ref",
    "trades_ref",
    "quotes_ref",
    "depth_levels",
    "notional_scenarios",
}
DEPTH_LEVELS_BPS = {"10bps": 10.0, "30bps": 30.0, "50bps": 50.0, "100bps": 100.0}
DOCUMENTED_DEPTH_METRICS = {10.0: "order_book_depth_10bps", 30.0: "order_book_depth_30bps", 50.0: "order_book_depth_50bps"}
DOCUMENTED_NOTIONAL_METRICS = {100000.0: "estimated_slippage_100k", 1000000.0: "estimated_slippage_1m"}
ORDERBOOK_TTL_SECONDS = 180
SLIPPAGE_TTL_SECONDS = 300
DEFAULT_DEPTH_BAND_BPS = 30.0


class LiquidityMicrostructureError(ValueError):
    """Raised when liquidity processing would violate module documentation."""


@dataclass(frozen=True)
class LiquidityMicrostructureInput:
    instrument_ids: tuple[str, ...]
    orderbook_ref: str
    trades_ref: str
    quotes_ref: str
    depth_levels: tuple[str, ...]
    notional_scenarios: tuple[float, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "LiquidityMicrostructureInput":
        input_payload = payload.get("liquidity_input")
        if not isinstance(input_payload, Mapping):
            raise LiquidityMicrostructureError("payload must contain liquidity_input")
        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise LiquidityMicrostructureError(f"liquidity_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise LiquidityMicrostructureError(f"liquidity_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise LiquidityMicrostructureError("liquidity_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise LiquidityMicrostructureError("liquidity_input.instrument_ids must match module_job.instrument_ids")

        depth_levels = tuple(str(item) for item in (input_payload.get("depth_levels") or ()))
        invalid_depth_levels = sorted(set(depth_levels) - set(DEPTH_LEVELS_BPS))
        if invalid_depth_levels:
            raise LiquidityMicrostructureError(f"invalid depth_levels: {invalid_depth_levels}")
        if not depth_levels:
            raise LiquidityMicrostructureError("liquidity_input.depth_levels is required")

        scenarios = tuple(float(item) for item in (input_payload.get("notional_scenarios") or ()))
        invalid_scenarios = sorted(set(scenarios) - set(DOCUMENTED_NOTIONAL_METRICS))
        if invalid_scenarios:
            raise LiquidityMicrostructureError(f"invalid notional_scenarios: {invalid_scenarios}")
        missing_scenarios = sorted(set(DOCUMENTED_NOTIONAL_METRICS) - set(scenarios))
        if missing_scenarios:
            raise LiquidityMicrostructureError(f"notional_scenarios missing documented values: {missing_scenarios}")

        return cls(
            instrument_ids=instrument_ids,
            orderbook_ref=str(input_payload.get("orderbook_ref") or ""),
            trades_ref=str(input_payload.get("trades_ref") or ""),
            quotes_ref=str(input_payload.get("quotes_ref") or ""),
            depth_levels=depth_levels,
            notional_scenarios=scenarios,
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


@dataclass(frozen=True)
class LiquidityMicrostructureExecutionResult:
    module_job_result: ModuleJobResult
    feature_records: tuple[FeatureRecord, ...]
    execution_constraint_hints: tuple[ExecutionConstraintHint, ...]
    feature_record_refs: tuple[str, ...]
    execution_constraint_refs: tuple[str, ...]
    audit_ref: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "feature_records": [record.to_dict() for record in self.feature_records],
            "execution_constraint_hints": [hint.to_dict() for hint in self.execution_constraint_hints],
            "feature_record_refs": list(self.feature_record_refs),
            "execution_constraint_refs": list(self.execution_constraint_refs),
            "audit_ref": self.audit_ref,
        }
        if len(self.execution_constraint_hints) == 1:
            payload["execution_constraint_hint"] = self.execution_constraint_hints[0].to_dict()
        return payload


class LiquidityMicrostructureService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: LiquidityMicrostructureRepository | None = None,
        gateway: Any | None = None,
    ) -> None:
        self.repository = repository or InMemoryLiquidityMicrostructureRepository()
        self.gateway = gateway

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> LiquidityMicrostructureExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> LiquidityMicrostructureExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> LiquidityMicrostructureExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    severity="error",
                    event_type="module_job_missing",
                    message="Liquidity & Microstructure Module requires module_job",
                    object_type="module_job",
                    reason_codes=("module_job_required",),
                    payload={"source_module": self.module_name},
                )
            )
            result = ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                errors=("Liquidity & Microstructure Module requires module_job",),
            )
            return LiquidityMicrostructureExecutionResult(result, (), (), (), (), audit_ref)

        try:
            self.validate_module_job(job)
            liquidity_input = LiquidityMicrostructureInput.from_dict(payload, job)
            profiles = self.repository.list_active_instrument_profiles(job.universe_id, liquidity_input.instrument_ids)
            active_ids = tuple(profile.instrument_id for profile in profiles)
            inactive_ids = tuple(item for item in liquidity_input.instrument_ids if item not in set(active_ids))
            warnings = [f"instrument_not_active:{instrument_id}" for instrument_id in inactive_ids]

            orderbooks = self.repository.list_orderbooks(
                orderbook_ref=liquidity_input.orderbook_ref,
                universe_id=job.universe_id,
                instrument_ids=active_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            trades = self.repository.list_trades(
                trades_ref=liquidity_input.trades_ref,
                universe_id=job.universe_id,
                instrument_ids=active_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )
            candles = self.repository.list_candles(
                universe_id=job.universe_id,
                instrument_ids=active_ids,
                from_ts=job.time_range.from_ts,
                to_ts=job.time_range.to_ts,
            )

            external_requests = self.create_external_requests_for_missing_raw_data(
                liquidity_input=liquidity_input,
                job=job,
                profiles=profiles,
                orderbooks=orderbooks,
                trades=trades,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self.gateway.process(request)
            warnings.extend(f"external_request_created:{request.request_id}" for request in external_requests)
            if external_requests and self.gateway is not None:
                orderbooks = self.repository.list_orderbooks(
                    orderbook_ref=liquidity_input.orderbook_ref,
                    universe_id=job.universe_id,
                    instrument_ids=active_ids,
                    from_ts=job.time_range.from_ts,
                    to_ts=job.time_range.to_ts,
                )
                trades = self.repository.list_trades(
                    trades_ref=liquidity_input.trades_ref,
                    universe_id=job.universe_id,
                    instrument_ids=active_ids,
                    from_ts=job.time_range.from_ts,
                    to_ts=job.time_range.to_ts,
                )
                candles = self.repository.list_candles(
                    universe_id=job.universe_id,
                    instrument_ids=active_ids,
                    from_ts=job.time_range.from_ts,
                    to_ts=job.time_range.to_ts,
                )

            feature_records, hints, compute_warnings = self.compute_outputs(
                liquidity_input=liquidity_input,
                job=job,
                profiles=profiles,
                orderbooks=orderbooks,
                trades=trades,
                candles=candles,
            )
            warnings.extend(compute_warnings)
            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            hint_refs = tuple(self.write_execution_constraint_hint(hint) for hint in hints)
            output_refs = feature_refs + hint_refs

            if not feature_records and not hints:
                warnings.append("raw_liquidity_data_missing")

            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="info" if feature_records and not warnings else "warning",
                    event_type="liquidity_microstructure_written",
                    message="Liquidity and microstructure outputs computed",
                    object_type="feature_record,execution_constraint",
                    object_ref=",".join(output_refs),
                    reason_codes=("feature_records_written", "execution_hints_written") if output_refs else ("no_outputs",),
                    payload={
                        "feature_record_refs": list(feature_refs),
                        "execution_constraint_refs": list(hint_refs),
                        "warnings": warnings,
                        "external_request_ids": [request.request_id for request in external_requests],
                    },
                )
            )
            status = "success" if feature_records and hints and not warnings else "partial_success" if output_refs else "skipped"
            return LiquidityMicrostructureExecutionResult(
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
                execution_constraint_hints=hints,
                feature_record_refs=feature_refs,
                execution_constraint_refs=hint_refs,
                audit_ref=audit_ref,
            )
        except (LiquidityMicrostructureError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise LiquidityMicrostructureError("Liquidity & Microstructure Module requires module_job")
        if job.module_name != self.module_name:
            raise LiquidityMicrostructureError("module_job.module_name must be Liquidity & Microstructure Module")
        if job.contour not in VALID_CONTOURS:
            raise LiquidityMicrostructureError("module_job.contour must be realtime_contour or intraday_contour")
        if not job.universe_id:
            raise LiquidityMicrostructureError("module_job.universe_id is required")
        if not job.instrument_ids:
            raise LiquidityMicrostructureError("module_job.instrument_ids is required")
        if not job.horizons:
            raise LiquidityMicrostructureError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise LiquidityMicrostructureError(f"Liquidity metrics only support intraday horizon: {invalid_horizons}")
        if not job.run_mode:
            raise LiquidityMicrostructureError("module_job.run_mode is required")

    def compute_outputs(
        self,
        *,
        liquidity_input: LiquidityMicrostructureInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...],
        orderbooks: tuple[RawOrderBook, ...],
        trades: tuple[RawTrade, ...],
        candles: tuple[RawCandle, ...],
    ) -> tuple[tuple[FeatureRecord, ...], tuple[ExecutionConstraintHint, ...], tuple[str, ...]]:
        feature_records: list[FeatureRecord] = []
        hints: list[ExecutionConstraintHint] = []
        warnings: list[str] = []
        snapshots_by_instrument = _group_orderbooks(orderbooks)
        trades_by_instrument = _group_trades(trades)
        candles_by_instrument = _group_candles(candles)

        for profile in profiles:
            instrument_orderbooks = snapshots_by_instrument.get(profile.instrument_id, ())
            if not instrument_orderbooks:
                fallback = self.compute_candle_liquidity_proxy(
                    liquidity_input=liquidity_input,
                    job=job,
                    profile=profile,
                    candles=candles_by_instrument.get(profile.instrument_id, ()),
                )
                if fallback is not None:
                    metric_values, constraint_data, metric_warnings = fallback
                    warnings.extend(metric_warnings)
                    for metric_value in metric_values:
                        feature_records.append(
                            self.build_feature_record(
                                profile=profile,
                                metric_value=metric_value,
                                contour=job.contour,
                                timestamp=str(constraint_data["as_of_ts"]),
                                quality_flags=tuple(metric_warnings),
                            )
                        )
                    hints.append(
                        self.build_execution_constraint_hint(
                            profile=profile,
                            as_of_ts=str(constraint_data["as_of_ts"]),
                            spread_bps=constraint_data["spread_bps"],
                            estimated_slippage_bps=constraint_data["estimated_slippage_bps"],
                            max_suggested_order_notional=constraint_data["max_suggested_order_notional"] or 0.0,
                            market_order_allowed=False,
                            reason_codes=tuple(constraint_data["reason_codes"]),
                            payload={
                                "raw_candle_id": constraint_data["raw_candle_id"],
                                "fallback_source": "candle_liquidity_proxy",
                            },
                        )
                    )
                    continue
                hints.append(
                    self.build_execution_constraint_hint(
                        profile=profile,
                        as_of_ts=job.time_range.to_ts,
                        spread_bps=None,
                        estimated_slippage_bps=None,
                        max_suggested_order_notional=0.0,
                        market_order_allowed=False,
                        reason_codes=("missing_orderbook",),
                        payload={"orderbook_ref": liquidity_input.orderbook_ref},
                    )
                )
                warnings.append(f"missing_orderbook:{profile.instrument_id}")
                continue

            latest_raw = instrument_orderbooks[-1]
            latest_snapshot = build_order_book_snapshot(latest_raw.bids, latest_raw.asks)
            if latest_snapshot.best_bid is None or latest_snapshot.best_ask is None:
                hints.append(
                    self.build_execution_constraint_hint(
                        profile=profile,
                        as_of_ts=latest_raw.snapshot_ts,
                        spread_bps=None,
                        estimated_slippage_bps=None,
                        max_suggested_order_notional=0.0,
                        market_order_allowed=False,
                        reason_codes=("empty_orderbook_side",),
                        payload={"raw_orderbook_id": latest_raw.raw_orderbook_id},
                    )
                )
                warnings.append(f"empty_orderbook_side:{profile.instrument_id}")
                continue

            is_stale = _orderbook_stale(latest_raw, job)
            if is_stale:
                hints.append(
                    self.build_execution_constraint_hint(
                        profile=profile,
                        as_of_ts=latest_raw.snapshot_ts,
                        spread_bps=compute_bid_ask_spread(latest_snapshot.best_bid, latest_snapshot.best_ask),
                        estimated_slippage_bps=None,
                        max_suggested_order_notional=0.0,
                        market_order_allowed=False,
                        reason_codes=("stale_orderbook",),
                        payload={"raw_orderbook_id": latest_raw.raw_orderbook_id},
                    )
                )
                warnings.append(f"stale_orderbook:{profile.instrument_id}")
                continue

            previous_snapshot = (
                build_order_book_snapshot(instrument_orderbooks[-2].bids, instrument_orderbooks[-2].asks)
                if len(instrument_orderbooks) >= 2
                else None
            )
            instrument_trades = trades_by_instrument.get(profile.instrument_id, ())
            metric_values, constraint_data, metric_warnings = self.compute_metric_values(
                liquidity_input=liquidity_input,
                job=job,
                profile=profile,
                latest_raw=latest_raw,
                latest_snapshot=latest_snapshot,
                previous_snapshot=previous_snapshot,
                instrument_orderbooks=instrument_orderbooks,
                trades=instrument_trades,
            )
            warnings.extend(metric_warnings)
            for metric_value in metric_values:
                feature_records.append(
                    self.build_feature_record(
                        profile=profile,
                        metric_value=metric_value,
                        contour=job.contour,
                        timestamp=latest_raw.snapshot_ts,
                        quality_flags=tuple(metric_warnings),
                    )
                )
            hints.append(
                self.build_execution_constraint_hint(
                    profile=profile,
                    as_of_ts=latest_raw.snapshot_ts,
                    spread_bps=constraint_data["spread_bps"],
                    estimated_slippage_bps=constraint_data["estimated_slippage_bps"],
                    max_suggested_order_notional=constraint_data["max_suggested_order_notional"] or 0.0,
                    market_order_allowed=bool(constraint_data["market_order_allowed"]),
                    reason_codes=tuple(constraint_data["reason_codes"]),
                    payload={
                        "raw_orderbook_id": latest_raw.raw_orderbook_id,
                        "depth_band_bps": DEFAULT_DEPTH_BAND_BPS,
                    },
                )
            )

        return tuple(feature_records), tuple(hints), tuple(warnings)

    def compute_metric_values(
        self,
        *,
        liquidity_input: LiquidityMicrostructureInput,
        job: ModuleJob,
        profile: InstrumentProfile,
        latest_raw: RawOrderBook,
        latest_snapshot: OrderBookSnapshot,
        previous_snapshot: OrderBookSnapshot | None,
        instrument_orderbooks: tuple[RawOrderBook, ...],
        trades: tuple[RawTrade, ...],
    ) -> tuple[tuple[MetricValue, ...], Mapping[str, Any], tuple[str, ...]]:
        values: list[MetricValue] = []
        warnings: list[str] = []
        source_refs = _context_refs(liquidity_input)
        spread_bps = compute_bid_ask_spread(latest_snapshot.best_bid, latest_snapshot.best_ask)
        self._append_metric(values, "bid_ask_spread_bps", "raw_metric", spread_bps, None, "bps", ORDERBOOK_TTL_SECONDS, source_refs)
        self._append_metric(values, "spread_bps", "raw_metric", spread_bps, None, "bps", ORDERBOOK_TTL_SECONDS, source_refs)

        historical_spreads = tuple(
            spread
            for spread in (
                compute_bid_ask_spread(
                    build_order_book_snapshot(orderbook.bids, orderbook.asks).best_bid,
                    build_order_book_snapshot(orderbook.bids, orderbook.asks).best_ask,
                )
                for orderbook in instrument_orderbooks
            )
            if spread is not None
        )
        spread_percentile = None if spread_bps is None else percentile_rank(spread_bps, historical_spreads)
        self._append_metric(values, "spread_percentile", "derived_metric", spread_percentile, spread_percentile, "rank_0_1", ORDERBOOK_TTL_SECONDS, source_refs)

        depth_by_bps: dict[float, float | None] = {}
        for depth_level in liquidity_input.depth_levels:
            bps_band = DEPTH_LEVELS_BPS[depth_level]
            depth_value = compute_order_book_depth(latest_snapshot, bps_band)
            depth_by_bps[bps_band] = depth_value
            metric_name = DOCUMENTED_DEPTH_METRICS.get(bps_band)
            if metric_name:
                self._append_metric(values, metric_name, "raw_metric", depth_value, None, "shares", ORDERBOOK_TTL_SECONDS, source_refs)

        slippage_by_notional: dict[float, float | None] = {}
        for notional in liquidity_input.notional_scenarios:
            estimate = estimate_slippage(latest_snapshot, notional)
            slippage_by_notional[notional] = estimate.conservative_slippage_bps
            metric_name = DOCUMENTED_NOTIONAL_METRICS[notional]
            payload = {
                "notional_scenario": notional,
                "buy_slippage_bps": estimate.buy_slippage_bps,
                "sell_slippage_bps": estimate.sell_slippage_bps,
                "filled": estimate.filled,
            }
            self._append_metric(
                values,
                metric_name,
                "derived_metric",
                estimate.conservative_slippage_bps,
                None,
                "bps",
                SLIPPAGE_TTL_SECONDS,
                source_refs,
                payload,
            )
        canonical_slippage = slippage_by_notional.get(1000000.0) or slippage_by_notional.get(100000.0)
        self._append_metric(
            values,
            "estimated_slippage_bps",
            "derived_metric",
            canonical_slippage,
            None,
            "bps",
            SLIPPAGE_TTL_SECONDS,
            source_refs,
            {"source_metric": "estimated_slippage_1m_or_100k"},
        )
        self._append_metric(
            values,
            "estimated_order_slippage_bps",
            "derived_metric",
            canonical_slippage,
            None,
            "bps",
            SLIPPAGE_TTL_SECONDS,
            source_refs,
            {"source_metric": "estimated_slippage_1m_or_100k"},
        )

        trade_prices = tuple(trade.price for trade in trades if trade.price is not None and trade.price > 0)
        turnover = sum(_trade_value(trade) for trade in trades)
        amihud = compute_amihud_illiquidity(trade_prices[0] if trade_prices else None, trade_prices[-1] if trade_prices else None, turnover)
        self._append_metric(values, "amihud_illiquidity", "derived_metric", amihud, None, "ratio_per_rub", SLIPPAGE_TTL_SECONDS, source_refs)

        order_book_imbalance = compute_order_book_imbalance(latest_snapshot, DEFAULT_DEPTH_BAND_BPS)
        order_flow_imbalance = compute_order_flow_imbalance(previous_snapshot, latest_snapshot, DEFAULT_DEPTH_BAND_BPS)
        self._append_metric(values, "order_book_imbalance", "derived_metric", order_book_imbalance, order_book_imbalance, "ratio", ORDERBOOK_TTL_SECONDS, source_refs)
        self._append_metric(values, "order_flow_imbalance", "derived_metric", order_flow_imbalance, order_flow_imbalance, "ratio", ORDERBOOK_TTL_SECONDS, source_refs)

        aggressive_buy_volume, aggressive_sell_volume, total_trade_volume = self._aggressive_trade_volumes(trades, latest_snapshot)
        trade_imbalance = compute_trade_imbalance(aggressive_buy_volume, aggressive_sell_volume, total_trade_volume)
        aggressive_buy_ratio = compute_aggressive_ratio(aggressive_buy_volume, total_trade_volume)
        aggressive_sell_ratio = compute_aggressive_ratio(aggressive_sell_volume, total_trade_volume)
        if not trades:
            warnings.append(f"low_trade_coverage:{profile.instrument_id}")
        self._append_metric(values, "trade_imbalance", "derived_metric", trade_imbalance, trade_imbalance, "ratio", SLIPPAGE_TTL_SECONDS, source_refs)
        self._append_metric(values, "aggressive_buy_ratio", "derived_metric", aggressive_buy_ratio, aggressive_buy_ratio, "ratio", SLIPPAGE_TTL_SECONDS, source_refs)
        self._append_metric(values, "aggressive_sell_ratio", "derived_metric", aggressive_sell_ratio, aggressive_sell_ratio, "ratio", SLIPPAGE_TTL_SECONDS, source_refs)

        window_seconds = (parse_utc_iso(job.time_range.to_ts) - parse_utc_iso(job.time_range.from_ts)).total_seconds()
        quote_velocity = compute_quote_velocity(len(instrument_orderbooks), window_seconds)
        self._append_metric(values, "quote_velocity", "raw_metric", quote_velocity, None, "updates_per_second", ORDERBOOK_TTL_SECONDS, source_refs)

        spread_widening_flag = compute_spread_widening_flag(spread_bps, historical_spreads)
        self._append_metric(values, "spread_widening_flag", "derived_metric", spread_widening_flag, spread_widening_flag, "flag", ORDERBOOK_TTL_SECONDS, source_refs)

        depth_history = tuple(
            depth
            for depth in (
                compute_order_book_depth(build_order_book_snapshot(orderbook.bids, orderbook.asks), DEFAULT_DEPTH_BAND_BPS)
                for orderbook in instrument_orderbooks
            )
            if depth is not None
        )
        depth_30bps_z = None
        if depth_by_bps.get(DEFAULT_DEPTH_BAND_BPS) is not None:
            depth_30bps_z = zscore(float(depth_by_bps[DEFAULT_DEPTH_BAND_BPS]), depth_history)

        short_term_pressure_score = self._weighted_composite(
            horizon="intraday",
            component_values={
                "order_book_imbalance": order_book_imbalance,
                "trade_imbalance": trade_imbalance,
                "order_flow_imbalance": order_flow_imbalance,
            },
        )
        if short_term_pressure_score is None and any(
            value is not None for value in (order_book_imbalance, trade_imbalance, order_flow_imbalance)
        ):
            warnings.append(f"active_weights_missing:short_term_pressure_score:{profile.instrument_id}")
        self._append_metric(
            values,
            "short_term_pressure_score",
            "composite_score",
            short_term_pressure_score,
            short_term_pressure_score,
            "score",
            ORDERBOOK_TTL_SECONDS,
            source_refs,
            {"formula": "WAvg([order_book_imbalance, trade_imbalance, order_flow_imbalance], active_weights)"},
        )

        liquidity_risk_score = self._weighted_composite(
            horizon="intraday",
            component_values={
                "spread_percentile": spread_percentile,
                "estimated_slippage_1m": slippage_by_notional.get(1000000.0),
                "amihud_illiquidity": amihud,
                "order_book_depth_30bps_z": None if depth_30bps_z is None else -depth_30bps_z,
            },
        )
        if liquidity_risk_score is None and any(
            value is not None
            for value in (
                spread_percentile,
                slippage_by_notional.get(1000000.0),
                amihud,
                depth_30bps_z,
            )
        ):
            warnings.append(f"active_weights_missing:liquidity_risk_score:{profile.instrument_id}")
        self._append_metric(
            values,
            "liquidity_risk_score",
            "composite_score",
            liquidity_risk_score,
            liquidity_risk_score,
            "score",
            SLIPPAGE_TTL_SECONDS,
            source_refs,
            {"formula": "WAvg([spread_percentile, estimated_slippage_1m, amihud_illiquidity, -order_book_depth_30bps_z], active_weights)"},
        )

        depth_notional = compute_depth_notional(latest_snapshot, DEFAULT_DEPTH_BAND_BPS)
        max_suggested_notional = min(depth_notional) if depth_notional is not None else 0.0
        reason_codes: list[str] = []
        if not profile.tradable:
            reason_codes.append("instrument_not_tradable")
        if spread_bps is None:
            reason_codes.append("invalid_spread")
        if slippage_by_notional.get(100000.0) is None:
            reason_codes.append("insufficient_depth_for_100k")
        if slippage_by_notional.get(1000000.0) is None:
            reason_codes.append("insufficient_depth_for_1m")
        if not reason_codes:
            reason_codes.append("fresh_orderbook")
        else:
            warnings.extend(f"{reason_code}:{profile.instrument_id}" for reason_code in reason_codes)

        constraint_data = {
            "spread_bps": spread_bps,
            "estimated_slippage_bps": slippage_by_notional.get(1000000.0) or slippage_by_notional.get(100000.0),
            "max_suggested_order_notional": max_suggested_notional,
            "market_order_allowed": reason_codes == ["fresh_orderbook"],
            "reason_codes": tuple(reason_codes),
        }
        return tuple(values), constraint_data, tuple(warnings)

    def compute_candle_liquidity_proxy(
        self,
        *,
        liquidity_input: LiquidityMicrostructureInput,
        job: ModuleJob,
        profile: InstrumentProfile,
        candles: tuple[RawCandle, ...],
    ) -> tuple[tuple[MetricValue, ...], Mapping[str, Any], tuple[str, ...]] | None:
        latest = _latest_liquidity_candle(candles)
        if latest is None or latest.close_price is None or latest.close_price <= 0:
            return None

        source_refs = tuple(
            dict.fromkeys(
                (
                    *_context_refs(liquidity_input),
                    f"raw_market.raw_candle:{latest.raw_candle_id}",
                )
            )
        )
        close_price = float(latest.close_price)
        high_price = float(latest.high_price) if latest.high_price is not None else close_price
        low_price = float(latest.low_price) if latest.low_price is not None else close_price
        range_bps = max(0.0, ((high_price - low_price) / close_price) * 10_000.0) if close_price > 0 else 0.0
        turnover = _candle_turnover(latest)
        spread_bps = _clip(max(3.0, range_bps * 0.08), 3.0, 80.0)
        liquidity_penalty_bps = 0.0 if turnover <= 0 else min(60.0, (100_000.0 / max(turnover, 1.0)) * 10.0)
        estimated_slippage_bps = _clip(max(spread_bps, spread_bps * 0.75 + liquidity_penalty_bps), 3.0, 120.0)
        liquidity_risk_score = _clip(estimated_slippage_bps / 80.0, 0.0, 1.0)
        max_suggested_notional = max(0.0, min(100_000.0, turnover * 0.02)) if turnover > 0 else 0.0
        values: list[MetricValue] = []
        for metric_name, value, unit in (
            ("bid_ask_spread_bps", spread_bps, "bps"),
            ("spread_bps", spread_bps, "bps"),
            ("estimated_slippage_100k", estimated_slippage_bps, "bps"),
            ("estimated_slippage_1m", min(120.0, estimated_slippage_bps * 1.5), "bps"),
            ("estimated_slippage_bps", estimated_slippage_bps, "bps"),
            ("estimated_order_slippage_bps", estimated_slippage_bps, "bps"),
            ("liquidity_risk_score", liquidity_risk_score, "score"),
        ):
            self._append_metric(
                values,
                metric_name,
                "proxy_metric" if metric_name != "liquidity_risk_score" else "composite_score",
                value,
                liquidity_risk_score if metric_name == "liquidity_risk_score" else None,
                unit,
                SLIPPAGE_TTL_SECONDS,
                source_refs,
                {
                    "fallback_source": "candle_liquidity_proxy",
                    "raw_candle_id": latest.raw_candle_id,
                    "timeframe": latest.timeframe,
                    "turnover_rub": turnover,
                    "range_bps": range_bps,
                },
            )
        warnings = (
            f"missing_orderbook_using_candle_liquidity_proxy:{profile.instrument_id}",
            f"low_trade_coverage:{profile.instrument_id}",
        )
        constraint_data = {
            "as_of_ts": latest.close_ts,
            "raw_candle_id": latest.raw_candle_id,
            "spread_bps": spread_bps,
            "estimated_slippage_bps": estimated_slippage_bps,
            "max_suggested_order_notional": max_suggested_notional,
            "market_order_allowed": False,
            "reason_codes": ("candle_liquidity_proxy", "missing_orderbook"),
        }
        return tuple(values), constraint_data, warnings

    def build_feature_record(
        self,
        *,
        profile: InstrumentProfile,
        metric_value: MetricValue,
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
            "horizon": "intraday",
            "timestamp": timestamp,
            "calculation_version": CALCULATION_VERSION,
        }
        feature_id = f"feature_{_stable_hash(feature_payload)[:24]}"
        return FeatureRecord(
            feature_id=feature_id,
            instrument_id=profile.instrument_id,
            metric_name=metric_value.metric_name,
            metric_group="liquidity",
            metric_type=metric_value.metric_type,
            raw_value=float(metric_value.raw_value),
            normalized_value=metric_value.normalized_value,
            unit=metric_value.unit,
            horizon="intraday",
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

    def build_execution_constraint_hint(
        self,
        *,
        profile: InstrumentProfile,
        as_of_ts: str,
        spread_bps: float | None,
        estimated_slippage_bps: float | None,
        max_suggested_order_notional: float,
        market_order_allowed: bool,
        reason_codes: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> ExecutionConstraintHint:
        hint_payload = {
            "instrument_id": profile.instrument_id,
            "as_of_ts": as_of_ts,
            "reason_codes": reason_codes,
            "calculation_version": CALCULATION_VERSION,
        }
        constraint_id = f"execution_constraint_{_stable_hash(hint_payload)[:24]}"
        return ExecutionConstraintHint(
            constraint_id=constraint_id,
            instrument_id=profile.instrument_id,
            universe_id=profile.universe_id,
            as_of_ts=as_of_ts,
            spread_bps=spread_bps,
            estimated_slippage_bps=estimated_slippage_bps,
            max_suggested_order_notional=max_suggested_order_notional,
            market_order_allowed=market_order_allowed,
            reason_codes=reason_codes,
            source_module=self.module_name,
            calculation_version=CALCULATION_VERSION,
            payload=payload,
        )

    def create_external_requests_for_missing_raw_data(
        self,
        *,
        liquidity_input: LiquidityMicrostructureInput,
        job: ModuleJob,
        profiles: tuple[InstrumentProfile, ...],
        orderbooks: tuple[RawOrderBook, ...],
        trades: tuple[RawTrade, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if len(profiles) < len(liquidity_input.instrument_ids):
            active_set = {profile.instrument_id for profile in profiles}
            for instrument_id in liquidity_input.instrument_ids:
                if instrument_id not in active_set:
                    requests.append(self._instrument_metadata_request(job, instrument_id))
        latest_by_instrument = {orderbook.instrument_id: orderbook for orderbook in _latest_orderbooks(orderbooks)}
        for profile in profiles:
            latest_orderbook = latest_by_instrument.get(profile.instrument_id)
            if latest_orderbook is None or _orderbook_stale(latest_orderbook, job):
                requests.append(self._instrument_orderbook_request(job, profile))
            instrument_trades = tuple(trade for trade in trades if trade.instrument_id == profile.instrument_id)
            if _env_bool("LIQUIDITY_FETCH_RAW_TRADES", False) and (not instrument_trades or _trades_stale(instrument_trades, job)):
                requests.append(self._instrument_trades_request(job, profile))
        return tuple(requests)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_execution_constraint_hint(self, hint: ExecutionConstraintHint) -> str:
        return self.repository.save_execution_constraint_hint(hint)

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

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
        payload: Mapping[str, Any] | None = None,
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
                payload={"calculation_version": CALCULATION_VERSION, **dict(payload or {})},
            )
        )

    def _aggressive_trade_volumes(
        self,
        trades: tuple[RawTrade, ...],
        latest_snapshot: OrderBookSnapshot,
    ) -> tuple[float, float, float]:
        buy_volume = 0.0
        sell_volume = 0.0
        total_volume = 0.0
        for trade in trades:
            if trade.quantity is None or trade.quantity <= 0:
                continue
            total_volume += float(trade.quantity)
            side = classify_aggressive_trade(trade.price, latest_snapshot.best_bid, latest_snapshot.best_ask, trade.side)
            if side == "buy":
                buy_volume += float(trade.quantity)
            elif side == "sell":
                sell_volume += float(trade.quantity)
        return buy_volume, sell_volume, total_volume

    def _weighted_composite(
        self,
        *,
        horizon: str,
        component_values: Mapping[str, float | None],
    ) -> float | None:
        metric_names = tuple(component_values)
        active_weights = self.repository.list_active_metric_weights(horizon, metric_names)
        if not active_weights:
            return None
        return weighted_average(component_values, active_weights)

    def _instrument_orderbook_request(self, job: ModuleJob, profile: InstrumentProfile) -> ExternalRequest:
        board_id = profile.board_id or "TQBR"
        secid = profile.ticker or _strip_moex_prefix(profile.instrument_id)
        payload = {
            "secid": secid,
            "board_id": board_id,
            "time_range": job.time_range.to_dict(),
        }
        idempotency_key = f"{job.idempotency_key}:orderbook:{profile.instrument_id}:{board_id}"
        return ExternalRequest(
            request_id=f"request_{_stable_hash({'idempotency_key': idempotency_key})[:24]}",
            caller_module=self.module_name,
            provider="moex_iss",
            request_type="orderbook",
            universe_id=job.universe_id,
            instrument_ids=(profile.instrument_id,),
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=30, write_cache=True),
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

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> LiquidityMicrostructureExecutionResult:
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="liquidity_microstructure_failed",
                message="Liquidity and microstructure processing failed",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=("liquidity_microstructure_failed",),
                payload={"error": str(error)},
            )
        )
        return LiquidityMicrostructureExecutionResult(
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
            execution_constraint_hints=(),
            feature_record_refs=(),
            execution_constraint_refs=(),
            audit_ref=audit_ref,
        )


def _context_refs(liquidity_input: LiquidityMicrostructureInput) -> tuple[str, ...]:
    return tuple(
        ref
        for ref in (
            liquidity_input.orderbook_ref,
            liquidity_input.trades_ref,
            liquidity_input.quotes_ref,
        )
        if ref
    )


def _trade_value(trade: RawTrade) -> float:
    if trade.trade_value is not None:
        return float(trade.trade_value)
    if trade.price is None or trade.quantity is None:
        return 0.0
    return float(trade.price) * float(trade.quantity)


def _group_orderbooks(orderbooks: tuple[RawOrderBook, ...]) -> dict[str, tuple[RawOrderBook, ...]]:
    grouped: dict[str, list[RawOrderBook]] = {}
    for orderbook in orderbooks:
        grouped.setdefault(orderbook.instrument_id, []).append(orderbook)
    return {
        instrument_id: tuple(sorted(items, key=lambda item: item.snapshot_ts))
        for instrument_id, items in grouped.items()
    }


def _group_trades(trades: tuple[RawTrade, ...]) -> dict[str, tuple[RawTrade, ...]]:
    grouped: dict[str, list[RawTrade]] = {}
    for trade in trades:
        grouped.setdefault(trade.instrument_id, []).append(trade)
    return {
        instrument_id: tuple(sorted(items, key=lambda item: item.trade_ts))
        for instrument_id, items in grouped.items()
    }


def _group_candles(candles: tuple[RawCandle, ...]) -> dict[str, tuple[RawCandle, ...]]:
    grouped: dict[str, list[RawCandle]] = {}
    for candle in candles:
        grouped.setdefault(candle.instrument_id, []).append(candle)
    return {
        instrument_id: tuple(sorted(items, key=lambda item: (_timeframe_rank(item.timeframe), item.close_ts)))
        for instrument_id, items in grouped.items()
    }


def _latest_liquidity_candle(candles: tuple[RawCandle, ...]) -> RawCandle | None:
    candidates = tuple(candle for candle in candles if candle.close_ts and candle.close_price is not None and candle.close_price > 0)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.close_ts, -_timeframe_rank(item.timeframe)))[-1]


def _timeframe_rank(timeframe: str) -> int:
    return {"1m": 0, "5m": 1, "15m": 2, "1d": 3}.get(str(timeframe or ""), 9)


def _candle_turnover(candle: RawCandle) -> float:
    if candle.turnover is not None and candle.turnover > 0:
        return float(candle.turnover)
    if candle.volume is not None and candle.volume > 0 and candle.close_price is not None and candle.close_price > 0:
        return float(candle.volume) * float(candle.close_price)
    return 0.0


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _latest_orderbooks(orderbooks: tuple[RawOrderBook, ...]) -> tuple[RawOrderBook, ...]:
    return tuple(items[-1] for items in _group_orderbooks(orderbooks).values() if items)


def _orderbook_stale(orderbook: RawOrderBook, job: ModuleJob) -> bool:
    age_seconds = (parse_utc_iso(job.time_range.to_ts) - parse_utc_iso(orderbook.snapshot_ts)).total_seconds()
    return age_seconds > ORDERBOOK_TTL_SECONDS


def _trades_stale(trades: tuple[RawTrade, ...], job: ModuleJob) -> bool:
    trade_times = tuple(parse_utc_iso(trade.trade_ts) for trade in trades if trade.trade_ts)
    if not trade_times:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - max(trade_times)).total_seconds() > SLIPPAGE_TTL_SECONDS


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text
