from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)

from . import metrics
from .repository import (
    BacktestingPaperTradingRepository,
    CorporateActionRecord,
    FeatureRecord,
    FeatureVector,
    InMemoryBacktestingPaperTradingRepository,
    InstrumentLimit,
    MarketBar,
    MetricWeightRule,
    PaperExecutionResultRecord,
    PortfolioLimit,
    RiskPolicy,
    SimulationReportRecord,
    WeightsProfile,
    stable_record_id,
)


MODULE_NAME = "Backtesting & Paper Trading Module"
CALCULATION_VERSION = "backtesting_paper_trading_v1"
VALID_CONTOURS = {"research_contour", "execution_contour"}
VALID_RUN_MODES = {"backtest", "paper_trading"}
VALID_HORIZONS = {"intraday", "swing", "position"}
SIMULATION_REQUEST_FIELDS = {
    "simulation_id",
    "universe_id",
    "time_range",
    "weights_profile_id",
    "risk_policy_id",
    "execution_model_id",
    "cost_model_id",
    "run_mode",
}
TIME_RANGE_FIELDS = {"from_ts", "to_ts"}


class BacktestingPaperTradingError(ValueError):
    """Raised when module 21 would violate its documented contract."""


@dataclass(frozen=True)
class SimulationRequest:
    simulation_id: str
    universe_id: str
    from_ts: str
    to_ts: str
    weights_profile_id: str
    risk_policy_id: str
    execution_model_id: str
    cost_model_id: str
    run_mode: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "SimulationRequest":
        extra_top_level = sorted(set(payload) - {"simulation_request"})
        if extra_top_level:
            raise BacktestingPaperTradingError(f"payload has undocumented fields: {extra_top_level}")
        request_payload = payload.get("simulation_request")
        if not isinstance(request_payload, Mapping):
            raise BacktestingPaperTradingError("payload must contain simulation_request")
        missing_fields = sorted(SIMULATION_REQUEST_FIELDS - set(request_payload))
        if missing_fields:
            raise BacktestingPaperTradingError(f"simulation_request missing required fields: {missing_fields}")
        extra_fields = sorted(set(request_payload) - SIMULATION_REQUEST_FIELDS)
        if extra_fields:
            raise BacktestingPaperTradingError(f"simulation_request has undocumented fields: {extra_fields}")

        time_range_payload = request_payload.get("time_range")
        if not isinstance(time_range_payload, Mapping):
            raise BacktestingPaperTradingError("simulation_request.time_range must be an object")
        missing_time_fields = sorted(TIME_RANGE_FIELDS - set(time_range_payload))
        if missing_time_fields:
            raise BacktestingPaperTradingError(f"simulation_request.time_range missing fields: {missing_time_fields}")
        extra_time_fields = sorted(set(time_range_payload) - TIME_RANGE_FIELDS)
        if extra_time_fields:
            raise BacktestingPaperTradingError(
                f"simulation_request.time_range has undocumented fields: {extra_time_fields}"
            )

        from_ts = str(time_range_payload.get("from_ts") or "")
        to_ts = str(time_range_payload.get("to_ts") or "")
        if parse_utc_iso(from_ts) > parse_utc_iso(to_ts):
            raise BacktestingPaperTradingError("simulation_request.time_range.from_ts must be <= to_ts")
        if from_ts != job.time_range.from_ts or to_ts != job.time_range.to_ts:
            raise BacktestingPaperTradingError("simulation_request.time_range must match module_job.time_range")

        run_mode = str(request_payload.get("run_mode") or "")
        if run_mode not in VALID_RUN_MODES:
            raise BacktestingPaperTradingError("simulation_request.run_mode must be backtest or paper_trading")
        if run_mode != job.run_mode:
            raise BacktestingPaperTradingError("simulation_request.run_mode must match module_job.run_mode")

        universe_id = str(request_payload.get("universe_id") or "")
        if universe_id != job.universe_id:
            raise BacktestingPaperTradingError("simulation_request.universe_id must match module_job.universe_id")

        text_fields = {
            "simulation_id": request_payload.get("simulation_id"),
            "weights_profile_id": request_payload.get("weights_profile_id"),
            "risk_policy_id": request_payload.get("risk_policy_id"),
            "execution_model_id": request_payload.get("execution_model_id"),
            "cost_model_id": request_payload.get("cost_model_id"),
        }
        missing_text = [name for name, value in text_fields.items() if not str(value or "")]
        if missing_text:
            raise BacktestingPaperTradingError(f"simulation_request missing text fields: {missing_text}")

        return cls(
            simulation_id=str(request_payload.get("simulation_id") or ""),
            universe_id=universe_id,
            from_ts=from_ts,
            to_ts=to_ts,
            weights_profile_id=str(request_payload.get("weights_profile_id") or ""),
            risk_policy_id=str(request_payload.get("risk_policy_id") or ""),
            execution_model_id=str(request_payload.get("execution_model_id") or ""),
            cost_model_id=str(request_payload.get("cost_model_id") or ""),
            run_mode=run_mode,
        )


@dataclass(frozen=True)
class SimulationConfig:
    initial_capital_rub: float = 1_000_000.0
    buy_threshold: float = 0.20
    sell_threshold: float = -0.20
    target_position_pct: float = 0.10
    max_position_pct: float = 0.10
    fee_rate_bps: float = 1.0
    slippage_bps: float = 5.0
    risk_free_rate: float = 0.0
    capacity_max_slippage_bps: float = 50.0
    quantity_step: float = 1.0

    @classmethod
    def from_policy(cls, risk_policy: RiskPolicy | None, base: Mapping[str, Any] | None = None) -> "SimulationConfig":
        merged: dict[str, Any] = {}
        if base:
            merged.update(dict(base))
        if risk_policy is not None:
            rules = dict(risk_policy.rules)
            for key in ("simulation", "backtesting", "paper_trading", "execution_model", "cost_model"):
                value = rules.get(key)
                if isinstance(value, Mapping):
                    merged.update(dict(value))
            merged.update({key: value for key, value in rules.items() if key in CONFIG_KEYS})

        return cls(
            initial_capital_rub=_positive_float(
                merged.get("initial_capital_rub"),
                _positive_float(os.getenv("INITIAL_CAPITAL_RUB"), 1_000_000.0),
            ),
            buy_threshold=_float(merged.get("buy_threshold"), 0.20),
            sell_threshold=_float(merged.get("sell_threshold"), -0.20),
            target_position_pct=_clip(_float(merged.get("target_position_pct"), 0.10), 0.0, 1.0),
            max_position_pct=_clip(_float(merged.get("max_position_pct"), 0.10), 0.0, 1.0),
            fee_rate_bps=max(0.0, _float(merged.get("fee_rate_bps"), 1.0)),
            slippage_bps=max(0.0, _float(merged.get("slippage_bps"), 5.0)),
            risk_free_rate=_float(merged.get("risk_free_rate"), 0.0),
            capacity_max_slippage_bps=max(0.0, _float(merged.get("capacity_max_slippage_bps"), 50.0)),
            quantity_step=max(0.000001, _float(merged.get("quantity_step"), 1.0)),
        )


