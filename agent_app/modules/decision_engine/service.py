from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)
from agent_app.runtime_calendar import current_market_session

from .metrics import (
    clip,
    data_quality_penalty,
    decision_confidence_score,
    expected_edge_score,
    feature_contribution,
    portfolio_concentration_risk,
    signed_target_position_pct,
    target_position_pct,
    target_quantity,
    threshold_margin,
    weighted_average,
)
from .repository import (
    AuditRecord,
    DecisionEngineRepository,
    DecisionExplanation,
    DecisionRecord,
    DecisionRequestRecord,
    DecisionSetRecord,
    FeatureVector,
    InMemoryDecisionEngineRepository,
    MarketStateRecord,
    MetricWeightRule,
    PortfolioSnapshot,
    PositionState,
    WeightsProfile,
    stable_record_id,
    stable_uuid,
)


MODULE_NAME = "Decision Engine Module"
CALCULATION_VERSION = "decision_engine_v1"
VALID_CONTOURS = {"decision_contour", "event_contour", "intraday_contour", "daily_contour"}
VALID_HORIZONS = {"intraday", "swing", "position"}
VALID_RUN_MODES = {"analysis_only", "paper_trading", "live_trading"}
VALID_DECISION_MODES = {"normal", "risk_off", "reduce_only", "manual_approval_required"}
VALID_ACTIONS = {"buy", "sell", "hold", "reduce", "close", "block"}
INPUT_FIELDS = {
    "decision_request_id",
    "universe_id",
    "instrument_ids",
    "horizon",
    "as_of_ts",
    "feature_vector_refs",
    "portfolio_state_ref",
    "weights_profile_id",
    "run_mode",
    "decision_mode",
}
ACTION_THRESHOLD = 0.05
MIN_COVERAGE_RATIO = 0.50
PORTFOLIO_STALE_SECONDS = 86_400
MARKET_STATE_STALE_SECONDS = 86_400
MAX_POSITION_PCT = 0.10
RISK_BLOCK_THRESHOLD = 0.85
RISK_REDUCE_THRESHOLD = 0.65
RISK_METRIC_NAMES = (
    "volatility_risk_score",
    "liquidity_risk_score",
    "portfolio_concentration_risk",
    "data_quality_penalty",
)
NON_CENTERED_DECISION_METRICS = {
    "trade_urgency_score",
    "turnover_deficit_score",
}


class DecisionEngineError(ValueError):
    """Raised when module 17 would violate its documented contract."""


@dataclass(frozen=True)
class DecisionRequest:
    decision_request_id: str
    universe_id: str
    instrument_ids: tuple[str, ...]
    horizon: str
    as_of_ts: str
    feature_vector_refs: tuple[str, ...]
    portfolio_state_ref: str
    weights_profile_id: str
    run_mode: str
    decision_mode: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "DecisionRequest":
        extra_top_level = sorted(set(payload) - {"decision_request"})
        if extra_top_level:
            raise DecisionEngineError(f"payload has undocumented fields: {extra_top_level}")
        request_payload = payload.get("decision_request")
        if not isinstance(request_payload, Mapping):
            raise DecisionEngineError("payload must contain decision_request")
        missing_fields = sorted(INPUT_FIELDS - set(request_payload))
        if missing_fields:
            raise DecisionEngineError(f"decision_request missing required fields: {missing_fields}")
        extra_fields = sorted(set(request_payload) - INPUT_FIELDS)
        if extra_fields:
            raise DecisionEngineError(f"decision_request has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (request_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise DecisionEngineError("decision_request.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise DecisionEngineError("decision_request.instrument_ids must match module_job.instrument_ids")

        horizon = str(request_payload.get("horizon") or "")
        if horizon not in VALID_HORIZONS:
            raise DecisionEngineError("decision_request.horizon is invalid")
        if tuple(job.horizons) and horizon not in set(job.horizons):
            raise DecisionEngineError("decision_request.horizon must be included in module_job.horizons")

        as_of_ts = str(request_payload.get("as_of_ts") or "")
        parse_utc_iso(as_of_ts)
        if parse_utc_iso(as_of_ts) > parse_utc_iso(job.time_range.to_ts):
            raise DecisionEngineError("decision_request.as_of_ts must be <= module_job.time_range.to_ts")

        run_mode = str(request_payload.get("run_mode") or "")
        if run_mode not in VALID_RUN_MODES:
            raise DecisionEngineError("decision_request.run_mode is invalid")
        if run_mode != job.run_mode:
            raise DecisionEngineError("decision_request.run_mode must match module_job.run_mode")

        decision_mode = str(request_payload.get("decision_mode") or "")
        if decision_mode not in VALID_DECISION_MODES:
            raise DecisionEngineError("decision_request.decision_mode is invalid")

        required_text = {
            "decision_request_id": request_payload.get("decision_request_id"),
            "universe_id": request_payload.get("universe_id"),
            "portfolio_state_ref": request_payload.get("portfolio_state_ref"),
            "weights_profile_id": request_payload.get("weights_profile_id"),
        }
        missing_text = [name for name, value in required_text.items() if not str(value or "")]
        if missing_text:
            raise DecisionEngineError(f"decision_request missing text fields: {missing_text}")
        if str(request_payload.get("universe_id")) != job.universe_id:
            raise DecisionEngineError("decision_request.universe_id must match module_job.universe_id")
        feature_vector_refs = tuple(str(item) for item in (request_payload.get("feature_vector_refs") or ()))
        if not feature_vector_refs:
            raise DecisionEngineError("decision_request.feature_vector_refs is required")

        return cls(
            decision_request_id=str(request_payload.get("decision_request_id")),
            universe_id=str(request_payload.get("universe_id")),
            instrument_ids=instrument_ids,
            horizon=horizon,
            as_of_ts=as_of_ts,
            feature_vector_refs=feature_vector_refs,
            portfolio_state_ref=str(request_payload.get("portfolio_state_ref") or ""),
            weights_profile_id=str(request_payload.get("weights_profile_id") or ""),
            run_mode=run_mode,
            decision_mode=decision_mode,
        )

    def to_record(self) -> DecisionRequestRecord:
        return DecisionRequestRecord(
            decision_request_id=self.decision_request_id,
            universe_id=self.universe_id,
            instrument_ids=self.instrument_ids,
            horizon=self.horizon,
            as_of_ts=self.as_of_ts,
            feature_vector_refs=self.feature_vector_refs,
            portfolio_state_ref=self.portfolio_state_ref,
            weights_profile_id=self.weights_profile_id,
            run_mode=self.run_mode,
            decision_mode=self.decision_mode,
            payload={"calculation_version": CALCULATION_VERSION},
        )


@dataclass(frozen=True)
class DecisionPolicy:
    action_threshold: float = ACTION_THRESHOLD
    min_coverage_ratio: float = MIN_COVERAGE_RATIO
    max_position_pct: float = MAX_POSITION_PCT
    portfolio_stale_seconds: int = PORTFOLIO_STALE_SECONDS
    market_state_stale_seconds: int = MARKET_STATE_STALE_SECONDS
    risk_block_threshold: float = RISK_BLOCK_THRESHOLD
    risk_reduce_threshold: float = RISK_REDUCE_THRESHOLD
    turnover_urgency_boost_factor: float = field(
        default_factory=lambda: _env_float("DECISION_TURNOVER_URGENCY_BOOST_FACTOR", 0.25)
    )
    macro_range_edge_multiplier: float = field(
        default_factory=lambda: _env_float("DECISION_MACRO_RANGE_EDGE_MULTIPLIER", 0.75)
    )
    macro_degraded_edge_multiplier: float = field(
        default_factory=lambda: _env_float("DECISION_MACRO_DEGRADED_EDGE_MULTIPLIER", 0.75)
    )
    macro_risk_on_low_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_MACRO_RISK_ON_LOW_THRESHOLD", 0.55)
    )
    macro_breadth_weak_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_MACRO_BREADTH_WEAK_THRESHOLD", 0.55)
    )
    allow_short_selling: bool = field(
        default_factory=lambda: _env_bool("DECISION_ALLOW_SHORT_SELLING", True)
    )
    exit_overlay_enabled: bool = field(
        default_factory=lambda: _env_bool("DECISION_EXIT_OVERLAY_ENABLED", True)
    )
    take_profit_pct: float = field(
        default_factory=lambda: _env_float("DECISION_TAKE_PROFIT_PCT", 0.012)
    )
    stop_loss_pct: float = field(
        default_factory=lambda: _env_float("DECISION_STOP_LOSS_PCT", 0.008)
    )
    take_profit_hold_edge_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_TAKE_PROFIT_HOLD_EDGE_THRESHOLD", 0.06)
    )
    use_post_cost_edge_for_actions: bool = field(
        default_factory=lambda: _env_bool("DECISION_USE_POST_COST_EDGE_FOR_ACTIONS", True)
    )
    conservative_cost_proxy_bps: float = field(
        default_factory=lambda: _env_float("DECISION_CONSERVATIVE_COST_PROXY_BPS", 8.0)
    )
    min_post_cost_edge_score: float = field(
        default_factory=lambda: _env_float("DECISION_MIN_POST_COST_EDGE_SCORE", ACTION_THRESHOLD)
    )
    partial_take_profit_enabled: bool = field(
        default_factory=lambda: _env_bool("DECISION_PARTIAL_TAKE_PROFIT_ENABLED", True)
    )
    partial_take_profit_ratio: float = field(
        default_factory=lambda: _env_float("DECISION_PARTIAL_TAKE_PROFIT_RATIO", 0.5)
    )
    profit_lock_enabled: bool = field(
        default_factory=lambda: _env_bool("DECISION_PROFIT_LOCK_ENABLED", True)
    )
    trailing_stop_pct: float = field(
        default_factory=lambda: _env_float("DECISION_TRAILING_STOP_PCT", 0.006)
    )
    exit_use_post_cost_edge: bool = field(
        default_factory=lambda: _env_bool("DECISION_EXIT_USE_POST_COST_EDGE", True)
    )
    exit_edge_hold_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_EXIT_EDGE_HOLD_THRESHOLD", 0.06)
    )
    short_entry_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_ENTRY_THRESHOLD", 0.012)
    )
    short_add_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_ADD_THRESHOLD", 0.018)
    )
    short_exit_edge_threshold: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_EXIT_EDGE_THRESHOLD", 0.0)
    )
    allow_long_to_short_flip: bool = field(
        default_factory=lambda: _env_bool("DECISION_ALLOW_LONG_TO_SHORT_FLIP", False)
    )
    short_use_post_cost_edge: bool = field(
        default_factory=lambda: _env_bool("DECISION_SHORT_USE_POST_COST_EDGE", True)
    )
    short_take_profit_pct: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_TAKE_PROFIT_PCT", 0.012)
    )
    short_partial_take_profit_ratio: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_PARTIAL_TAKE_PROFIT_RATIO", 0.5)
    )
    short_trailing_stop_pct: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_TRAILING_STOP_PCT", 0.006)
    )
    short_stop_loss_pct: float = field(
        default_factory=lambda: _env_float("DECISION_SHORT_STOP_LOSS_PCT", 0.008)
    )
    short_profit_lock_enabled: bool = field(
        default_factory=lambda: _env_bool("DECISION_SHORT_PROFIT_LOCK_ENABLED", True)
    )