CONFIG_KEYS = {
    "initial_capital_rub",
    "buy_threshold",
    "sell_threshold",
    "target_position_pct",
    "max_position_pct",
    "fee_rate_bps",
    "slippage_bps",
    "risk_free_rate",
    "capacity_max_slippage_bps",
    "quantity_step",
}


@dataclass(frozen=True)
class SimulatedTrade:
    execution_result_id: str
    order_intent_id: str
    instrument_id: str
    side: str
    decision_ts: str
    horizon: str
    quantity: float
    raw_price: float
    fill_price: float
    trade_value: float
    fees: float
    slippage_bps: float
    pnl: float | None
    feature_vector_id: str
    score: float
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class SimulationBuild:
    report: SimulationReportRecord
    execution_results: tuple[PaperExecutionResultRecord, ...]
    warnings: tuple[str, ...]
    status: str
    data_quality_score: float


@dataclass(frozen=True)
class BacktestingPaperTradingRunResult:
    module_job_result: ModuleJobResult
    simulation_report: Mapping[str, Any] | None
    simulation_report_ref: str | None = None
    paper_execution_result_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "simulation_report": dict(self.simulation_report) if self.simulation_report else None,
            "simulation_report_ref": self.simulation_report_ref,
            "paper_execution_result_refs": list(self.paper_execution_result_refs),
        }