@dataclass(frozen=True)
class InstrumentDecision:
    record: DecisionRecord
    explanation: DecisionExplanation
    decision_payload: Mapping[str, Any]


@dataclass(frozen=True)
class DecisionEngineExecutionResult:
    module_job_result: ModuleJobResult
    decision_set: DecisionSetRecord | None
    decision_records: tuple[DecisionRecord, ...]
    decision_explanations: tuple[DecisionExplanation, ...]
    decision_request_ref: str | None = None
    decision_set_ref: str | None = None
    decision_record_refs: tuple[str, ...] = ()
    decision_explanation_refs: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "decision_set": self.decision_set.to_contract() if self.decision_set else None,
            "decision_records": [_decision_record_to_dict(record) for record in self.decision_records],
            "decision_explanations": [_decision_explanation_to_dict(item) for item in self.decision_explanations],
            "decision_request_ref": self.decision_request_ref,
            "decision_set_ref": self.decision_set_ref,
            "decision_record_refs": list(self.decision_record_refs),
            "decision_explanation_refs": list(self.decision_explanation_refs),
            "audit_refs": list(self.audit_refs),
        }


class DecisionEngineService:
    module_name = MODULE_NAME

    def __init__(self, repository: DecisionEngineRepository | None = None, policy: DecisionPolicy | None = None) -> None:
        self.repository = repository or InMemoryDecisionEngineRepository()
        self.policy = policy or DecisionPolicy()

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> DecisionEngineExecutionResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> DecisionEngineExecutionResult:
        return self.execute(payload, job)

    def execute(self, payload: Mapping[str, Any], job: ModuleJob | None) -> DecisionEngineExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            audit_refs: tuple[str, ...] = ()
            try:
                audit_refs = (
                    self.repository.save_audit_record(
                        AuditRecord(
                            audit_record_id=stable_record_id(
                                "audit_decision_engine_missing_job",
                                {"module_name": self.module_name, "error": "module_job_required"},
                            ),
                            module_name=self.module_name,
                            job_id="missing",
                            severity="error",
                            event_type="decision_engine_failed",
                            message=f"{self.module_name} requires module_job",
                            object_type="module_job",
                            object_ref="audit.module_job:missing",
                            reason_codes=("module_job_required",),
                            payload={"calculation_version": CALCULATION_VERSION},
                        )
                    ),
                )
            except Exception:
                audit_refs = ()
            result = ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=audit_refs,
                errors=(f"{self.module_name} requires module_job",),
            )
            return DecisionEngineExecutionResult(result, None, (), (), audit_refs=audit_refs)
        try:
            self.validate_module_job(job)
            request = DecisionRequest.from_dict(payload, job)
            feature_vectors = self.repository.list_feature_vectors(
                feature_vector_refs=request.feature_vector_refs,
                instrument_ids=request.instrument_ids,
                horizon=request.horizon,
                as_of_ts=request.as_of_ts,
            )
            weights_profile = self.repository.get_weights_profile(request.weights_profile_id)
            metric_rules = self.repository.list_metric_weight_rules(
                weights_profile_id=request.weights_profile_id,
                horizon=request.horizon,
                instrument_ids=request.instrument_ids,
            )
            portfolio_snapshot = self.repository.get_portfolio_snapshot(
                portfolio_state_ref=request.portfolio_state_ref,
                universe_id=request.universe_id,
                as_of_ts=request.as_of_ts,
            )
            positions = self.repository.list_position_states(
                portfolio_id=portfolio_snapshot.portfolio_id if portfolio_snapshot else "",
                instrument_ids=request.instrument_ids,
                as_of_ts=request.as_of_ts,
            ) if portfolio_snapshot else ()
            market_state = self.repository.get_market_state(request.universe_id, request.as_of_ts)

            decision_set, decisions, explanations, warnings = self.build_decision_set(
                request=request,
                feature_vectors=feature_vectors,
                weights_profile=weights_profile,
                metric_rules=metric_rules,
                portfolio_snapshot=portfolio_snapshot,
                positions=positions,
                market_state=market_state,
                job=job,
            )

            decision_request_ref = self.repository.save_decision_request(request.to_record())
            decision_set_ref = self.repository.save_decision_set(decision_set)
            decision_record_refs = tuple(self.repository.save_decision_record(record) for record in decisions)
            explanation_refs = tuple(self.repository.save_decision_explanation(explanation) for explanation in explanations)
            audit_record = self.build_audit_record(job, request, decision_set, warnings)
            audit_refs = (self.repository.save_audit_record(audit_record),)

            output_refs = (decision_set_ref, *decision_record_refs, *explanation_refs, *audit_refs)
            status = "success" if decisions and not warnings else "partial_success" if decisions else "failed"
            return DecisionEngineExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(decisions),
                    data_quality_score=_mean(record.confidence_score for record in decisions),
                ),
                decision_set=decision_set,
                decision_records=decisions,
                decision_explanations=explanations,
                decision_request_ref=decision_request_ref,
                decision_set_ref=decision_set_ref,
                decision_record_refs=decision_record_refs,
                decision_explanation_refs=explanation_refs,
                audit_refs=audit_refs,
            )
        except (DecisionEngineError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise DecisionEngineError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise DecisionEngineError("module_job.module_name must be Decision Engine Module")
        if job.contour not in VALID_CONTOURS:
            raise DecisionEngineError("module_job.contour is not valid for Decision Engine Module")
        if not job.instrument_ids:
            raise DecisionEngineError("module_job.instrument_ids is required")
        if not job.horizons:
            raise DecisionEngineError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise DecisionEngineError(f"invalid module_job.horizons: {invalid_horizons}")
        if job.run_mode not in VALID_RUN_MODES:
            raise DecisionEngineError("module_job.run_mode must be analysis_only, paper_trading, or live_trading")
        if not job.config_ref:
            raise DecisionEngineError("module_job.config_ref is required")

    def build_decision_set(
        self,
        *,
        request: DecisionRequest,
        feature_vectors: tuple[FeatureVector, ...],
        weights_profile: WeightsProfile | None,
        metric_rules: tuple[MetricWeightRule, ...],
        portfolio_snapshot: PortfolioSnapshot | None,
        positions: tuple[PositionState, ...],
        market_state: MarketStateRecord | None,
        job: ModuleJob,
    ) -> tuple[DecisionSetRecord, tuple[DecisionRecord, ...], tuple[DecisionExplanation, ...], tuple[str, ...]]:
        warnings: list[str] = []
        vector_by_instrument = {vector.instrument_id: vector for vector in feature_vectors}
        position_by_instrument = {position.instrument_id: position for position in positions}
        global_block_reasons = self.global_block_reasons(
            request=request,
            weights_profile=weights_profile,
            portfolio_snapshot=portfolio_snapshot,
            market_state=market_state,
        )
        warnings.extend(global_block_reasons)
        if weights_profile is None:
            metric_rules = ()
        rules_by_metric = self.rules_by_metric(metric_rules)

        instrument_decisions: list[InstrumentDecision] = []
        for instrument_id in request.instrument_ids:
            vector = vector_by_instrument.get(instrument_id)
            position = position_by_instrument.get(instrument_id)
            instrument_decisions.append(
                self.build_instrument_decision(
                    request=request,
                    instrument_id=instrument_id,
                    vector=vector,
                    weights_profile=weights_profile,
                    rules_by_metric=rules_by_metric,
                    portfolio_snapshot=portfolio_snapshot,
                    position=position,
                    market_state=market_state,
                    global_block_reasons=global_block_reasons,
                    config_ref=str(job.config_ref or ""),
                )
            )

        decision_payloads = tuple(item.decision_payload for item in instrument_decisions)
        decision_set = DecisionSetRecord(
            decision_set_id=stable_record_id(
                "decision_set",
                {
                    "decision_request_id": request.decision_request_id,
                    "weights_profile_id": request.weights_profile_id,
                    "horizon": request.horizon,
                    "as_of_ts": request.as_of_ts,
                    "calculation_version": f"{CALCULATION_VERSION}:{job.config_ref}",
                },
            ),
            decision_request_id=request.decision_request_id,
            horizon=request.horizon,
            decisions=decision_payloads,
            calculation_version=f"{CALCULATION_VERSION}:{job.config_ref}",
            created_at=to_utc_iso(utc_now()),
        )
        records = tuple(item.record for item in instrument_decisions)
        explanations = tuple(item.explanation for item in instrument_decisions)
        return decision_set, records, explanations, tuple(dict.fromkeys(warnings))

    def build_instrument_decision(
        self,
        *,
        request: DecisionRequest,
        instrument_id: str,
        vector: FeatureVector | None,
        weights_profile: WeightsProfile | None,
        rules_by_metric: Mapping[str, MetricWeightRule],
        portfolio_snapshot: PortfolioSnapshot | None,
        position: PositionState | None,
        market_state: MarketStateRecord | None,
        global_block_reasons: tuple[str, ...],
        config_ref: str,
    ) -> InstrumentDecision:
        reason_codes: list[str] = list(global_block_reasons)
        if vector is None:
            reason_codes.append("feature_vector_missing")
            vector_features: Mapping[str, Mapping[str, Any]] = {}
            coverage_ratio = 0.0
            data_quality_score = 0.0
        else:
            vector_features = vector.features
            coverage_ratio = clip(vector.coverage_ratio)
            data_quality_score = clip(vector.data_quality_score)
            if coverage_ratio < self.policy.min_coverage_ratio:
                reason_codes.append("feature_coverage_low")

        contributions: dict[str, float] = {}
        contribution_confidences: list[float] = []
        blocked_by_expired = False
        for metric_name, feature_payload in vector_features.items():
            rule = rules_by_metric.get(metric_name)
            if rule is None or rule.metric_name in RISK_METRIC_NAMES:
                continue
            normalized_value = _payload_float(feature_payload, "normalized_value")
            raw_value = _payload_float(feature_payload, "raw_value")
            feature_confidence = clip(_payload_float(feature_payload, "confidence_score"))
            ttl_status = str(feature_payload.get("ttl_status") or "fresh")
            if feature_confidence < rule.min_confidence_score:
                reason_codes.append(f"feature_confidence_below_rule:{metric_name}")
                continue
            freshness_multiplier = self.freshness_multiplier(metric_name, ttl_status, rule)
            if freshness_multiplier is None:
                blocked_by_expired = True
                reason_codes.append(f"expired_feature_blocked:{metric_name}")
                continue
            confidence = feature_confidence * freshness_multiplier
            contribution_input = normalized_value if normalized_value is not None else raw_value
            contributions[metric_name] = feature_contribution(
                normalized_feature=contribution_input,
                weight=rule.weight,
                direction=rule.direction,
                confidence=confidence,
                centered=normalized_value is not None and metric_name not in NON_CENTERED_DECISION_METRICS,
            )
            contribution_confidences.append(confidence)

        if blocked_by_expired:
            reason_codes.append("expired_features_not_allowed")
        if weights_profile is None or weights_profile.status != "active":
            reason_codes.append("weights_profile_not_active")
        elif not contributions and not global_block_reasons:
            reason_codes.append("no_weighted_features")
        if request.decision_mode == "manual_approval_required":
            reason_codes.append("manual_approval_required")

        raw_edge = expected_edge_score(contributions)
        turnover_context = self.turnover_context(portfolio_snapshot)
        turnover_urgency = turnover_context.get("trade_urgency_score", 0.0)
        edge = raw_edge
        if (
            request.run_mode == "live_trading"
            and raw_edge != 0
            and turnover_urgency > 0
            and (raw_edge > 0 or self.policy.allow_short_selling)
        ):
            turnover_boost = raw_edge * self.policy.turnover_urgency_boost_factor * turnover_urgency
            contributions["turnover_urgency_score"] = turnover_boost
            edge = raw_edge + turnover_boost
            reason_codes.append("turnover_mandate_urgency")
        macro_overlay = self.macro_regime_overlay(vector_features, market_state)
        if macro_overlay["edge_penalty"] > 0:
            contributions["macro_regime_overlay_penalty"] = -macro_overlay["edge_penalty"]
        if macro_overlay["edge_multiplier"] < 1.0:
            contributions["macro_regime_overlay_multiplier"] = -(1.0 - macro_overlay["edge_multiplier"]) * max(edge, 0.0)
        edge = (edge * macro_overlay["edge_multiplier"]) - macro_overlay["edge_penalty"]
        reason_codes.extend(str(item) for item in macro_overlay["reason_codes"])
        risk_values = self.risk_components(
            vector_features=vector_features,
            data_quality_score=data_quality_score,
            portfolio_snapshot=portfolio_snapshot,
        )
        risk_weights = {
            metric_name: rules_by_metric[metric_name].weight
            for metric_name in RISK_METRIC_NAMES
            if metric_name in rules_by_metric
        }
        base_risk = weighted_average(risk_values, risk_weights)
        risk = clip(base_risk + macro_overlay["risk_penalty"])
        execution_cost_bps = self.execution_cost_estimate_bps(vector_features)
        commission_bps = self.commission_bps(vector_features)
        cost_score = execution_cost_bps / 10_000.0
        expected_edge_after_cost = self.post_cost_edge_score(edge, execution_cost_bps)
        edge_to_cost_ratio = self.edge_to_cost_ratio(edge, execution_cost_bps)
        if self.execution_cost_uses_conservative_proxy(vector_features):
            reason_codes.append("execution_cost_conservative_proxy")
        if edge > 0 and expected_edge_after_cost < self.policy.min_post_cost_edge_score:
            reason_codes.append("post_cost_edge_below_threshold")
        if edge < 0 and expected_edge_after_cost > -abs(self.policy.short_entry_threshold):
            reason_codes.append("short_post_cost_edge_below_threshold")
        action_edge = expected_edge_after_cost if self.policy.use_post_cost_edge_for_actions else edge
        confidence = decision_confidence_score(
            coverage_ratio=coverage_ratio,
            feature_confidences=contribution_confidences,
            weights_profile_confidence=1.0 if weights_profile and weights_profile.status == "active" else 0.0,
        )
        margin = threshold_margin(action_edge, self.policy.action_threshold)
        latest_price = self.latest_price(vector_features, position)
        equity = portfolio_snapshot.equity if portfolio_snapshot and portfolio_snapshot.equity is not None else portfolio_snapshot.cash if portfolio_snapshot else None
        max_position_pct = self.portfolio_max_position_pct(portfolio_snapshot)
        current_quantity = float(position.quantity) if position else 0.0
        current_value = 0.0
        if position and position.market_value is not None:
            current_value = float(position.market_value or 0.0)
        elif latest_price is not None and current_quantity != 0:
            current_value = current_quantity * latest_price
        current_pct = (
            max(-max_position_pct, min(max_position_pct, current_value / float(equity)))
            if equity and equity > 0
            else 0.0
        )
        target_edge = 0.0
        if action_edge > 0:
            target_edge = action_edge
        elif edge < 0 and action_edge < 0 and self.policy.allow_short_selling:
            target_edge = action_edge
        desired_target_pct = (
            signed_target_position_pct(target_edge, risk, max_position_pct)
            if target_edge != 0 and (target_edge > 0 or self.policy.allow_short_selling)
            else 0.0
        )
        desired_target_value = float(equity or 0.0) * abs(desired_target_pct)
        desired_target_abs_quantity = target_quantity(target_position_value=desired_target_value, latest_price=latest_price, quantity_step=1.0)
        desired_target_quantity = desired_target_abs_quantity if desired_target_pct >= 0 else -desired_target_abs_quantity
        if current_quantity > 0 and desired_target_quantity < 0 and not self.policy.allow_long_to_short_flip:
            desired_target_pct = 0.0
            desired_target_quantity = 0.0
            reason_codes.append("long_to_short_flip_disabled")
        if current_quantity < 0 and desired_target_quantity > 0:
            desired_target_pct = 0.0
            desired_target_quantity = 0.0
            reason_codes.append("short_to_long_flip_reduced_to_cover")
        action = self.choose_action(
            request=request,
            expected_edge=action_edge,
            gross_expected_edge=edge,
            expected_edge_after_cost=expected_edge_after_cost,
            risk_score=risk,
            margin=margin,
            reason_codes=tuple(reason_codes),
            position=position,
        )
        exit_overlay = self.position_exit_overlay(
            position=position,
            latest_price=latest_price,
            current_quantity=current_quantity,
            current_pct=current_pct,
            expected_edge=edge,
            expected_edge_after_cost=expected_edge_after_cost,
            risk_score=risk,
        )
        if exit_overlay is not None:
            action, desired_target_pct, desired_target_quantity, exit_reasons = exit_overlay
            reason_codes.extend(exit_reasons)
        min_rebalance_value = max(1_000.0, float(equity or 0.0) * 0.002)
        rebalance_value = abs(current_quantity - desired_target_quantity) * float(latest_price or 0.0)
        if action == "buy" and current_quantity > 0 and desired_target_quantity <= current_quantity:
            action = "hold"
            reason_codes.append("target_position_already_reached")
        elif action == "sell" and desired_target_quantity >= current_quantity:
            action = "hold"
            reason_codes.append("target_position_already_reached")
        elif action == "hold" and current_quantity > 0 and desired_target_quantity < current_quantity and rebalance_value >= min_rebalance_value:
            if action_edge <= 0 or margin <= 0 or "macro_context_degraded" in reason_codes or risk >= self.policy.risk_reduce_threshold:
                action = "reduce"
                reason_codes.append("over_target_rebalance")
        elif action == "hold" and current_quantity < 0 and desired_target_quantity > current_quantity and rebalance_value >= min_rebalance_value:
            if action_edge >= 0 or margin <= 0 or "macro_context_degraded" in reason_codes or risk >= self.policy.risk_reduce_threshold:
                action = "reduce"
                reason_codes.append("short_over_target_rebalance")
        if action == "block" and risk >= self.policy.risk_block_threshold:
            reason_codes.append("risk_score_block")
        elif action == "hold" and action_edge > 0 and risk >= self.policy.risk_reduce_threshold:
            reason_codes.append("risk_score_hold")
        elif action == "hold" and margin <= 0:
            reason_codes.append("edge_below_threshold")
        elif action in {"reduce", "close"} and request.decision_mode == "risk_off":
            reason_codes.append("decision_mode_risk_off")
        if action in {"buy", "sell", "reduce"}:
            target_pct = desired_target_pct
            quantity = desired_target_quantity
        elif action == "hold" and current_quantity > 0:
            target_pct = current_pct
            quantity = current_quantity
        elif action == "hold" and current_quantity < 0:
            target_pct = current_pct
            quantity = current_quantity
        else:
            target_pct = 0.0
            quantity = 0.0
        if action in {"close", "block"}:
            quantity = 0.0
            target_pct = 0.0
        position_effect = self.position_effect_for_action(
            action=action,
            current_quantity=current_quantity,
            target_quantity=quantity,
        )

        decision_record_id = stable_uuid(
            {
                "object_type": "decision_record",
                "decision_request_id": request.decision_request_id,
                "instrument_id": instrument_id,
                "as_of_ts": request.as_of_ts,
                "weights_profile_id": request.weights_profile_id,
                "calculation_version": f"{CALCULATION_VERSION}:{config_ref}",
            }
        )
        decision_set_id = stable_record_id(
            "decision_set",
            {
                "decision_request_id": request.decision_request_id,
                "weights_profile_id": request.weights_profile_id,
                "horizon": request.horizon,
                "as_of_ts": request.as_of_ts,
                "calculation_version": f"{CALCULATION_VERSION}:{config_ref}",
            },
        )
        cleaned_reasons = tuple(dict.fromkeys(reason_codes or ("decision_scored",)))
        record = DecisionRecord(
            decision_record_id=decision_record_id,
            decision_set_id=decision_set_id,
            instrument_id=instrument_id,
            action=action,
            target_position_pct=target_pct,
            target_quantity=quantity,
            confidence_score=confidence,
            expected_edge_score=edge,
            risk_score=risk,
            primary_reason_codes=cleaned_reasons,
            feature_contributions=contributions,
        )
        explanation = DecisionExplanation(
            decision_explanation_id=stable_uuid(
                {
                    "object_type": "decision_explanation",
                    "decision_record_id": decision_record_id,
                    "calculation_version": f"{CALCULATION_VERSION}:{config_ref}",
                }
            ),
            decision_record_id=decision_record_id,
            explanation={
                "decision_request_id": request.decision_request_id,
                "feature_vector_id": vector.feature_vector_id if vector else None,
                "weights_profile_id": weights_profile.weights_profile_id if weights_profile else request.weights_profile_id,
                "horizon": request.horizon,
                "run_mode": request.run_mode,
                "decision_mode": request.decision_mode,
                "decision_threshold_margin": margin,
                "configured_action_threshold": self.policy.action_threshold,
                "decision_action_edge_score": action_edge,
                "max_position_pct": max_position_pct,
                "risk_components": risk_values,
                "base_risk_score": base_risk,
                "macro_regime_overlay": macro_overlay,
                "latest_price": latest_price,
                "feature_contributions": contributions,
                "raw_expected_edge_score": raw_edge,
                "gross_expected_edge_score": edge,
                "expected_edge_after_cost_score": expected_edge_after_cost,
                "cost_score": cost_score,
                "commission_bps": commission_bps,
                "execution_cost_estimate_bps": execution_cost_bps,
                "edge_to_cost_ratio": edge_to_cost_ratio,
                "position_effect": position_effect,
                "short_selling_enabled": self.policy.allow_short_selling,
                "turnover_context": turnover_context,
                "reason_codes": list(cleaned_reasons),
                "risk_control_required": True,
                "order_intent_created": False,
                "calculation_version": f"{CALCULATION_VERSION}:{config_ref}",
            },
        )
        decision_payload = _decision_record_to_dict(record)
        decision_payload["expected_edge_after_cost_score"] = expected_edge_after_cost
        decision_payload["gross_expected_edge_score"] = edge
        decision_payload["decision_action_edge_score"] = action_edge
        decision_payload["execution_cost_estimate_bps"] = execution_cost_bps
        decision_payload["commission_bps"] = commission_bps
        decision_payload["edge_to_cost_ratio"] = edge_to_cost_ratio
        decision_payload["position_effect"] = position_effect
        decision_payload["short_selling_enabled"] = self.policy.allow_short_selling
        return InstrumentDecision(record=record, explanation=explanation, decision_payload=decision_payload)


    def execution_cost_estimate_bps(self, vector_features: Mapping[str, Mapping[str, Any]]) -> float:
        total = 0.0
        saw_cost_input = False
        for metric_name in ("estimated_order_slippage_bps", "estimated_slippage_bps", "spread_bps", "commission_bps"):
            value = _feature_numeric(vector_features, metric_name)
            if value is not None and value > 0:
                saw_cost_input = True
                total += float(value)
        if _feature_numeric(vector_features, "commission_bps") is None:
            total += self.commission_bps(vector_features)
        if not saw_cost_input:
            total += max(0.0, self.policy.conservative_cost_proxy_bps)
        return max(0.0, total)

    def commission_bps(self, vector_features: Mapping[str, Mapping[str, Any]]) -> float:
        value = _feature_numeric(vector_features, "commission_bps")
        if value is not None and value >= 0:
            return float(value)
        return max(0.0, _env_float("ARENA_GO_COMMISSION_BPS", 0.0))

    def execution_cost_uses_conservative_proxy(self, vector_features: Mapping[str, Mapping[str, Any]]) -> bool:
        for metric_name in ("estimated_order_slippage_bps", "estimated_slippage_bps", "spread_bps", "commission_bps"):
            value = _feature_numeric(vector_features, metric_name)
            if value is not None and value > 0:
                return False
        return self.policy.conservative_cost_proxy_bps > 0

    def post_cost_edge_score(self, expected_edge: float, execution_cost_bps: float) -> float:
        cost_score = max(0.0, float(execution_cost_bps or 0.0)) / 10_000.0
        if expected_edge > 0:
            return expected_edge - cost_score
        if expected_edge < 0:
            return expected_edge + cost_score
        return 0.0

    def edge_to_cost_ratio(self, expected_edge: float, execution_cost_bps: float) -> float:
        cost_score = max(0.0, float(execution_cost_bps or 0.0)) / 10_000.0
        if cost_score <= 0:
            return 999.0 if expected_edge != 0 else 0.0
        return abs(float(expected_edge or 0.0)) / cost_score

    def rules_by_metric(self, metric_rules: tuple[MetricWeightRule, ...]) -> dict[str, MetricWeightRule]:
        selected: dict[str, MetricWeightRule] = {}
        for rule in metric_rules:
            current = selected.get(rule.metric_name)
            if current is None or abs(rule.weight) > abs(current.weight):
                selected[rule.metric_name] = rule
        return selected

    def turnover_context(self, portfolio_snapshot: PortfolioSnapshot | None) -> dict[str, float | str | bool]:
        if portfolio_snapshot is None or not isinstance(portfolio_snapshot.payload, Mapping):
            return {
                "turnover_mandate_enabled": False,
                "turnover_target_status": "unknown",
                "trade_urgency_score": 0.0,
            }
        payload = portfolio_snapshot.payload
        status = str(payload.get("turnover_target_status") or "unknown")
        progress = clip(_payload_float(payload, "turnover_progress_ratio"))
        remaining = max(0.0, _payload_float(payload, "remaining_turnover_rub_14d") or 0.0)
        target = max(0.0, _payload_float(payload, "target_gross_turnover_rub_14d") or 0.0)
        if status == "achieved" or target <= 0:
            urgency = 0.0
        elif status == "critically_behind":
            urgency = 1.0
        elif status == "behind":
            urgency = max(0.35, min(0.85, 1.0 - progress))
        else:
            urgency = max(0.0, min(0.35, 1.0 - progress))
        return {
            "turnover_mandate_enabled": bool(payload.get("turnover_mandate_enabled", True)),
            "turnover_target_status": status,
            "turnover_progress_ratio": progress,
            "remaining_turnover_rub_14d": remaining,
            "target_gross_turnover_rub_14d": target,
            "trade_urgency_score": urgency,
        }

    def global_block_reasons(
        self,
        *,
        request: DecisionRequest,
        weights_profile: WeightsProfile | None,
        portfolio_snapshot: PortfolioSnapshot | None,
        market_state: MarketStateRecord | None,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if weights_profile is None or weights_profile.status != "active":
            reasons.append("weights_profile_not_active")
        else:
            if weights_profile.horizon != request.horizon:
                reasons.append("weights_profile_horizon_mismatch")
            if request.run_mode not in weights_profile.run_mode_allowed:
                reasons.append("run_mode_not_allowed_by_weights_profile")
        if portfolio_snapshot is None:
            reasons.append("portfolio_snapshot_missing")
        else:
            age = (parse_utc_iso(request.as_of_ts) - parse_utc_iso(portfolio_snapshot.as_of_ts)).total_seconds()
            if age > self.policy.portfolio_stale_seconds:
                reasons.append("portfolio_state_stale")
        runtime_market_status = current_market_session().market_session_status
        if market_state is not None:
            age = (parse_utc_iso(request.as_of_ts) - parse_utc_iso(market_state.as_of_ts)).total_seconds()
            if age > self.policy.market_state_stale_seconds:
                reasons.append("market_state_stale")
            market_status = str(market_state.market_session_status or "").strip().lower()
            if market_status == "unknown" and runtime_market_status != "unknown":
                market_status = runtime_market_status
            if market_status and market_status != "open":
                reasons.append(f"market_session_status:{market_status}")
            if market_state.market_regime in {"risk_off", "stress"}:
                reasons.append(f"market_regime:{market_state.market_regime}")
        elif runtime_market_status == "unknown":
            reasons.append("market_session_status:unknown")
        elif runtime_market_status != "open":
            reasons.append(f"market_session_status:{runtime_market_status}")
        return tuple(dict.fromkeys(reasons))

    def freshness_multiplier(
        self,
        metric_name: str,
        ttl_status: str,
        rule: MetricWeightRule,
    ) -> float | None:
        del metric_name
        if ttl_status == "fresh":
            return 1.0
        if ttl_status == "stale":
            if rule.stale_policy == "block_decision":
                return None
            if rule.stale_policy == "downweight":
                return 0.5
            return 1.0
        if ttl_status == "expired":
            if rule.stale_policy == "block_decision":
                return None
            if rule.stale_policy == "downweight":
                return 0.1
            return 1.0
        return None

    def risk_components(
        self,
        *,
        vector_features: Mapping[str, Mapping[str, Any]],
        data_quality_score: float,
        portfolio_snapshot: PortfolioSnapshot | None,
    ) -> dict[str, float]:
        values = {
            "volatility_risk_score": _feature_risk_score(vector_features, "volatility_risk_score"),
            "liquidity_risk_score": _feature_risk_score(vector_features, "liquidity_risk_score"),
            "portfolio_concentration_risk": portfolio_concentration_risk(
                portfolio_snapshot.gross_exposure if portfolio_snapshot else None,
                portfolio_snapshot.equity if portfolio_snapshot else None,
            ),
            "data_quality_penalty": data_quality_penalty(data_quality_score),
        }
        return {key: clip(value) for key, value in values.items() if value is not None}

    def macro_regime_overlay(
        self,
        vector_features: Mapping[str, Mapping[str, Any]],
        market_state: MarketStateRecord | None = None,
    ) -> dict[str, Any]:
        """Conservative market-wide overlay built from Market Context features.

        This is not a trading decision by itself; it adjusts the weighted alpha
        score and risk budget when the same feature store reports weak breadth,
        low risk-on score, high volatility or a stressed regime. Strong
        instrument-level evidence can still pass the normal risk gates.
        """
        edge_penalty = 0.0
        risk_penalty = 0.0
        edge_multiplier = 1.0
        reason_codes: list[str] = []

        market_context = _payload_mapping(market_state.payload, "market_context") if market_state is not None else {}
        macro_context = _payload_mapping(market_state.payload, "macro_context") if market_state is not None else {}
        market_regime = _feature_regime_text(vector_features, "market_regime")
        if market_regime is None and market_state is not None and market_state.market_regime:
            market_regime = str(market_state.market_regime).strip().lower()
        if market_regime in {"stress", "risk_off", "halt"}:
            edge_penalty += 0.10
            risk_penalty += 0.20
            edge_multiplier = min(edge_multiplier, 0.50)
            reason_codes.append(f"macro_regime_overlay:{market_regime}")
        elif market_regime in {"range", "unknown"}:
            edge_multiplier = min(edge_multiplier, self.policy.macro_range_edge_multiplier)
            reason_codes.append(f"macro_regime_overlay:{market_regime}")

        risk_on_score = _feature_numeric(vector_features, "risk_on_risk_off_score", default=None)
        if risk_on_score is None and market_state is not None:
            risk_on_score = _payload_float(market_state.payload, "risk_on_risk_off_score")
        if risk_on_score is not None and risk_on_score < self.policy.macro_risk_on_low_threshold:
            weakness = self.policy.macro_risk_on_low_threshold - clip(risk_on_score)
            edge_penalty += weakness * 0.45
            risk_penalty += weakness * 0.60
            edge_multiplier = min(edge_multiplier, 0.75)
            reason_codes.append("risk_on_score_low")

        market_breadth = _feature_numeric(vector_features, "market_breadth", default=None)
        if market_breadth is None:
            market_breadth = _payload_float(market_context, "market_breadth")
        if market_breadth is not None and market_breadth < self.policy.macro_breadth_weak_threshold:
            weakness = self.policy.macro_breadth_weak_threshold - clip(market_breadth)
            edge_penalty += weakness * 0.30
            risk_penalty += weakness * 0.35
            edge_multiplier = min(edge_multiplier, 0.75)
            reason_codes.append("market_breadth_weak")

        volatility_regime = _feature_numeric(vector_features, "index_volatility_regime", default=None)
        if volatility_regime is None:
            volatility_regime = _feature_numeric(vector_features, "volatility_regime", default=None)
        if volatility_regime is None:
            volatility_regime = _payload_float(market_context, "index_volatility_percentile")
        if volatility_regime is not None and volatility_regime > 0.70:
            stress = clip(volatility_regime) - 0.70
            edge_penalty += stress * 0.15
            risk_penalty += stress * 0.35
            reason_codes.append("market_volatility_high")

        if macro_context and all(_payload_float(macro_context, key) is None for key in ("key_rate_level", "ofz_10y_yield", "currency_return_z", "oil_return_z")):
            edge_penalty += 0.01
            risk_penalty += 0.03
            edge_multiplier = min(edge_multiplier, self.policy.macro_degraded_edge_multiplier)
            reason_codes.append("macro_context_degraded")

        return {
            "edge_penalty": clip(edge_penalty, 0.0, 0.25),
            "risk_penalty": clip(risk_penalty, 0.0, 0.35),
            "edge_multiplier": clip(edge_multiplier, 0.35, 1.0),
            "reason_codes": tuple(dict.fromkeys(reason_codes)),
        }

    def latest_price(
        self,
        vector_features: Mapping[str, Mapping[str, Any]],
        position: PositionState | None,
    ) -> float | None:
        for metric_name in ("latest_price", "market_price", "close_price", "last_price", "price"):
            payload = vector_features.get(metric_name)
            if not isinstance(payload, Mapping):
                continue
            value = None
            for key in ("raw_value", "value", "price", "normalized_value"):
                value = _payload_float(payload, key)
                if value is not None:
                    break
            if value is not None and value > 0:
                return value
        if position is not None:
            return position.market_price or position.average_price
        return None

    def portfolio_max_position_pct(self, portfolio_snapshot: PortfolioSnapshot | None) -> float:
        if portfolio_snapshot is None or not isinstance(portfolio_snapshot.payload, Mapping):
            return self.policy.max_position_pct
        limits = portfolio_snapshot.payload.get("portfolio_limits")
        limit_payload = limits if isinstance(limits, Mapping) else {}
        for candidate in (
            _payload_float(limit_payload, "max_position_pct"),
            _payload_float(portfolio_snapshot.payload, "max_position_pct"),
            _payload_float(limit_payload, "max_exposure_pct"),
            _payload_float(portfolio_snapshot.payload, "max_exposure_pct"),
        ):
            if candidate is not None and candidate > 0:
                return clip(candidate, 0.0, 1.0)
        return self.policy.max_position_pct

    def choose_action(
        self,
        *,
        request: DecisionRequest,
        expected_edge: float,
        risk_score: float,
        margin: float,
        reason_codes: tuple[str, ...],
        position: PositionState | None,
        gross_expected_edge: float | None = None,
        expected_edge_after_cost: float | None = None,
    ) -> str:
        blocking_reasons = self.blocking_reason_codes(reason_codes)
        if blocking_reasons:
            return "block"
        if request.decision_mode == "manual_approval_required":
            return "block"
        if risk_score >= self.policy.risk_block_threshold:
            return "block"
        current_quantity = float(position.quantity) if position else 0.0
        gross_edge = float(gross_expected_edge if gross_expected_edge is not None else expected_edge)
        action_edge = float(expected_edge_after_cost if expected_edge_after_cost is not None else expected_edge)
        long_threshold = max(abs(self.policy.action_threshold), abs(self.policy.min_post_cost_edge_score))
        short_threshold = abs(self.policy.short_entry_threshold)
        if request.decision_mode == "risk_off":
            return "reduce" if current_quantity != 0 else "hold"
        if request.decision_mode == "reduce_only":
            if action_edge < -self.policy.action_threshold and current_quantity > 0:
                return "reduce"
            if action_edge > self.policy.short_exit_edge_threshold and current_quantity < 0:
                return "reduce"
            return "hold"
        if current_quantity > 0:
            if action_edge <= 0:
                return "sell"
            if margin <= 0:
                return "hold"
            return "buy" if action_edge >= long_threshold and risk_score < self.policy.risk_reduce_threshold else "hold"
        if current_quantity < 0:
            if action_edge >= self.policy.short_exit_edge_threshold:
                return "reduce"
            short_add_threshold = max(short_threshold, abs(self.policy.short_add_threshold))
            return "sell" if action_edge <= -short_add_threshold and risk_score < self.policy.risk_reduce_threshold else "hold"
        if action_edge >= long_threshold:
            return "buy" if risk_score < self.policy.risk_reduce_threshold else "hold"
        if gross_edge < 0 and action_edge <= -short_threshold:
            return "sell" if self.policy.allow_short_selling and risk_score < self.policy.risk_reduce_threshold else "hold"
        return "hold"

    def position_exit_overlay(
        self,
        *,
        position: PositionState | None,
        latest_price: float | None,
        current_quantity: float,
        expected_edge: float,
        risk_score: float,
        current_pct: float = 0.0,
        expected_edge_after_cost: float | None = None,
    ) -> tuple[str, float, float, tuple[str, ...]] | None:
        if not self.policy.exit_overlay_enabled or position is None or current_quantity == 0:
            return None
        price = float(latest_price or position.market_price or 0.0)
        average_price = float(position.average_price or 0.0)
        if price <= 0 or average_price <= 0:
            return None
        signed_pnl_pct = (price - average_price) / average_price
        if current_quantity < 0:
            signed_pnl_pct = (average_price - price) / average_price
        decision_edge = expected_edge_after_cost if self.policy.exit_use_post_cost_edge and expected_edge_after_cost is not None else expected_edge
        partial_ratio = clip(self.policy.partial_take_profit_ratio, 0.0, 1.0)
        short_partial_ratio = clip(self.policy.short_partial_take_profit_ratio, 0.0, 1.0)

        stop_loss_pct = abs(self.policy.short_stop_loss_pct if current_quantity < 0 else self.policy.stop_loss_pct)
        if signed_pnl_pct <= -stop_loss_pct:
            return (
                "reduce",
                0.0,
                0.0,
                (
                    "position_stop_loss_triggered",
                    f"position_unrealized_pnl_pct:{signed_pnl_pct:.6f}",
                ),
            )
        take_profit_pct = abs(self.policy.short_take_profit_pct if current_quantity < 0 else self.policy.take_profit_pct)
        if current_quantity > 0 and decision_edge <= 0 and signed_pnl_pct < take_profit_pct:
            return (
                "reduce",
                0.0,
                0.0,
                (
                    "long_post_cost_edge_non_positive",
                    f"position_unrealized_pnl_pct:{signed_pnl_pct:.6f}",
                ),
            )
        if current_quantity < 0 and decision_edge >= self.policy.short_exit_edge_threshold and signed_pnl_pct < take_profit_pct:
            return (
                "reduce",
                0.0,
                0.0,
                (
                    "short_post_cost_edge_non_negative",
                    f"position_unrealized_pnl_pct:{signed_pnl_pct:.6f}",
                ),
            )
        if signed_pnl_pct >= take_profit_pct:
            hold_threshold = abs(self.policy.exit_edge_hold_threshold or self.policy.take_profit_hold_edge_threshold)
            long_weak_edge = current_quantity > 0 and decision_edge < hold_threshold
            short_weak_edge = current_quantity < 0 and decision_edge > -hold_threshold
            if long_weak_edge or short_weak_edge:
                ratio = short_partial_ratio if current_quantity < 0 else partial_ratio
                partial_enabled = self.policy.partial_take_profit_enabled
                if current_quantity < 0:
                    partial_enabled = self.policy.short_profit_lock_enabled and ratio < 1.0
                if partial_enabled and 0.0 < ratio < 1.0:
                    target_quantity = current_quantity * (1.0 - ratio)
                    target_pct = current_pct * (1.0 - ratio)
                    return (
                        "reduce",
                        target_pct,
                        target_quantity,
                        (
                            "position_take_profit_triggered",
                            "position_partial_take_profit_triggered",
                            f"position_unrealized_pnl_pct:{signed_pnl_pct:.6f}",
                        ),
                    )
                return (
                    "reduce",
                    0.0,
                    0.0,
                    (
                        "position_take_profit_triggered",
                        f"position_unrealized_pnl_pct:{signed_pnl_pct:.6f}",
                    ),
                )
            if current_quantity > 0 and self.policy.profit_lock_enabled:
                return None
            if current_quantity < 0 and self.policy.short_profit_lock_enabled:
                return None
        if risk_score >= self.policy.risk_reduce_threshold and current_quantity != 0:
            return (
                "reduce",
                0.0,
                0.0,
                (
                    "position_risk_reduce_triggered",
                    f"position_unrealized_pnl_pct:{signed_pnl_pct:.6f}",
                ),
            )
        return None

    def position_effect_for_action(
        self,
        *,
        action: str,
        current_quantity: float,
        target_quantity: float,
    ) -> str:
        current = float(current_quantity or 0.0)
        target = float(target_quantity or 0.0)
        if action == "buy":
            if current < 0:
                return "close_short" if target >= 0 else "reduce_short"
            return "increase_long" if current > 0 else "open_long"
        if action == "sell":
            if current > 0:
                return "close_long" if target <= 0 else "reduce_long"
            return "increase_short" if current < 0 else "open_short"
        if action in {"reduce", "close"}:
            if current < 0:
                return "close_short" if target >= 0 else "reduce_short"
            if current > 0:
                return "close_long" if target <= 0 else "reduce_long"
        return "none"

    def blocking_reason_codes(self, reason_codes: tuple[str, ...]) -> tuple[str, ...]:
        """Return only hard-block reasons.

        ``primary_reason_codes`` intentionally include explanatory codes such as
        ``turnover_mandate_urgency``. Earlier versions treated any reason code as
        a block, which made autonomous turnover-aware decisions self-blocking.
        """
        hard_prefixes = (
            "feature_vector_missing",
            "feature_coverage_low",
            "feature_confidence_below_rule",
            "expired_feature_blocked",
            "market_session_status:",
            "market_regime:risk_off",
            "market_regime:stress",
            "market_regime:halt",
        )
        hard_codes = {
            "weights_profile_not_active",
            "weights_profile_horizon_mismatch",
            "run_mode_not_allowed_by_weights_profile",
            "portfolio_snapshot_missing",
            "portfolio_state_stale",
            "market_state_stale",
            "expired_features_not_allowed",
            "manual_approval_required",
        }
        return tuple(
            code
            for code in reason_codes
            if code in hard_codes or any(code.startswith(prefix) for prefix in hard_prefixes)
        )

    def build_audit_record(
        self,
        job: ModuleJob,
        request: DecisionRequest,
        decision_set: DecisionSetRecord,
        warnings: tuple[str, ...],
    ) -> AuditRecord:
        severity = "warning" if warnings else "info"
        return AuditRecord(
            audit_record_id=stable_record_id(
                "audit_decision_engine",
                {
                    "job_id": job.job_id,
                    "decision_set_id": decision_set.decision_set_id,
                    "calculation_version": decision_set.calculation_version,
                },
            ),
            module_name=self.module_name,
            job_id=job.job_id,
            severity=severity,
            event_type="decision_set_written",
            message="Decision Engine wrote decision_set without broker/order side effects",
            object_type="decision_set",
            object_ref=f"decisions.decision_set:{decision_set.decision_set_id}",
            reason_codes=warnings,
            payload={
                "decision_request_id": request.decision_request_id,
                "run_mode": request.run_mode,
                "decision_mode": request.decision_mode,
                "no_broker_calls": True,
                "order_intents_written": 0,
            },
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
        data_quality_score: float,
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
            data_quality_score=data_quality_score,
        )

    def _failed_result(self, job: ModuleJob, started_at: str, error: Exception) -> DecisionEngineExecutionResult:
        audit_ref: tuple[str, ...] = ()
        try:
            audit_record = AuditRecord(
                audit_record_id=stable_record_id(
                    "audit_decision_engine_failed",
                    {"job_id": job.job_id, "error": str(error), "calculation_version": CALCULATION_VERSION},
                ),
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="decision_engine_failed",
                message=str(error),
                object_type="module_job",
                object_ref=f"audit.module_job:{job.job_id}",
                reason_codes=("decision_engine_failed",),
                payload={"calculation_version": CALCULATION_VERSION},
            )
            audit_ref = (self.repository.save_audit_record(audit_record),)
        except Exception:
            audit_ref = ()
        return DecisionEngineExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=audit_ref,
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
                data_quality_score=0.0,
            ),
            decision_set=None,
            decision_records=(),
            decision_explanations=(),
            audit_refs=audit_ref,
        )