class BacktestingPaperTradingService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: BacktestingPaperTradingRepository | None = None,
        config: SimulationConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.repository = repository or InMemoryBacktestingPaperTradingRepository()
        self.base_config = config if isinstance(config, SimulationConfig) else SimulationConfig.from_policy(None, config)

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> BacktestingPaperTradingRunResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> BacktestingPaperTradingRunResult:
        return self.execute(payload, job)

    def execute(self, payload: Mapping[str, Any], job: ModuleJob | None) -> BacktestingPaperTradingRunResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            return self._missing_job_result(started_at)

        try:
            self.validate_module_job(job)
            request = SimulationRequest.from_dict(payload, job)
            build = self.build_simulation(request, job)
            report_ref = self.repository.save_simulation_report(build.report)
            execution_refs = tuple(
                self.repository.save_paper_execution_result(execution_result)
                for execution_result in build.execution_results
            )
            output_refs = (report_ref, *execution_refs)
            result_status = "partial_success" if build.status == "invalid" else "success"
            return BacktestingPaperTradingRunResult(
                module_job_result=ModuleJobResult(
                    job_id=job.job_id,
                    module_name=self.module_name,
                    status=result_status,
                    started_at=started_at,
                    finished_at=to_utc_iso(utc_now()),
                    output_refs=output_refs,
                    warnings=build.warnings,
                    errors=(),
                    metrics_written=1,
                    events_written=len(execution_refs),
                    data_quality_score=build.data_quality_score,
                ),
                simulation_report=build.report.to_contract(),
                simulation_report_ref=report_ref,
                paper_execution_result_refs=execution_refs,
            )
        except (BacktestingPaperTradingError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise BacktestingPaperTradingError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise BacktestingPaperTradingError("module_job.module_name must be Backtesting & Paper Trading Module")
        if job.contour not in VALID_CONTOURS:
            raise BacktestingPaperTradingError("module_job.contour is not valid for Backtesting & Paper Trading Module")
        if not job.universe_id:
            raise BacktestingPaperTradingError("module_job.universe_id is required")
        if not job.horizons:
            raise BacktestingPaperTradingError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise BacktestingPaperTradingError(f"invalid module_job.horizons: {invalid_horizons}")
        if job.run_mode not in VALID_RUN_MODES:
            raise BacktestingPaperTradingError("module_job.run_mode must be backtest or paper_trading")
        if not job.config_ref:
            raise BacktestingPaperTradingError("module_job.config_ref is required")

    def build_simulation(self, request: SimulationRequest, job: ModuleJob) -> SimulationBuild:
        bars = self.repository.list_market_bars(
            universe_id=request.universe_id,
            instrument_ids=tuple(job.instrument_ids),
            from_ts=request.from_ts,
            to_ts=request.to_ts,
        )
        instrument_ids = tuple(job.instrument_ids) or tuple(sorted({bar.instrument_id for bar in bars if bar.instrument_id}))
        feature_vectors = tuple(
            vector
            for horizon in job.horizons
            for vector in self.repository.list_feature_vectors(
                instrument_ids=instrument_ids,
                horizon=horizon,
                from_ts=request.from_ts,
                to_ts=request.to_ts,
            )
        )
        feature_records = tuple(
            record
            for horizon in job.horizons
            for record in self.repository.list_feature_records(
                instrument_ids=instrument_ids,
                horizon=horizon,
                from_ts=request.from_ts,
                to_ts=request.to_ts,
            )
        )
        feature_vectors = self.rebuild_historical_features(
            feature_vectors=feature_vectors,
            feature_records=feature_records,
            config_ref=str(job.config_ref or ""),
        )
        if not instrument_ids:
            instrument_ids = tuple(sorted({vector.instrument_id for vector in feature_vectors if vector.instrument_id}))
        weights_profile = self.repository.get_weights_profile(request.weights_profile_id)
        metric_rules = tuple(
            rule
            for horizon in job.horizons
            for rule in self.repository.list_metric_weight_rules(request.weights_profile_id, horizon)
        )
        risk_policy = self.repository.get_risk_policy(request.risk_policy_id)
        instrument_limits = self.repository.list_instrument_limits(request.risk_policy_id, instrument_ids)
        portfolio_limits = self.repository.list_portfolio_limits(request.risk_policy_id)
        corporate_actions = self.repository.list_corporate_actions(instrument_ids, request.from_ts, request.to_ts)
        config = self._config_from_policy(risk_policy)

        warnings: list[str] = []
        warnings.extend(
            self.validation_warnings(
                request=request,
                job=job,
                bars=bars,
                feature_vectors=feature_vectors,
                weights_profile=weights_profile,
                risk_policy=risk_policy,
                metric_rules=metric_rules,
                corporate_actions=corporate_actions,
            )
        )

        missing_adjustments = tuple(
            action
            for action in corporate_actions
            if action.effective_date
            and parse_utc_iso(f"{action.effective_date}T00:00:00+00:00") <= parse_utc_iso(request.to_ts)
            and (action.adjustment_factor is None or action.adjustment_factor <= 0)
        )
        if missing_adjustments:
            warnings.append("missing_corporate_adjustment")
        calendar_valid = self.calendar_alignment_valid(bars, request)
        if not calendar_valid:
            warnings.append("invalid_calendar_alignment")
        leakage_detected = self.lookahead_bias_detected(feature_vectors)
        if leakage_detected:
            warnings.append("lookahead_bias_detected")

        invalid_reasons = {
            reason
            for reason in warnings
            if reason in {"lookahead_bias_detected", "missing_corporate_adjustment", "invalid_calendar_alignment"}
        }
        if invalid_reasons:
            trades: tuple[SimulatedTrade, ...] = ()
            equity_curve = (config.initial_capital_rub,)
            equity_by_date = self.daily_equity_curve((), ((request.from_ts, config.initial_capital_rub),))
            simulation_state = {
                "decision_trace": [],
                "risk_trace": [],
                "equity_curve": list(equity_by_date),
                "final_positions": {},
                "cash": config.initial_capital_rub,
            }
        else:
            trades, equity_curve, simulation_state = self.simulate(
                request=request,
                job=job,
                bars=bars,
                feature_vectors=feature_vectors,
                weights_profile=weights_profile,
                metric_rules=metric_rules,
                instrument_limits=instrument_limits,
                portfolio_limits=portfolio_limits,
                corporate_actions=corporate_actions,
                config=config,
            )
            if not trades:
                warnings.append("no_simulated_trades")

        trading_days = self.trading_days(bars, request)
        performance = self.performance_metrics(
            equity_curve=equity_curve,
            trades=trades,
            bars=bars,
            trading_days=trading_days,
            risk_free_rate=config.risk_free_rate,
            capacity_max_slippage_bps=config.capacity_max_slippage_bps,
            feature_leakage=leakage_detected,
        )
        created_at = to_utc_iso(utc_now())
        report_status = "invalid" if invalid_reasons else "valid"
        bias_checks = self.bias_checks(
            leakage_detected=leakage_detected,
            missing_adjustments=bool(missing_adjustments),
            calendar_valid=calendar_valid,
        )
        simulation_report_id = stable_record_id(
            "simulation_report",
            {
                "simulation_id": request.simulation_id,
                "time_range": {"from_ts": request.from_ts, "to_ts": request.to_ts},
                "weights_profile_id": request.weights_profile_id,
                "risk_policy_id": request.risk_policy_id,
                "config_ref": job.config_ref,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        report = {
            "simulation_report_id": simulation_report_id,
            "simulation_id": request.simulation_id,
            "time_range": {"from_ts": request.from_ts, "to_ts": request.to_ts},
            "performance": {
                "total_return": performance["total_return"],
                "annualized_return": performance["annualized_return"],
                "max_drawdown": performance["max_drawdown"],
                "sharpe_ratio": performance["sharpe_ratio"],
                "turnover": performance["turnover"],
                "win_rate": performance["win_rate"],
                "avg_slippage_bps": performance["avg_slippage_bps"],
            },
            "bias_checks": bias_checks,
            "created_at": created_at,
            "status": report_status,
            "run_mode": request.run_mode,
            "universe_id": request.universe_id,
            "horizons": list(job.horizons),
            "performance_report_version": CALCULATION_VERSION,
        }
        payload = {
            "simulation_report": report,
            "performance_record": performance,
            "validation_report": {
                "status": report_status,
                "invalid_reasons": sorted(invalid_reasons),
                "warnings": sorted(dict.fromkeys(warnings)),
                "rebuild_historical_features": {
                    "feature_records_read": len(feature_records),
                    "feature_vectors_available": len(feature_vectors),
                    "source": "features.feature_record + features.feature_vector",
                },
                "acceptance_criteria": {
                    "transaction_costs_included": True,
                    "slippage_model_explicit": True,
                    "corporate_actions_applied": not missing_adjustments,
                    "no_future_data_access": not leakage_detected,
                    "performance_report_versioned": True,
                    "paper_mode_uses_same_decision_risk_contracts": True,
                },
                "llm_usage": "none",
            },
            "simulation_state": simulation_state,
            "source_module": self.module_name,
            "calculation_version": CALCULATION_VERSION,
            "timestamp": created_at,
            "confidence_score": self.data_quality_score(report_status, warnings),
            "ttl_seconds": 0,
            "input_versions": {
                "weights_profile_id": request.weights_profile_id,
                "weights_profile_version": weights_profile.version if weights_profile else None,
                "risk_policy_id": request.risk_policy_id,
                "risk_policy_version": risk_policy.version if risk_policy else None,
                "execution_model_id": request.execution_model_id,
                "cost_model_id": request.cost_model_id,
                "config_ref": job.config_ref,
            },
        }
        report_record = SimulationReportRecord(
            simulation_report_id=simulation_report_id,
            simulation_id=request.simulation_id,
            universe_id=request.universe_id,
            horizon=",".join(job.horizons),
            as_of_ts=created_at,
            status=report_status,
            payload=payload,
        )
        execution_results = tuple(
            self.trade_to_execution_result(trade, request, created_at)
            for trade in trades
            if report_status == "valid"
        )
        return SimulationBuild(
            report=report_record,
            execution_results=execution_results,
            warnings=tuple(dict.fromkeys(warnings)),
            status=report_status,
            data_quality_score=self.data_quality_score(report_status, warnings),
        )

    def rebuild_historical_features(
        self,
        *,
        feature_vectors: tuple[FeatureVector, ...],
        feature_records: tuple[FeatureRecord, ...],
        config_ref: str,
    ) -> tuple[FeatureVector, ...]:
        if not feature_records:
            return tuple(sorted(feature_vectors, key=lambda item: (item.as_of_ts, item.instrument_id, item.horizon)))

        grouped: dict[tuple[str, str, str], list[FeatureRecord]] = {}
        for record in feature_records:
            if not record.instrument_id or not record.horizon or not record.timestamp or not record.metric_name:
                continue
            grouped.setdefault((record.instrument_id, record.horizon, record.timestamp), []).append(record)

        rebuilt: dict[tuple[str, str, str], FeatureVector] = {}
        for (instrument_id, horizon, timestamp), records in grouped.items():
            feature_payload: dict[str, Mapping[str, Any]] = {}
            confidences: list[float] = []
            for record in sorted(records, key=lambda item: (item.metric_name, item.feature_id)):
                confidence = record.confidence_score if record.confidence_score is not None else 1.0
                confidences.append(_clip(confidence, 0.0, 1.0))
                payload = dict(record.payload)
                payload.update(
                    {
                        "raw_value": record.raw_value,
                        "normalized_value": record.normalized_value,
                        "metric_group": record.metric_group,
                        "metric_type": record.metric_type,
                        "unit": record.unit,
                        "timestamp": record.timestamp,
                        "ttl_seconds": record.ttl_seconds,
                        "confidence_score": confidence,
                        "source_module": record.source_module,
                        "source_refs": list(record.source_refs),
                        "calculation_version": record.calculation_version,
                        "quality_flags": list(record.quality_flags),
                        "feature_record_id": record.feature_id,
                    }
                )
                feature_payload[record.metric_name] = payload
            if not feature_payload:
                continue
            vector_id = stable_record_id(
                "rebuilt_feature_vector",
                {
                    "instrument_id": instrument_id,
                    "horizon": horizon,
                    "as_of_ts": timestamp,
                    "feature_ids": [record.feature_id for record in records],
                    "config_ref": config_ref,
                    "calculation_version": CALCULATION_VERSION,
                },
            )
            rebuilt[(instrument_id, horizon, timestamp)] = FeatureVector(
                feature_vector_id=vector_id,
                instrument_id=instrument_id,
                horizon=horizon,
                as_of_ts=timestamp,
                features=feature_payload,
                coverage_ratio=1.0,
                data_quality_score=sum(confidences) / len(confidences) if confidences else 0.0,
                build_version=f"rebuilt:{CALCULATION_VERSION}:{config_ref}",
            )

        merged = {
            (vector.instrument_id, vector.horizon, vector.as_of_ts): vector
            for vector in feature_vectors
        }
        merged.update(rebuilt)
        return tuple(sorted(merged.values(), key=lambda item: (item.as_of_ts, item.instrument_id, item.horizon)))

    def validation_warnings(
        self,
        *,
        request: SimulationRequest,
        job: ModuleJob,
        bars: tuple[MarketBar, ...],
        feature_vectors: tuple[FeatureVector, ...],
        weights_profile: WeightsProfile | None,
        risk_policy: RiskPolicy | None,
        metric_rules: tuple[MetricWeightRule, ...],
        corporate_actions: tuple[CorporateActionRecord, ...],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if not bars:
            warnings.append("historical_raw_data_missing")
        if not feature_vectors:
            warnings.append("feature_history_missing")
        if weights_profile is None:
            warnings.append("weights_profile_missing")
        else:
            if weights_profile.status != "active":
                warnings.append("weights_profile_not_active")
            if weights_profile.horizon and weights_profile.horizon not in set(job.horizons):
                warnings.append("weights_profile_horizon_mismatch")
            if weights_profile.run_mode_allowed and request.run_mode not in weights_profile.run_mode_allowed:
                warnings.append("run_mode_not_allowed_by_weights_profile")
        if not metric_rules:
            warnings.append("metric_weight_rules_missing")
        if risk_policy is None:
            warnings.append("risk_policy_missing")
        else:
            if risk_policy.status != "active":
                warnings.append("risk_policy_not_active")
            if risk_policy.run_mode_allowed and request.run_mode not in risk_policy.run_mode_allowed:
                warnings.append("run_mode_not_allowed_by_risk_policy")
        if not corporate_actions:
            warnings.append("corporate_actions_empty")
        return tuple(warnings)

    def calendar_alignment_valid(self, bars: tuple[MarketBar, ...], request: SimulationRequest) -> bool:
        if not bars:
            return False
        start = parse_utc_iso(request.from_ts)
        end = parse_utc_iso(request.to_ts)
        previous_by_instrument: dict[str, Any] = {}
        for bar in bars:
            if not bar.instrument_id or not bar.open_ts:
                return False
            open_dt = parse_utc_iso(bar.open_ts)
            close_dt = parse_utc_iso(bar.close_ts or bar.open_ts)
            if open_dt < start or open_dt > end or close_dt < open_dt:
                return False
            previous = previous_by_instrument.get(bar.instrument_id)
            if previous is not None and open_dt < previous:
                return False
            previous_by_instrument[bar.instrument_id] = open_dt
        return True

    def lookahead_bias_detected(self, feature_vectors: tuple[FeatureVector, ...]) -> bool:
        for vector in feature_vectors:
            decision_dt = parse_utc_iso(vector.as_of_ts)
            for feature_payload in vector.features.values():
                for key in (
                    "timestamp",
                    "as_of_ts",
                    "source_ts",
                    "event_ts",
                    "adjusted_as_of_ts",
                    "adjustment_as_of_ts",
                ):
                    value = feature_payload.get(key) if isinstance(feature_payload, Mapping) else None
                    if not value:
                        continue
                    try:
                        if parse_utc_iso(str(value)) > decision_dt:
                            return True
                    except (ContractValidationError, ValueError):
                        return True
                for key in ("corporate_action_effective_date", "adjustment_effective_date", "effective_date"):
                    value = feature_payload.get(key) if isinstance(feature_payload, Mapping) else None
                    if not value:
                        continue
                    try:
                        if parse_utc_iso(f"{value}T00:00:00+00:00").date() > decision_dt.date():
                            return True
                    except (ContractValidationError, ValueError):
                        return True
        return False

    def simulate(
        self,
        *,
        request: SimulationRequest,
        job: ModuleJob,
        bars: tuple[MarketBar, ...],
        feature_vectors: tuple[FeatureVector, ...],
        weights_profile: WeightsProfile | None,
        metric_rules: tuple[MetricWeightRule, ...],
        instrument_limits: tuple[InstrumentLimit, ...],
        portfolio_limits: tuple[PortfolioLimit, ...],
        corporate_actions: tuple[CorporateActionRecord, ...],
        config: SimulationConfig,
    ) -> tuple[tuple[SimulatedTrade, ...], tuple[float, ...], Mapping[str, Any]]:
        price_index = self.price_index(bars)
        rules_by_key = self.rules_by_key(metric_rules)
        limits_by_instrument = {limit.instrument_id: limit for limit in instrument_limits}
        portfolio_limit_values = self.portfolio_limit_values(portfolio_limits)
        cash = config.initial_capital_rub
        positions: dict[str, dict[str, float]] = {}
        trades: list[SimulatedTrade] = []
        decision_trace: list[dict[str, Any]] = []
        risk_trace: list[dict[str, Any]] = []
        equity_points: list[tuple[str, float]] = [(request.from_ts, config.initial_capital_rub)]
        valid_weights_profile = self.weights_profile_tradeable(weights_profile, request)

        for vector in sorted(feature_vectors, key=lambda item: (item.as_of_ts, item.horizon, item.instrument_id)):
            decision_ts = vector.as_of_ts
            bar = self.latest_bar_at(price_index, vector.instrument_id, decision_ts)
            if bar is None:
                decision_trace.append(
                    {
                        "instrument_id": vector.instrument_id,
                        "decision_ts": decision_ts,
                        "action": "hold",
                        "reason_codes": ["market_price_missing"],
                    }
                )
                equity_points.append((decision_ts, self.current_equity(cash, positions, price_index, corporate_actions, decision_ts)))
                continue

            raw_price = bar.close_price
            price = self.adjusted_price(bar, decision_ts, corporate_actions)
            score, contribution_trace = self.weighted_score(vector, rules_by_key)
            equity_before = self.current_equity(cash, positions, price_index, corporate_actions, decision_ts)
            action = self.decision_action(score, vector.instrument_id, positions, config, valid_weights_profile)
            quantity = self.order_quantity(action, vector.instrument_id, positions, equity_before, price, config, limits_by_instrument)
            risk_flags = self.risk_flags(
                action=action,
                instrument_id=vector.instrument_id,
                quantity=quantity,
                price=price,
                equity=equity_before,
                positions=positions,
                config=config,
                instrument_limit=limits_by_instrument.get(vector.instrument_id),
                portfolio_limits=portfolio_limit_values,
            )
            if risk_flags:
                quantity = 0.0
                action = "hold"
            trade: SimulatedTrade | None = None
            if action in {"buy", "sell"} and quantity > 0:
                trade, cash = self.apply_trade(
                    request=request,
                    job=job,
                    vector=vector,
                    action=action,
                    quantity=quantity,
                    raw_price=raw_price,
                    price=price,
                    cash=cash,
                    positions=positions,
                    config=config,
                    risk_flags=risk_flags,
                    score=score,
                )
                trades.append(trade)
            equity_after = self.current_equity(cash, positions, price_index, corporate_actions, decision_ts)
            equity_points.append((decision_ts, equity_after))
            decision_trace.append(
                {
                    "instrument_id": vector.instrument_id,
                    "feature_vector_id": vector.feature_vector_id,
                    "horizon": vector.horizon,
                    "decision_ts": decision_ts,
                    "score": score,
                    "action": action,
                    "target_quantity": quantity,
                    "price_source_ref": bar.source_ref,
                    "feature_contributions": contribution_trace,
                    "execution_result_id": trade.execution_result_id if trade else None,
                }
            )
            risk_trace.append(
                {
                    "instrument_id": vector.instrument_id,
                    "decision_ts": decision_ts,
                    "risk_flags": list(risk_flags),
                    "simulated_risk_contract": {
                        "run_mode": request.run_mode,
                        "risk_policy_id": request.risk_policy_id,
                        "instrument_limit_applied": vector.instrument_id in limits_by_instrument,
                    },
                }
            )

        final_equity = self.current_equity(cash, positions, price_index, corporate_actions, request.to_ts)
        equity_points.append((request.to_ts, final_equity))
        daily_curve = self.daily_equity_curve((value for _, value in equity_points), equity_points)
        simulation_state = {
            "decision_trace": decision_trace,
            "risk_trace": risk_trace,
            "equity_curve": list(daily_curve),
            "final_positions": {instrument_id: dict(position) for instrument_id, position in sorted(positions.items())},
            "cash": cash,
            "transaction_costs_included": True,
            "slippage_model_explicit": True,
            "paper_mode_uses_same_decision_risk_contracts": True,
        }
        return tuple(trades), tuple(point["equity"] for point in daily_curve), simulation_state

    def weights_profile_tradeable(self, weights_profile: WeightsProfile | None, request: SimulationRequest) -> bool:
        if weights_profile is None or weights_profile.status != "active":
            return False
        return not weights_profile.run_mode_allowed or request.run_mode in weights_profile.run_mode_allowed

    def weighted_score(
        self,
        vector: FeatureVector,
        rules_by_key: Mapping[tuple[str, str], tuple[MetricWeightRule, ...]],
    ) -> tuple[float, Mapping[str, Any]]:
        contributions: dict[str, float] = {}
        total_weight = 0.0
        for metric_name, feature_payload in vector.features.items():
            rules = rules_by_key.get((vector.horizon, metric_name), ())
            for rule in rules:
                if not self.rule_applies_to_instrument(rule, vector.instrument_id):
                    continue
                value = self.feature_numeric_value(feature_payload)
                if value is None:
                    continue
                confidence = self.feature_confidence(feature_payload)
                if rule.min_confidence_score is not None and confidence < rule.min_confidence_score:
                    continue
                transformed = self.transform_value(value, rule.transform)
                if rule.direction == "negative":
                    transformed *= -1.0
                elif rule.direction == "nonlinear":
                    transformed = math.tanh(transformed)
                contribution = transformed * rule.weight * confidence
                contributions[metric_name] = contributions.get(metric_name, 0.0) + contribution
                total_weight += abs(rule.weight)
        if total_weight <= 0:
            return 0.0, contributions
        return sum(contributions.values()) / total_weight, contributions

    def rule_applies_to_instrument(self, rule: MetricWeightRule, instrument_id: str) -> bool:
        if rule.instrument_scope == "instrument":
            return instrument_id in rule.instrument_ids
        return rule.instrument_scope in {"all", "sector"}

    def feature_numeric_value(self, payload: Mapping[str, Any]) -> float | None:
        for key in ("normalized_value", "raw_value", "value", "score"):
            value = _maybe_float(payload.get(key))
            if value is not None:
                return value
        return None

    def feature_confidence(self, payload: Mapping[str, Any]) -> float:
        value = _maybe_float(payload.get("confidence_score"))
        return _clip(value if value is not None else 1.0, 0.0, 1.0)

    def transform_value(self, value: float, transform: str) -> float:
        if transform == "identity":
            return value
        if transform in {"clip", "winsorized"}:
            return _clip(value, -1.0, 1.0)
        if transform in {"log", "log1p"}:
            return math.copysign(math.log1p(abs(value)), value)
        if transform == "tanh":
            return math.tanh(value)
        return value

    def decision_action(
        self,
        score: float,
        instrument_id: str,
        positions: Mapping[str, Mapping[str, float]],
        config: SimulationConfig,
        valid_weights_profile: bool,
    ) -> str:
        if not valid_weights_profile:
            return "hold"
        quantity = positions.get(instrument_id, {}).get("quantity", 0.0)
        if score >= config.buy_threshold:
            return "buy"
        if score <= config.sell_threshold and quantity > 0:
            return "sell"
        return "hold"

    def order_quantity(
        self,
        action: str,
        instrument_id: str,
        positions: Mapping[str, Mapping[str, float]],
        equity: float,
        price: float,
        config: SimulationConfig,
        limits_by_instrument: Mapping[str, InstrumentLimit],
    ) -> float:
        if price <= 0 or equity <= 0:
            return 0.0
        if action == "sell":
            return positions.get(instrument_id, {}).get("quantity", 0.0)
        if action != "buy":
            return 0.0
        limit = limits_by_instrument.get(instrument_id)
        max_position_pct = config.max_position_pct
        if limit and limit.max_position_pct is not None and limit.max_position_pct > 0:
            max_position_pct = min(max_position_pct, limit.max_position_pct)
        target_pct = min(config.target_position_pct, max_position_pct)
        current_value = positions.get(instrument_id, {}).get("quantity", 0.0) * price
        target_value = equity * target_pct
        order_value = max(0.0, target_value - current_value)
        if limit and limit.max_order_value_rub is not None and limit.max_order_value_rub > 0:
            order_value = min(order_value, limit.max_order_value_rub)
        return math.floor((order_value / price) / config.quantity_step) * config.quantity_step

    def risk_flags(
        self,
        *,
        action: str,
        instrument_id: str,
        quantity: float,
        price: float,
        equity: float,
        positions: Mapping[str, Mapping[str, float]],
        config: SimulationConfig,
        instrument_limit: InstrumentLimit | None,
        portfolio_limits: Mapping[str, float],
    ) -> tuple[str, ...]:
        if action not in {"buy", "sell"} or quantity <= 0:
            return ()
        flags: list[str] = []
        order_value = quantity * price
        if instrument_limit and instrument_limit.max_order_value_rub is not None:
            if order_value > instrument_limit.max_order_value_rub:
                flags.append("max_order_value_rub_exceeded")
        if instrument_limit and instrument_limit.max_slippage_bps is not None:
            if config.slippage_bps > instrument_limit.max_slippage_bps:
                flags.append("max_slippage_bps_exceeded")
        if action == "buy" and equity > 0:
            existing_qty = positions.get(instrument_id, {}).get("quantity", 0.0)
            position_pct = (existing_qty + quantity) * price / equity
            max_position_pct = instrument_limit.max_position_pct if instrument_limit and instrument_limit.max_position_pct else config.max_position_pct
            if position_pct > max_position_pct:
                flags.append("max_position_pct_exceeded")
        gross_exposure_limit = portfolio_limits.get("max_gross_exposure_pct")
        if gross_exposure_limit is not None and equity > 0:
            projected = sum(abs(item.get("quantity", 0.0)) * price for item in positions.values()) + order_value
            if projected / equity > gross_exposure_limit:
                flags.append("max_gross_exposure_pct_exceeded")
        return tuple(dict.fromkeys(flags))

    def apply_trade(
        self,
        *,
        request: SimulationRequest,
        job: ModuleJob,
        vector: FeatureVector,
        action: str,
        quantity: float,
        raw_price: float,
        price: float,
        cash: float,
        positions: dict[str, dict[str, float]],
        config: SimulationConfig,
        risk_flags: tuple[str, ...],
        score: float,
    ) -> tuple[SimulatedTrade, float]:
        slippage = config.slippage_bps / 10_000.0
        fill_price = price * (1.0 + slippage if action == "buy" else 1.0 - slippage)
        trade_value = fill_price * quantity
        fees = trade_value * config.fee_rate_bps / 10_000.0
        current = positions.setdefault(vector.instrument_id, {"quantity": 0.0, "average_price": 0.0})
        pnl: float | None = None
        if action == "buy":
            total_cost = current["quantity"] * current["average_price"] + trade_value + fees
            current["quantity"] += quantity
            current["average_price"] = total_cost / current["quantity"] if current["quantity"] > 0 else 0.0
            cash -= trade_value + fees
        else:
            sell_qty = min(quantity, current["quantity"])
            pnl = (fill_price - current["average_price"]) * sell_qty - fees
            current["quantity"] -= sell_qty
            cash += fill_price * sell_qty - fees
            quantity = sell_qty
            if current["quantity"] <= 0:
                current["quantity"] = 0.0
                current["average_price"] = 0.0

        payload_id = {
            "simulation_id": request.simulation_id,
            "instrument_id": vector.instrument_id,
            "decision_ts": vector.as_of_ts,
            "side": action,
            "quantity": quantity,
            "feature_vector_id": vector.feature_vector_id,
            "job_id": job.job_id,
        }
        execution_result_id = stable_record_id("paper_execution_result", payload_id)
        order_intent_id = stable_record_id("simulated_order_intent", payload_id)
        return (
            SimulatedTrade(
                execution_result_id=execution_result_id,
                order_intent_id=order_intent_id,
                instrument_id=vector.instrument_id,
                side=action,
                decision_ts=vector.as_of_ts,
                horizon=vector.horizon,
                quantity=quantity,
                raw_price=raw_price,
                fill_price=fill_price,
                trade_value=trade_value,
                fees=fees,
                slippage_bps=config.slippage_bps,
                pnl=pnl,
                feature_vector_id=vector.feature_vector_id,
                score=score,
                risk_flags=risk_flags,
            ),
            cash,
        )

    def trade_to_execution_result(
        self,
        trade: SimulatedTrade,
        request: SimulationRequest,
        created_at: str,
    ) -> PaperExecutionResultRecord:
        return PaperExecutionResultRecord(
            execution_result_id=trade.execution_result_id,
            order_intent_id=trade.order_intent_id,
            status="filled",
            submitted_at=trade.decision_ts,
            last_update_at=created_at,
            filled_quantity=trade.quantity,
            avg_fill_price=trade.fill_price,
            fees=trade.fees,
            slippage_bps=trade.slippage_bps,
            errors=(),
            payload={
                "paper_execution_result": {
                    "execution_result_id": trade.execution_result_id,
                    "simulation_id": request.simulation_id,
                    "instrument_id": trade.instrument_id,
                    "side": trade.side,
                    "decision_ts": trade.decision_ts,
                    "horizon": trade.horizon,
                    "quantity": trade.quantity,
                    "fill_price": trade.fill_price,
                    "fees": trade.fees,
                    "slippage_bps": trade.slippage_bps,
                    "pnl": trade.pnl,
                },
                "simulated": True,
                "live_trade": False,
                "broker_order_id": None,
                "arena_go_order_sent": False,
                "run_mode": request.run_mode,
                "source_module": self.module_name,
                "calculation_version": CALCULATION_VERSION,
                "feature_vector_id": trade.feature_vector_id,
                "score": trade.score,
                "risk_flags": list(trade.risk_flags),
            },
        )

    def performance_metrics(
        self,
        *,
        equity_curve: tuple[float, ...],
        trades: tuple[SimulatedTrade, ...],
        bars: tuple[MarketBar, ...],
        trading_days: int,
        risk_free_rate: float,
        capacity_max_slippage_bps: float,
        feature_leakage: bool,
    ) -> dict[str, float]:
        if not equity_curve:
            equity_curve = (0.0,)
        total_ret = metrics.total_return(equity_curve[0], equity_curve[-1])
        annualized_ret = metrics.annualized_return(total_ret, trading_days)
        returns = metrics.daily_returns(equity_curve)
        vol = metrics.volatility(returns)
        downside_vol = metrics.downside_volatility(returns)
        slippages = tuple(trade.slippage_bps for trade in trades)
        fees = tuple(trade.fees for trade in trades)
        closed_pnls = tuple(trade.pnl for trade in trades if trade.pnl is not None)
        average_turnover = self.average_daily_turnover(bars)
        return {
            "total_return": total_ret,
            "annualized_return": annualized_ret,
            "max_drawdown": metrics.max_drawdown(equity_curve),
            "volatility": vol,
            "sharpe_ratio": metrics.sharpe_ratio(annualized_ret, vol, risk_free_rate),
            "sortino_ratio": metrics.sortino_ratio(annualized_ret, downside_vol, risk_free_rate),
            "turnover": metrics.turnover((trade.trade_value for trade in trades), equity_curve),
            "win_rate": metrics.win_rate(closed_pnls),
            "profit_factor": metrics.profit_factor(closed_pnls),
            "avg_slippage_bps": metrics.avg_slippage_bps(slippages),
            "fees_total": metrics.fees_total(fees),
            "capacity_estimate": metrics.capacity_estimate(
                average_turnover,
                capacity_max_slippage_bps,
                metrics.avg_slippage_bps(slippages),
                equity_curve[0],
            ),
            "feature_leakage_flag": float(metrics.feature_leakage_flag(feature_leakage)),
        }

    def bias_checks(
        self,
        *,
        leakage_detected: bool,
        missing_adjustments: bool,
        calendar_valid: bool,
    ) -> list[str]:
        return [
            "lookahead_bias_detected" if leakage_detected else "no_future_data_access_passed",
            "missing_corporate_adjustment" if missing_adjustments else "corporate_actions_applied",
            "invalid_calendar_alignment" if not calendar_valid else "trading_calendar_aligned",
        ]

    def price_index(self, bars: tuple[MarketBar, ...]) -> dict[str, tuple[MarketBar, ...]]:
        grouped: dict[str, list[MarketBar]] = {}
        for bar in bars:
            grouped.setdefault(bar.instrument_id, []).append(bar)
        return {
            instrument_id: tuple(sorted(items, key=lambda item: item.open_ts))
            for instrument_id, items in grouped.items()
        }

    def latest_bar_at(
        self,
        price_index: Mapping[str, tuple[MarketBar, ...]],
        instrument_id: str,
        as_of_ts: str,
    ) -> MarketBar | None:
        decision_dt = parse_utc_iso(as_of_ts)
        latest: MarketBar | None = None
        for bar in price_index.get(instrument_id, ()):
            if parse_utc_iso(bar.open_ts) <= decision_dt:
                latest = bar
            else:
                break
        return latest

    def adjusted_price(
        self,
        bar: MarketBar,
        decision_ts: str,
        corporate_actions: tuple[CorporateActionRecord, ...],
    ) -> float:
        factor = 1.0
        bar_date = parse_utc_iso(bar.open_ts).date()
        decision_date = parse_utc_iso(decision_ts).date()
        for action in corporate_actions:
            if action.instrument_id != bar.instrument_id or action.adjustment_factor is None:
                continue
            if action.adjustment_factor <= 0 or not action.effective_date:
                continue
            effective_date = parse_utc_iso(f"{action.effective_date}T00:00:00+00:00").date()
            if effective_date <= decision_date and bar_date < effective_date:
                factor *= action.adjustment_factor
        return bar.close_price * factor

    def apply_corporate_action_adjustments(
        self,
        bars: tuple[MarketBar, ...],
        corporate_actions: tuple[CorporateActionRecord, ...],
        decision_ts: str,
    ) -> tuple[MarketBar, ...]:
        adjusted: list[MarketBar] = []
        for bar in bars:
            adjusted_price = self.adjusted_price(bar, decision_ts, corporate_actions)
            adjusted.append(
                replace(
                    bar,
                    close_price=adjusted_price,
                    payload={
                        **dict(bar.payload),
                        "corporate_actions_applied": adjusted_price != bar.close_price,
                        "adjustment_decision_ts": decision_ts,
                    },
                )
            )
        return tuple(adjusted)

    def current_equity(
        self,
        cash: float,
        positions: Mapping[str, Mapping[str, float]],
        price_index: Mapping[str, tuple[MarketBar, ...]],
        corporate_actions: tuple[CorporateActionRecord, ...],
        as_of_ts: str,
    ) -> float:
        equity = cash
        for instrument_id, position in positions.items():
            quantity = position.get("quantity", 0.0)
            if quantity <= 0:
                continue
            bar = self.latest_bar_at(price_index, instrument_id, as_of_ts)
            if bar is None:
                equity += quantity * position.get("average_price", 0.0)
            else:
                equity += quantity * self.adjusted_price(bar, as_of_ts, corporate_actions)
        return equity

    def daily_equity_curve(
        self,
        equity_values: Any,
        equity_points: tuple[tuple[str, float], ...] | list[tuple[str, float]],
    ) -> tuple[dict[str, Any], ...]:
        if not equity_points:
            return tuple({"date": str(index), "equity": float(value)} for index, value in enumerate(equity_values))
        by_date: dict[str, float] = {}
        for ts, value in equity_points:
            by_date[parse_utc_iso(ts).date().isoformat()] = float(value)
        return tuple({"date": date, "equity": by_date[date]} for date in sorted(by_date))

    def trading_days(self, bars: tuple[MarketBar, ...], request: SimulationRequest) -> int:
        dates = {parse_utc_iso(bar.open_ts).date() for bar in bars if bar.open_ts}
        if dates:
            return len(dates)
        return max(1, (parse_utc_iso(request.to_ts).date() - parse_utc_iso(request.from_ts).date()).days + 1)

    def average_daily_turnover(self, bars: tuple[MarketBar, ...]) -> float:
        by_date: dict[str, float] = {}
        for bar in bars:
            date = parse_utc_iso(bar.open_ts).date().isoformat()
            by_date[date] = by_date.get(date, 0.0) + max(0.0, bar.turnover)
        if not by_date:
            return 0.0
        return sum(by_date.values()) / len(by_date)

    def rules_by_key(self, rules: tuple[MetricWeightRule, ...]) -> dict[tuple[str, str], tuple[MetricWeightRule, ...]]:
        grouped: dict[tuple[str, str], list[MetricWeightRule]] = {}
        for rule in rules:
            grouped.setdefault((rule.horizon, rule.metric_name), []).append(rule)
        return {key: tuple(values) for key, values in grouped.items()}

    def portfolio_limit_values(self, limits: tuple[PortfolioLimit, ...]) -> dict[str, float]:
        values: dict[str, float] = {}
        for limit in limits:
            if limit.limit_value is not None:
                values[limit.limit_name] = limit.limit_value
        return values

    def _config_from_policy(self, risk_policy: RiskPolicy | None) -> SimulationConfig:
        base = self.base_config
        policy_config = SimulationConfig.from_policy(
            risk_policy,
            {
                "initial_capital_rub": base.initial_capital_rub,
                "buy_threshold": base.buy_threshold,
                "sell_threshold": base.sell_threshold,
                "target_position_pct": base.target_position_pct,
                "max_position_pct": base.max_position_pct,
                "fee_rate_bps": base.fee_rate_bps,
                "slippage_bps": base.slippage_bps,
                "risk_free_rate": base.risk_free_rate,
                "capacity_max_slippage_bps": base.capacity_max_slippage_bps,
                "quantity_step": base.quantity_step,
            },
        )
        return SimulationConfig(
            initial_capital_rub=policy_config.initial_capital_rub or base.initial_capital_rub,
            buy_threshold=policy_config.buy_threshold,
            sell_threshold=policy_config.sell_threshold,
            target_position_pct=policy_config.target_position_pct or base.target_position_pct,
            max_position_pct=policy_config.max_position_pct or base.max_position_pct,
            fee_rate_bps=policy_config.fee_rate_bps,
            slippage_bps=policy_config.slippage_bps,
            risk_free_rate=policy_config.risk_free_rate,
            capacity_max_slippage_bps=policy_config.capacity_max_slippage_bps,
            quantity_step=policy_config.quantity_step,
        )

    def data_quality_score(self, status: str, warnings: list[str]) -> float:
        if status == "invalid":
            return 0.0
        score = 1.0
        penalty_map = {
            "corporate_actions_empty": 0.05,
            "no_simulated_trades": 0.10,
            "weights_profile_missing": 0.25,
            "risk_policy_missing": 0.25,
            "metric_weight_rules_missing": 0.20,
            "feature_history_missing": 0.35,
            "historical_raw_data_missing": 0.35,
        }
        for warning in set(warnings):
            score -= penalty_map.get(warning, 0.0)
        return _clip(score, 0.0, 1.0)

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> BacktestingPaperTradingRunResult:
        return BacktestingPaperTradingRunResult(
            module_job_result=ModuleJobResult(
                job_id=job.job_id,
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
                events_written=0,
                data_quality_score=0.0,
            ),
            simulation_report=None,
        )

    def _missing_job_result(self, started_at: str) -> BacktestingPaperTradingRunResult:
        return BacktestingPaperTradingRunResult(
            module_job_result=ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=(),
                warnings=(),
                errors=(f"{self.module_name} requires module_job",),
                metrics_written=0,
                events_written=0,
                data_quality_score=0.0,
            ),
            simulation_report=None,
        )


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    parsed = _maybe_float(value)
    return default if parsed is None else parsed


def _positive_float(value: Any, default: float) -> float:
    parsed = _maybe_float(value)
    return default if parsed is None or parsed <= 0 else parsed


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