def _feature_value(features: Mapping[str, Mapping[str, Any]], metric_name: str) -> float | None:
    return _feature_numeric(features, metric_name, default=None, value_field="normalized_value")


def _feature_risk_score(features: Mapping[str, Mapping[str, Any]], metric_name: str) -> float | None:
    return _feature_numeric(features, metric_name, default=None, value_field="raw_value")


def _feature_numeric(
    features: Any,
    metric_name: str,
    default: float | None = 0.0,
    *,
    value_field: str = "auto",
) -> float | None:
    """Read a finite numeric feature value from dict or object-like payloads."""
    payload = _feature_payload(features, metric_name)
    if payload is None:
        return default
    if value_field == "auto":
        field_names = ("normalized_value", "raw_value")
    elif value_field in {"normalized_value", "raw_value"}:
        field_names = (value_field,)
    else:
        field_names = ("normalized_value", "raw_value")
    for field_name in field_names:
        parsed = _finite_float(_field_value(payload, field_name))
        if parsed is not None:
            return parsed
    return default


def _feature_payload(features: Any, metric_name: str) -> Any:
    feature_map = getattr(features, "features", features)
    if isinstance(feature_map, Mapping):
        return feature_map.get(metric_name)
    return getattr(feature_map, metric_name, None)


def _feature_regime_text(features: Mapping[str, Mapping[str, Any]], metric_name: str) -> str | None:
    payload = _feature_payload(features, metric_name)
    if payload is None:
        return None
    candidates = (
        _field_value(payload, "regime"),
        _field_value(payload, "raw_value"),
        _field_value(payload, "value"),
        _field_value(payload, "status"),
    )
    nested_payload = _field_value(payload, "payload")
    if isinstance(nested_payload, Mapping):
        candidates = (
            _field_value(nested_payload, "regime"),
            _field_value(nested_payload, "market_regime"),
            *candidates,
        )
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip().lower()
        if text and not _looks_numeric(text):
            return text
    return None


def _field_value(payload: Any, key: str) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _finite_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _payload_float(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _payload_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key) if isinstance(payload, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _mean(values: Any) -> float:
    parsed = [float(value) for value in values if value is not None]
    if not parsed:
        return 0.0
    return clip(sum(parsed) / len(parsed))


def _decision_record_to_dict(record: DecisionRecord) -> dict[str, Any]:
    return {
        "instrument_id": record.instrument_id,
        "action": record.action if record.action in VALID_ACTIONS else "block",
        "target_position_pct": record.target_position_pct,
        "target_quantity": record.target_quantity,
        "confidence_score": record.confidence_score,
        "expected_edge_score": record.expected_edge_score,
        "risk_score": record.risk_score,
        "primary_reason_codes": list(record.primary_reason_codes),
        "feature_contributions": dict(record.feature_contributions),
    }


def _decision_explanation_to_dict(explanation: DecisionExplanation) -> dict[str, Any]:
    return {
        "decision_explanation_id": explanation.decision_explanation_id,
        "decision_record_id": explanation.decision_record_id,
        "explanation": dict(explanation.explanation),
    }
