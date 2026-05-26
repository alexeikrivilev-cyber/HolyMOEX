from __future__ import annotations

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
from agent_app.runtime_calendar import current_market_session

from .metrics import (
    daily_loss_usage,
    drawdown_usage,
    floor_quantity,
    instrument_exposure_after_trade,
    liquidity_limit_usage,
    portfolio_exposure_after_trade,
    risk_budget_usage,
    slippage_limit_usage,
)
from .repository import (
    AuditRecord,
    DecisionSet,
    FeatureVector,
    InMemoryRiskControlRepository,
    InstrumentLimit,
    OrderIntentRecord,
    PortfolioLimit,
    PortfolioSnapshot,
    PositionState,
    RiskCheckResultRecord,
    RiskControlRepository,
    RiskEventRecord,
    RiskPolicy,
    stable_record_id,
)


MODULE_NAME = "Risk Control Module"
CALCULATION_VERSION = "risk_control_v1"
VALID_CONTOURS = {"decision_contour", "execution_contour"}
VALID_RUN_MODES = {"analysis_only", "paper_trading", "live_trading"}
VALID_RISK_STATUSES = {"approved", "approved_with_changes", "rejected", "manual_review_required"}
INPUT_FIELDS = {
    "decision_set_id",
    "portfolio_state_ref",
    "risk_policy_id",
    "market_state_ref",
    "data_quality_report_ref",
    "run_mode",
}
ORDER_ACTIONS = {"buy", "sell", "reduce", "close"}
DEFAULT_PORTFOLIO_STALE_SECONDS = 300
DEFAULT_ORDER_TTL_SECONDS = 300
DEFAULT_TIME_IN_FORCE = "day"
DEFAULT_ORDER_TYPE = "limit"
DEFAULT_MAX_RISK_INCREASING_ORDERS_PER_CYCLE = 4
DEFAULT_MAX_NEW_LONG_ORDERS_PER_CYCLE = 3
DEFAULT_MAX_NEW_SHORT_ORDERS_PER_CYCLE = 3


class RiskControlError(ValueError):
    """Raised when module 18 would violate its documented contract."""


@dataclass(frozen=True)
class RiskCheckRequest:
    decision_set_id: str
    portfolio_state_ref: str
    risk_policy_id: str
    market_state_ref: str
    data_quality_report_ref: str
    run_mode: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "RiskCheckRequest":
        extra_top_level = sorted(set(payload) - {"risk_check_request"})
        if extra_top_level:
            raise RiskControlError(f"payload has undocumented fields: {extra_top_level}")
        request_payload = payload.get("risk_check_request")
        if not isinstance(request_payload, Mapping):
            raise RiskControlError("payload must contain risk_check_request")
        missing_fields = sorted(INPUT_FIELDS - set(request_payload))
        if missing_fields:
            raise RiskControlError(f"risk_check_request missing required fields: {missing_fields}")
        extra_fields = sorted(set(request_payload) - INPUT_FIELDS)
        if extra_fields:
            raise RiskControlError(f"risk_check_request has undocumented fields: {extra_fields}")

        run_mode = str(request_payload.get("run_mode") or "")
        if run_mode not in VALID_RUN_MODES:
            raise RiskControlError("risk_check_request.run_mode is invalid")
        if run_mode != job.run_mode:
            raise RiskControlError("risk_check_request.run_mode must match module_job.run_mode")

        required_text = {
            "decision_set_id": request_payload.get("decision_set_id"),
            "portfolio_state_ref": request_payload.get("portfolio_state_ref"),
            "risk_policy_id": request_payload.get("risk_policy_id"),
            "market_state_ref": request_payload.get("market_state_ref"),
            "data_quality_report_ref": request_payload.get("data_quality_report_ref"),
        }
        missing_text = [name for name, value in required_text.items() if not str(value or "")]
        if missing_text:
            raise RiskControlError(f"risk_check_request missing text fields: {missing_text}")

        decision_set_id = str(request_payload.get("decision_set_id"))
        if tuple(job.input_refs):
            ref_tails = {ref.rsplit(":", 1)[-1] for ref in job.input_refs}
            required_refs = {
                "decision_set_id": decision_set_id,
                "portfolio_state_ref": str(request_payload.get("portfolio_state_ref")),
                "risk_policy_id": str(request_payload.get("risk_policy_id")),
                "market_state_ref": str(request_payload.get("market_state_ref")),
                "data_quality_report_ref": str(request_payload.get("data_quality_report_ref")),
            }
            missing_refs = [
                name
                for name, value in required_refs.items()
                if value.rsplit(":", 1)[-1] not in ref_tails
            ]
            if missing_refs:
                raise RiskControlError(f"risk_check_request refs must be present in module_job.input_refs: {missing_refs}")

        return cls(
            decision_set_id=decision_set_id,
            portfolio_state_ref=str(request_payload.get("portfolio_state_ref")),
            risk_policy_id=str(request_payload.get("risk_policy_id")),
            market_state_ref=str(request_payload.get("market_state_ref")),
            data_quality_report_ref=str(request_payload.get("data_quality_report_ref")),
            run_mode=run_mode,
        )


@dataclass(frozen=True)
class RiskDecisionAssessment:
    instrument_id: str
    action: str
    status: str
    flags: tuple[str, ...]
    metrics: Mapping[str, float]
    adjustments: tuple[Mapping[str, Any], ...]
    rejected_decision_ref: str | None = None
    order_intent: OrderIntentRecord | None = None


@dataclass(frozen=True)
class RiskControlExecutionResult:
    module_job_result: ModuleJobResult
    risk_check_result: RiskCheckResultRecord | None
    order_intents: tuple[OrderIntentRecord, ...]
    risk_events: tuple[RiskEventRecord, ...]
    risk_check_result_ref: str | None = None
    order_intent_refs: tuple[str, ...] = ()
    risk_event_refs: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "risk_check_result": self.risk_check_result.to_contract() if self.risk_check_result else None,
            "order_intents": [item.to_contract() for item in self.order_intents],
            "risk_events": [dict(item.payload) for item in self.risk_events],
            "risk_check_result_ref": self.risk_check_result_ref,
            "order_intent_refs": list(self.order_intent_refs),
            "risk_event_refs": list(self.risk_event_refs),
            "audit_refs": list(self.audit_refs),
        }


class RiskControlService:
    module_name = MODULE_NAME

    def __init__(self, repository: RiskControlRepository | None = None) -> None:
        self.repository = repository or InMemoryRiskControlRepository()

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> RiskControlExecutionResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> RiskControlExecutionResult:
        return self.execute(payload, job)

    def execute(self, payload: Mapping[str, Any], job: ModuleJob | None) -> RiskControlExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            audit_refs = self._audit_missing_job()
            result = ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=audit_refs,
                errors=(f"{self.module_name} requires module_job",),
            )
            return RiskControlExecutionResult(result, None, (), (), audit_refs=audit_refs)
        try:
            self.validate_module_job(job)
            request = RiskCheckRequest.from_dict(payload, job)
            as_of_ts = job.time_range.to_ts
            decision_set = self.repository.get_decision_set(request.decision_set_id)
            if decision_set is None:
                risk_check, risk_event = self.build_missing_decision_set_result(request, job, as_of_ts)
                risk_check_ref = self.repository.save_risk_check_result(risk_check)
                risk_event_ref = self.repository.save_risk_event(risk_event)
                audit_ref = self.repository.save_audit_record(self.build_audit_record(job, request, risk_check, ()))
                output_refs = (risk_check_ref, risk_event_ref, audit_ref)
                return RiskControlExecutionResult(
                    module_job_result=ModuleJobResult(
                        job_id=job.job_id,
                        module_name=self.module_name,
                        status="partial_success",
                        started_at=started_at,
                        finished_at=to_utc_iso(utc_now()),
                        output_refs=output_refs,
                        warnings=risk_check.risk_flags,
                        errors=(),
                        metrics_written=1,
                        events_written=1,
                        data_quality_score=0.0,
                    ),
                    risk_check_result=risk_check,
                    order_intents=(),
                    risk_events=(risk_event,),
                    risk_check_result_ref=risk_check_ref,
                    risk_event_refs=(risk_event_ref,),
                    audit_refs=(audit_ref,),
                )

            instrument_ids = tuple(str(item.get("instrument_id")) for item in decision_set.decisions if item.get("instrument_id"))
            risk_policy = self.repository.get_risk_policy(request.risk_policy_id)
            instrument_limits = self.repository.list_instrument_limits(request.risk_policy_id, instrument_ids)
            portfolio_limits = self.repository.list_portfolio_limits(request.risk_policy_id)
            portfolio_snapshot = self.repository.get_portfolio_snapshot(request.portfolio_state_ref, as_of_ts)
            positions = self.repository.list_position_states(
                portfolio_id=portfolio_snapshot.portfolio_id if portfolio_snapshot else "",
                instrument_ids=instrument_ids,
                as_of_ts=as_of_ts,
            ) if portfolio_snapshot else ()
            feature_vectors = self.repository.list_feature_vectors(instrument_ids, decision_set.horizon, as_of_ts)

            risk_check, orders, risk_event = self.build_risk_check(
                request=request,
                job=job,
                decision_set=decision_set,
                risk_policy=risk_policy,
                instrument_limits=instrument_limits,
                portfolio_limits=portfolio_limits,
                portfolio_snapshot=portfolio_snapshot,
                positions=positions,
                feature_vectors=feature_vectors,
                as_of_ts=as_of_ts,
            )

            risk_check_ref = self.repository.save_risk_check_result(risk_check)
            order_refs = tuple(self.repository.save_order_intent(order) for order in orders)
            risk_event_ref = self.repository.save_risk_event(risk_event)
            audit_ref = self.repository.save_audit_record(self.build_audit_record(job, request, risk_check, orders))
            output_refs = (risk_check_ref, *order_refs, risk_event_ref, audit_ref)
            module_status = "success" if not risk_check.risk_flags else "partial_success"
            return RiskControlExecutionResult(
                module_job_result=ModuleJobResult(
                    job_id=job.job_id,
                    module_name=self.module_name,
                    status=module_status,
                    started_at=started_at,
                    finished_at=to_utc_iso(utc_now()),
                    output_refs=output_refs,
                    warnings=risk_check.risk_flags,
                    errors=(),
                    metrics_written=1,
                    events_written=1,
                    data_quality_score=_payload_float(risk_check.payload, "data_quality_score"),
                ),
                risk_check_result=risk_check,
                order_intents=orders,
                risk_events=(risk_event,),
                risk_check_result_ref=risk_check_ref,
                order_intent_refs=order_refs,
                risk_event_refs=(risk_event_ref,),
                audit_refs=(audit_ref,),
            )
        except (RiskControlError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise RiskControlError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise RiskControlError("module_job.module_name must be Risk Control Module")
        if job.contour not in VALID_CONTOURS:
            raise RiskControlError("module_job.contour is not valid for Risk Control Module")
        if not job.input_refs:
            raise RiskControlError("module_job.input_refs is required")
        if job.run_mode not in VALID_RUN_MODES:
            raise RiskControlError("module_job.run_mode must be analysis_only, paper_trading, or live_trading")
        if not job.config_ref:
            raise RiskControlError("module_job.config_ref is required")

    def build_risk_check(
        self,
        *,
        request: RiskCheckRequest,
        job: ModuleJob,
        decision_set: DecisionSet,
        risk_policy: RiskPolicy | None,
        instrument_limits: tuple[InstrumentLimit, ...],
        portfolio_limits: tuple[PortfolioLimit, ...],
        portfolio_snapshot: PortfolioSnapshot | None,
        positions: tuple[PositionState, ...],
        feature_vectors: tuple[FeatureVector, ...],
        as_of_ts: str,
    ) -> tuple[RiskCheckResultRecord, tuple[OrderIntentRecord, ...], RiskEventRecord]:
        risk_check_id = stable_record_id(
            "risk_check",
            {
                "decision_set_id": decision_set.decision_set_id,
                "risk_policy_id": request.risk_policy_id,
                "portfolio_state_ref": request.portfolio_state_ref,
                "run_mode": request.run_mode,
                "config_ref": job.config_ref,
            },
        )
        global_flags, global_status = self.global_risk_flags(
            request=request,
            risk_policy=risk_policy,
            portfolio_snapshot=portfolio_snapshot,
            portfolio_limits=portfolio_limits,
            as_of_ts=as_of_ts,
        )
        rules = risk_policy.rules if risk_policy else {}
        limits_by_instrument = {item.instrument_id: item for item in instrument_limits}
        positions_by_instrument = {item.instrument_id: item for item in positions}
        features_by_instrument = {item.instrument_id: item for item in feature_vectors}

        assessments: list[RiskDecisionAssessment] = []
        if not decision_set.decisions:
            global_flags = tuple(dict.fromkeys((*global_flags, "decision_set_empty")))
            global_status = "manual_review_required" if global_status == "approved" else global_status
        if global_status in {"rejected", "manual_review_required"}:
            for decision in decision_set.decisions:
                instrument_id = str(decision.get("instrument_id") or "")
                assessments.append(
                    RiskDecisionAssessment(
                        instrument_id=instrument_id,
                        action=str(decision.get("action") or ""),
                        status=global_status,
                        flags=global_flags,
                        metrics={},
                        adjustments=(),
                        rejected_decision_ref=self.decision_ref(decision_set.decision_set_id, instrument_id),
                    )
                )
        else:
            existing_order_count = self.daily_submitted_order_count(portfolio_snapshot)
            shadow_snapshot = portfolio_snapshot
            cycle_counters = {
                "risk_increasing": 0,
                "new_long": 0,
                "new_short": 0,
            }
            for decision in self.prioritized_decisions(decision_set.decisions, positions_by_instrument):
                instrument_id = str(decision.get("instrument_id") or "")
                position_before_order = positions_by_instrument.get(instrument_id)
                assessment = self.assess_decision(
                    request=request,
                    job=job,
                    decision_set=decision_set,
                    decision=decision,
                    risk_check_id=risk_check_id,
                    risk_policy=risk_policy,
                    portfolio_limits=portfolio_limits,
                    instrument_limit=limits_by_instrument.get(instrument_id),
                    portfolio_snapshot=shadow_snapshot,
                    position=positions_by_instrument.get(instrument_id),
                    feature_vector=features_by_instrument.get(instrument_id),
                    as_of_ts=as_of_ts,
                    existing_order_count=existing_order_count,
                    pending_order_count=sum(1 for item in assessments if item.order_intent is not None),
                )
                assessment = self.apply_cycle_order_limits(
                    assessment=assessment,
                    decision_set=decision_set,
                    decision=decision,
                    position=position_before_order,
                    risk_policy=risk_policy,
                    portfolio_limits=portfolio_limits,
                    cycle_counters=cycle_counters,
                )
                assessments.append(assessment)
                if assessment.order_intent is not None and shadow_snapshot is not None:
                    self.increment_cycle_order_counters(assessment, position_before_order, cycle_counters)
                    shadow_snapshot, positions_by_instrument = self.apply_approved_order_to_shadow_state(
                        shadow_snapshot,
                        positions_by_instrument,
                        assessment.order_intent,
                        assessment.metrics,
                    )

        all_flags = tuple(dict.fromkeys((*global_flags, *(flag for item in assessments for flag in item.flags))))
        adjustments = tuple(item for assessment in assessments for item in assessment.adjustments)
        orders = tuple(item.order_intent for item in assessments if item.order_intent is not None)
        order_refs = tuple(f"orders.order_intent:{item.order_intent_id}" for item in orders)
        rejected_decisions = tuple(
            item.rejected_decision_ref
            for item in assessments
            if item.rejected_decision_ref is not None
        )
        status = self.result_status(assessments, adjustments, orders, all_flags)
        data_quality_score = self.aggregate_data_quality(feature_vectors)
        checked_at = to_utc_iso(utc_now())
        risk_check = RiskCheckResultRecord(
            risk_check_id=risk_check_id,
            decision_set_id=decision_set.decision_set_id,
            status=status,
            approved_order_intents=order_refs,
            rejected_decisions=rejected_decisions,
            risk_flags=all_flags,
            adjustments=adjustments,
            checked_at=checked_at,
            payload={
                "source_module": self.module_name,
                "timestamp": checked_at,
                "calculation_version": f"{CALCULATION_VERSION}:{job.config_ref}",
                "confidence_score": data_quality_score,
                "data_quality_score": data_quality_score,
                "run_mode": request.run_mode,
                "risk_policy_id": request.risk_policy_id,
                "market_state_ref": request.market_state_ref,
                "data_quality_report_ref": request.data_quality_report_ref,
                "metrics": [dict(item.metrics) for item in assessments],
                "risk_rejection_count": len(rejected_decisions),
            },
        )
        risk_event = RiskEventRecord(
            universe_id=job.universe_id,
            instrument_id=None,
            as_of_ts=checked_at,
            source_module=self.module_name,
            calculation_version=f"{CALCULATION_VERSION}:{job.config_ref}",
            payload={
                "object_type": "risk_event",
                "risk_check_id": risk_check_id,
                "decision_set_id": decision_set.decision_set_id,
                "status": status,
                "risk_flags": list(all_flags),
                "approved_order_intents": list(order_refs),
                "rejected_decisions": list(rejected_decisions),
                "source_module": self.module_name,
                "timestamp": checked_at,
                "confidence_score": data_quality_score,
                "calculation_version": f"{CALCULATION_VERSION}:{job.config_ref}",
            },
        )
        return risk_check, orders, risk_event

    def prioritized_decisions(
        self,
        decisions: tuple[Mapping[str, Any], ...],
        positions_by_instrument: Mapping[str, PositionState],
    ) -> tuple[Mapping[str, Any], ...]:
        """Rank actionable decisions before applying batch risk limits.

        The previous batch behavior assessed the universe in registry order, so
        a broad set of small positive signals could all become long orders in
        one cycle.  Risk-reducing actions still go first; risk-increasing
        actions are then ranked by post-cost edge strength.
        """

        def sort_key(decision: Mapping[str, Any]) -> tuple[int, float, str]:
            instrument_id = str(decision.get("instrument_id") or "")
            action = str(decision.get("action") or "hold")
            position = positions_by_instrument.get(instrument_id)
            current_quantity = float(position.quantity) if position else 0.0
            edge = _payload_float(decision, "expected_edge_after_cost_score")
            if edge is None:
                edge = _payload_float(decision, "expected_edge_score") or 0.0
            if action in {"reduce", "close"} or (action == "sell" and current_quantity > 0):
                group = 0
                score = abs(edge)
            elif action == "buy":
                group = 1
                score = max(edge, 0.0)
            elif action == "sell":
                group = 1
                score = abs(min(edge, 0.0))
            else:
                group = 3
                score = 0.0
            return (group, -score, instrument_id)

        return tuple(sorted(decisions, key=sort_key))

    def apply_cycle_order_limits(
        self,
        *,
        assessment: RiskDecisionAssessment,
        decision_set: DecisionSet,
        decision: Mapping[str, Any],
        position: PositionState | None,
        risk_policy: RiskPolicy | None,
        portfolio_limits: tuple[PortfolioLimit, ...],
        cycle_counters: Mapping[str, int],
    ) -> RiskDecisionAssessment:
        order = assessment.order_intent
        if order is None:
            return assessment

        risk_direction = self.order_risk_direction(order, position)
        if risk_direction == "risk_reducing":
            return assessment

        max_risk_increasing = self.cycle_limit_value(
            risk_policy,
            portfolio_limits,
            "max_risk_increasing_order_intents_per_cycle",
            "RISK_MAX_RISK_INCREASING_ORDERS_PER_CYCLE",
            DEFAULT_MAX_RISK_INCREASING_ORDERS_PER_CYCLE,
        )
        max_new_long = self.cycle_limit_value(
            risk_policy,
            portfolio_limits,
            "max_new_long_order_intents_per_cycle",
            "RISK_MAX_NEW_LONG_ORDERS_PER_CYCLE",
            DEFAULT_MAX_NEW_LONG_ORDERS_PER_CYCLE,
        )
        max_new_short = self.cycle_limit_value(
            risk_policy,
            portfolio_limits,
            "max_new_short_order_intents_per_cycle",
            "RISK_MAX_NEW_SHORT_ORDERS_PER_CYCLE",
            DEFAULT_MAX_NEW_SHORT_ORDERS_PER_CYCLE,
        )

        reason: str | None = None
        if int(cycle_counters.get("risk_increasing", 0)) >= max_risk_increasing:
            reason = "risk_increasing_cycle_order_limit_reached"
        elif risk_direction == "new_long" and int(cycle_counters.get("new_long", 0)) >= max_new_long:
            reason = "new_long_cycle_order_limit_reached"
        elif risk_direction == "new_short" and int(cycle_counters.get("new_short", 0)) >= max_new_short:
            reason = "new_short_cycle_order_limit_reached"

        if reason is None:
            return assessment

        metrics = dict(assessment.metrics)
        metrics["cycle_limit_applied"] = 1.0
        metrics["max_risk_increasing_order_intents_per_cycle"] = float(max_risk_increasing)
        metrics["max_new_long_order_intents_per_cycle"] = float(max_new_long)
        metrics["max_new_short_order_intents_per_cycle"] = float(max_new_short)
        flags = tuple(dict.fromkeys((*assessment.flags, reason)))
        return replace(
            assessment,
            status="rejected",
            flags=flags,
            metrics=metrics,
            rejected_decision_ref=self.decision_ref(decision_set.decision_set_id, str(decision.get("instrument_id") or "")),
            order_intent=None,
        )

    def increment_cycle_order_counters(
        self,
        assessment: RiskDecisionAssessment,
        position: PositionState | None,
        cycle_counters: dict[str, int],
    ) -> None:
        order = assessment.order_intent
        if order is None:
            return
        risk_direction = self.order_risk_direction(order, position)
        if risk_direction == "risk_reducing":
            return
        cycle_counters["risk_increasing"] = cycle_counters.get("risk_increasing", 0) + 1
        if risk_direction == "new_long":
            cycle_counters["new_long"] = cycle_counters.get("new_long", 0) + 1
        elif risk_direction == "new_short":
            cycle_counters["new_short"] = cycle_counters.get("new_short", 0) + 1

    def order_risk_direction(self, order: OrderIntentRecord, position: PositionState | None) -> str:
        position_effect = str((order.payload or {}).get("position_effect") or "")
        if position_effect in {"reduce_long", "close_long", "reduce_short", "close_short"}:
            return "risk_reducing"
        if position_effect in {"open_short", "increase_short"}:
            return "new_short"
        if position_effect in {"open_long", "increase_long"}:
            return "new_long"
        current_quantity = float(position.quantity) if position else 0.0
        quantity = float(order.quantity or 0.0)
        if order.side == "buy":
            if current_quantity < 0 and quantity <= abs(current_quantity):
                return "risk_reducing"
            return "new_long"
        if order.side == "sell":
            if current_quantity > 0 and quantity <= current_quantity:
                return "risk_reducing"
            return "new_short"
        return "risk_increasing"

    def order_side_for_action(self, action: str, current_quantity: float) -> str:
        if action == "buy":
            return "buy"
        if action == "sell":
            return "sell"
        if action in {"reduce", "close"}:
            return "buy" if float(current_quantity or 0.0) < 0 else "sell"
        return "sell"

    def position_effect_for_order(
        self,
        *,
        action: str,
        side: str,
        current_quantity: float,
        requested_quantity: float,
    ) -> str:
        current = float(current_quantity or 0.0)
        requested = max(0.0, float(requested_quantity or 0.0))
        if side == "buy":
            if current < 0:
                return "close_short" if requested >= abs(current) else "reduce_short"
            return "increase_long" if current > 0 else "open_long"
        if side == "sell":
            if current > 0:
                return "close_long" if requested >= current else "reduce_long"
            return "increase_short" if current < 0 else "open_short"
        return "unknown"

    def arena_go_shorts_allowed(self, risk_policy: RiskPolicy | None) -> bool:
        if risk_policy is not None:
            for key in ("arena_go_shorts_allowed", "short_selling_supported", "short_selling_enabled"):
                configured = _rule_value(risk_policy.rules, key)
                if configured is not None:
                    return _coerce_bool(configured)
        return _env_bool("ARENA_GO_SHORTS_ALLOWED", False)

    def short_selling_allowed(self, risk_policy: RiskPolicy | None) -> bool:
        decision_allowed = _env_bool("DECISION_ALLOW_SHORT_SELLING", True)
        if risk_policy is not None:
            configured = _rule_value(risk_policy.rules, "decision_allow_short_selling")
            if configured is not None:
                decision_allowed = _coerce_bool(configured)
        return bool(decision_allowed and self.arena_go_shorts_allowed(risk_policy))

    def short_limit_value(
        self,
        portfolio_limits: tuple[PortfolioLimit, ...],
        risk_policy: RiskPolicy | None,
        name: str,
        env_name: str,
    ) -> float | None:
        configured = self.portfolio_limit_value(portfolio_limits, risk_policy, name)
        if configured is not None:
            return configured
        raw = os.getenv(env_name)
        if raw not in (None, ""):
            try:
                return float(raw)
            except ValueError:
                return None
        defaults = {
            "max_short_position_pct": 0.05,
            "max_total_short_exposure_pct": 0.20,
            "max_single_short_order_value_rub": 75_000.0,
        }
        return defaults.get(name)

    def projected_short_exposure_rub(
        self,
        *,
        current_value: float,
        signed_trade_value: float,
        portfolio_snapshot: PortfolioSnapshot,
    ) -> float:
        current_short = abs(float(current_value or 0.0)) if current_value < 0 else 0.0
        projected_instrument_value = float(current_value or 0.0) + float(signed_trade_value or 0.0)
        projected_short = abs(projected_instrument_value) if projected_instrument_value < 0 else 0.0
        existing_short = _payload_float(portfolio_snapshot.payload, "current_short_exposure_rub")
        if existing_short is None:
            existing_short = _payload_float(portfolio_snapshot.payload, "short_exposure_rub")
        existing_short = max(0.0, float(existing_short or 0.0))
        return max(0.0, existing_short - current_short + projected_short)

    def cycle_limit_value(
        self,
        risk_policy: RiskPolicy | None,
        portfolio_limits: tuple[PortfolioLimit, ...],
        limit_name: str,
        env_name: str,
        default: int,
    ) -> int:
        configured = self.portfolio_limit_value(portfolio_limits, risk_policy, limit_name)
        if configured is None:
            env_value = os.getenv(env_name)
            if env_value not in (None, ""):
                try:
                    configured = float(env_value)
                except ValueError:
                    configured = None
        if configured is None:
            configured = default
        return max(0, int(configured))

    def build_missing_decision_set_result(
        self,
        request: RiskCheckRequest,
        job: ModuleJob,
        as_of_ts: str,
    ) -> tuple[RiskCheckResultRecord, RiskEventRecord]:
        risk_check_id = stable_record_id(
            "risk_check",
            {
                "decision_set_id": request.decision_set_id,
                "risk_policy_id": request.risk_policy_id,
                "portfolio_state_ref": request.portfolio_state_ref,
                "run_mode": request.run_mode,
                "config_ref": job.config_ref,
                "missing": "decision_set",
            },
        )
        checked_at = to_utc_iso(utc_now())
        risk_check = RiskCheckResultRecord(
            risk_check_id=risk_check_id,
            decision_set_id=request.decision_set_id.rsplit(":", 1)[-1],
            status="manual_review_required",
            approved_order_intents=(),
            rejected_decisions=(f"decisions.decision_set:{request.decision_set_id.rsplit(':', 1)[-1]}",),
            risk_flags=("decision_set_missing",),
            adjustments=(),
            checked_at=checked_at,
            payload={
                "source_module": self.module_name,
                "timestamp": checked_at,
                "calculation_version": f"{CALCULATION_VERSION}:{job.config_ref}",
                "confidence_score": 0.0,
                "data_quality_score": 0.0,
                "run_mode": request.run_mode,
                "risk_policy_id": request.risk_policy_id,
                "market_state_ref": request.market_state_ref,
                "data_quality_report_ref": request.data_quality_report_ref,
                "risk_rejection_count": 1,
                "as_of_ts": as_of_ts,
            },
        )
        risk_event = RiskEventRecord(
            universe_id=job.universe_id,
            instrument_id=None,
            as_of_ts=checked_at,
            source_module=self.module_name,
            calculation_version=f"{CALCULATION_VERSION}:{job.config_ref}",
            payload={
                "object_type": "risk_event",
                "risk_check_id": risk_check_id,
                "decision_set_id": request.decision_set_id,
                "status": "manual_review_required",
                "risk_flags": ["decision_set_missing"],
                "approved_order_intents": [],
                "rejected_decisions": [f"decisions.decision_set:{request.decision_set_id.rsplit(':', 1)[-1]}"],
                "source_module": self.module_name,
                "timestamp": checked_at,
                "confidence_score": 0.0,
                "calculation_version": f"{CALCULATION_VERSION}:{job.config_ref}",
            },
        )
        return risk_check, risk_event

    def global_risk_flags(
        self,
        *,
        request: RiskCheckRequest,
        risk_policy: RiskPolicy | None,
        portfolio_snapshot: PortfolioSnapshot | None,
        portfolio_limits: tuple[PortfolioLimit, ...],
        as_of_ts: str,
    ) -> tuple[tuple[str, ...], str]:
        flags: list[str] = []
        status = "approved"
        if risk_policy is None:
            flags.append("risk_policy_missing")
            status = "manual_review_required"
        elif risk_policy.status != "active":
            flags.append("risk_policy_not_active")
            status = "manual_review_required"
        else:
            if request.run_mode not in risk_policy.run_mode_allowed:
                flags.append("run_mode_not_allowed_by_risk_policy")
                status = "rejected"
            if _rule_bool(risk_policy.rules, "global_kill_switch") or _rule_bool(risk_policy.rules, "kill_switch"):
                flags.append("global_kill_switch_enabled")
                status = "rejected"

        if portfolio_snapshot is None:
            flags.append("portfolio_snapshot_missing")
            return tuple(dict.fromkeys(flags)), "manual_review_required"

        stale_seconds = self.portfolio_stale_seconds(risk_policy, portfolio_limits)
        age = (parse_utc_iso(as_of_ts) - parse_utc_iso(portfolio_snapshot.as_of_ts)).total_seconds()
        if age > stale_seconds:
            flags.append("portfolio_snapshot_stale")
            status = "rejected" if status != "manual_review_required" else status
        return tuple(dict.fromkeys(flags)), status

    def assess_decision(
        self,
        *,
        request: RiskCheckRequest,
        job: ModuleJob,
        decision_set: DecisionSet,
        decision: Mapping[str, Any],
        risk_check_id: str,
        risk_policy: RiskPolicy | None,
        portfolio_limits: tuple[PortfolioLimit, ...],
        instrument_limit: InstrumentLimit | None,
        portfolio_snapshot: PortfolioSnapshot | None,
        position: PositionState | None,
        feature_vector: FeatureVector | None,
        as_of_ts: str,
        existing_order_count: int,
        pending_order_count: int,
    ) -> RiskDecisionAssessment:
        del as_of_ts
        instrument_id = str(decision.get("instrument_id") or "")
        action = str(decision.get("action") or "hold")
        flags: list[str] = []
        adjustments: list[Mapping[str, Any]] = []
        metrics: dict[str, float] = {}
        if action == "hold":
            return RiskDecisionAssessment(instrument_id, action, "approved", (), {}, ())
        if action == "block":
            return RiskDecisionAssessment(
                instrument_id=instrument_id,
                action=action,
                status="rejected",
                flags=("decision_action_block",),
                metrics={},
                adjustments=(),
                rejected_decision_ref=self.decision_ref(decision_set.decision_set_id, instrument_id),
            )
        if action not in ORDER_ACTIONS:
            return RiskDecisionAssessment(
                instrument_id=instrument_id,
                action=action,
                status="manual_review_required",
                flags=("unknown_decision_action",),
                metrics={},
                adjustments=(),
                rejected_decision_ref=self.decision_ref(decision_set.decision_set_id, instrument_id),
            )
        if request.run_mode == "analysis_only":
            return RiskDecisionAssessment(
                instrument_id=instrument_id,
                action=action,
                status="approved",
                flags=("analysis_only_no_order_intent",),
                metrics={},
                adjustments=(),
            )
        if portfolio_snapshot is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "portfolio_snapshot_missing")
        if risk_policy is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "risk_policy_missing")
        if feature_vector is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "feature_vector_missing")
        if instrument_limit is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "instrument_limit_missing")
        freshness_flags = self.feature_freshness_flags(feature_vector)
        if freshness_flags:
            return RiskDecisionAssessment(
                instrument_id,
                action,
                "rejected",
                freshness_flags,
                {},
                (),
                self.decision_ref(decision_set.decision_set_id, instrument_id),
            )

        price = self.latest_price(feature_vector, position)
        if price is None or price <= 0:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "latest_price_missing")

        market_session_status = self.market_session_status(feature_vector, risk_policy, portfolio_snapshot)
        if market_session_status != "open":
            return RiskDecisionAssessment(
                instrument_id,
                action,
                "rejected",
                (f"market_session_status:{market_session_status or 'missing'}",),
                {},
                (),
                self.decision_ref(decision_set.decision_set_id, instrument_id),
            )
        market_regime = self.market_regime(feature_vector, risk_policy, portfolio_snapshot)
        if market_regime in self.blocked_market_regimes(risk_policy):
            return RiskDecisionAssessment(
                instrument_id,
                action,
                "rejected",
                (f"market_regime_blocked:{market_regime}",),
                {},
                (),
                self.decision_ref(decision_set.decision_set_id, instrument_id),
            )

        arena_go_secid = self.arena_go_secid(feature_vector, instrument_limit)
        if not arena_go_secid:
            return RiskDecisionAssessment(
                instrument_id,
                action,
                "rejected",
                ("arena_go_secid_missing",),
                {},
                (),
                self.decision_ref(decision_set.decision_set_id, instrument_id),
            )

        min_data_quality = self.min_data_quality_score(risk_policy, portfolio_limits)
        if feature_vector.data_quality_score < min_data_quality:
            flags.append("data_quality_score_below_threshold")

        current_quantity = position.quantity if position else 0.0
        current_value = position.market_value if position and position.market_value is not None else current_quantity * price
        requested_quantity = self.requested_order_quantity(action, decision, current_quantity, portfolio_snapshot.equity, price)
        if requested_quantity <= 0:
            return RiskDecisionAssessment(
                instrument_id,
                action,
                "rejected",
                ("order_quantity_non_positive",),
                {},
                (),
                self.decision_ref(decision_set.decision_set_id, instrument_id),
            )

        side = self.order_side_for_action(action, current_quantity)
        position_effect = self.position_effect_for_order(
            action=action,
            side=side,
            current_quantity=current_quantity,
            requested_quantity=requested_quantity,
        )
        decision_position_effect = str(decision.get("position_effect") or "").strip()
        if decision_position_effect and decision_position_effect != position_effect:
            flags.append("decision_position_effect_recomputed")
        available_cash = self.available_cash(portfolio_snapshot)
        min_expected_edge = self.portfolio_limit_value(portfolio_limits, risk_policy, "min_expected_edge_after_cost_score")
        expected_edge = _payload_float(decision, "expected_edge_score")
        expected_edge_after_cost = self.expected_edge_after_cost_score(decision, feature_vector)
        metrics["expected_edge_score"] = expected_edge
        metrics["expected_edge_after_cost_score"] = expected_edge_after_cost
        metrics["short_selling_enabled"] = 1.0 if self.short_selling_allowed(risk_policy) else 0.0
        risk_reducing_order = position_effect in {"reduce_long", "close_long", "reduce_short", "close_short"}
        new_or_add_short = position_effect in {"open_short", "increase_short"}
        new_or_add_long = position_effect in {"open_long", "increase_long"}
        if new_or_add_long and min_expected_edge is not None and expected_edge_after_cost < min_expected_edge:
            flags.append("expected_edge_after_cost_below_threshold")
        if new_or_add_short:
            min_short_edge = abs(min_expected_edge or 0.0)
            if not self.short_selling_allowed(risk_policy):
                flags.append("short_selling_not_supported")
            if min_short_edge > 0 and expected_edge_after_cost > -min_short_edge:
                flags.append("short_expected_edge_after_cost_above_threshold")
        if (new_or_add_long or new_or_add_short) and "turnover_mandate_urgency" in tuple(decision.get("primary_reason_codes") or ()):
            metrics["turnover_driven_expected_edge_after_cost"] = expected_edge_after_cost
            if new_or_add_long and expected_edge_after_cost <= 0:
                flags.append("turnover_trade_without_positive_edge")
            if new_or_add_short and expected_edge_after_cost >= 0:
                flags.append("turnover_trade_without_negative_short_edge")

        requested_quantity = self.lot_aware_order_quantity(
            requested_quantity=requested_quantity,
            price=price,
            position_effect=position_effect,
            expected_edge_after_cost=expected_edge_after_cost,
            min_expected_edge=min_expected_edge,
            instrument_limit=instrument_limit,
            feature_vector=feature_vector,
            risk_policy=risk_policy,
            metrics=metrics,
            adjustments=adjustments,
            flags=flags,
            final_pass=False,
        )
        proposed_trade_value = requested_quantity * price

        if side == "buy" and new_or_add_long and proposed_trade_value > available_cash:
            adjusted_quantity = floor_quantity(available_cash / price)
            adjustments.append(self.adjustment(instrument_id, "quantity", requested_quantity, adjusted_quantity, "cash_check"))
            requested_quantity = adjusted_quantity
            proposed_trade_value = requested_quantity * price
            flags.append("cash_check_adjusted")
            if requested_quantity <= 0:
                flags.append("cash_check_failed")

        max_daily_turnover = self.portfolio_limit_value(portfolio_limits, risk_policy, "max_daily_turnover_rub")
        if max_daily_turnover is not None:
            current_daily_turnover = _payload_float(portfolio_snapshot.payload, "gross_turnover_rub_1d")
            projected_daily_turnover = current_daily_turnover + proposed_trade_value
            metrics["projected_daily_turnover_rub"] = projected_daily_turnover
            metrics["max_daily_turnover_rub"] = max_daily_turnover
            daily_turnover_mode = self.daily_turnover_limit_mode(portfolio_limits, risk_policy)
            metrics["daily_turnover_limit_monitor_only"] = 1.0 if daily_turnover_mode == "monitor_only" else 0.0
            if max_daily_turnover > 0:
                metrics["daily_turnover_limit_usage"] = projected_daily_turnover / max_daily_turnover
            if projected_daily_turnover > max_daily_turnover:
                if daily_turnover_mode == "monitor_only":
                    flags.append("max_daily_turnover_soft_warning")
                else:
                    allowed_value = max(0.0, max_daily_turnover - current_daily_turnover)
                    adjusted_quantity = floor_quantity(allowed_value / price)
                    adjustments.append(self.adjustment(instrument_id, "quantity", requested_quantity, adjusted_quantity, "max_daily_turnover"))
                    requested_quantity = adjusted_quantity
                    proposed_trade_value = requested_quantity * price
                    flags.append("max_daily_turnover_adjusted")
                    if requested_quantity <= 0:
                        flags.append("max_daily_turnover_failed")

        max_order_value = self.max_order_value_rub(instrument_limit, risk_policy, portfolio_limits)
        if max_order_value is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "max_order_value_missing")
        if proposed_trade_value > max_order_value:
            adjusted_quantity = floor_quantity(max_order_value / price)
            adjustments.append(self.adjustment(instrument_id, "quantity", requested_quantity, adjusted_quantity, "max_order_value"))
            requested_quantity = adjusted_quantity
            proposed_trade_value = requested_quantity * price
            flags.append("max_order_value_adjusted")
            if requested_quantity <= 0:
                flags.append("max_order_value_failed")

        portfolio_equity = portfolio_snapshot.equity or portfolio_snapshot.cash or 0.0
        signed_trade_for_exposure = proposed_trade_value if side == "buy" else -proposed_trade_value
        portfolio_exposure = portfolio_exposure_after_trade(
            portfolio_snapshot.gross_exposure,
            signed_trade_for_exposure,
            portfolio_equity,
            current_instrument_value=current_value,
        )
        instrument_exposure = instrument_exposure_after_trade(current_value, signed_trade_for_exposure, portfolio_equity)
        metrics["portfolio_exposure_after_trade"] = portfolio_exposure
        metrics["instrument_exposure_after_trade"] = instrument_exposure
        metrics["current_short_exposure_rub"] = abs(current_value) if current_quantity < 0 else 0.0
        metrics["projected_short_exposure_rub"] = self.projected_short_exposure_rub(
            current_value=current_value,
            signed_trade_value=signed_trade_for_exposure,
            portfolio_snapshot=portfolio_snapshot,
        )

        max_portfolio_exposure = self.portfolio_limit_value(portfolio_limits, risk_policy, "max_portfolio_exposure_pct")
        if max_portfolio_exposure is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "max_portfolio_exposure_missing")
        if portfolio_exposure > max_portfolio_exposure:
            flags.append("portfolio_exposure_limit_exceeded")

        if new_or_add_short:
            max_single_short_order_value = self.short_limit_value(
                portfolio_limits,
                risk_policy,
                "max_single_short_order_value_rub",
                "RISK_MAX_SINGLE_SHORT_ORDER_VALUE_RUB",
            )
            if max_single_short_order_value is not None and proposed_trade_value > max_single_short_order_value:
                adjusted_quantity = floor_quantity(max_single_short_order_value / price)
                adjustments.append(self.adjustment(instrument_id, "quantity", requested_quantity, adjusted_quantity, "max_single_short_order_value"))
                requested_quantity = adjusted_quantity
                proposed_trade_value = requested_quantity * price
                flags.append("max_single_short_order_value_adjusted")
                if requested_quantity <= 0:
                    flags.append("max_single_short_order_value_failed")
            max_short_position_pct = self.short_limit_value(
                portfolio_limits,
                risk_policy,
                "max_short_position_pct",
                "RISK_MAX_SHORT_POSITION_PCT",
            )
            if max_short_position_pct is not None and instrument_exposure > max_short_position_pct:
                flags.append("max_short_position_pct_exceeded")
            max_total_short_exposure_pct = self.short_limit_value(
                portfolio_limits,
                risk_policy,
                "max_total_short_exposure_pct",
                "RISK_MAX_TOTAL_SHORT_EXPOSURE_PCT",
            )
            if (
                max_total_short_exposure_pct is not None
                and portfolio_equity
                and portfolio_equity > 0
                and metrics["projected_short_exposure_rub"] / portfolio_equity > max_total_short_exposure_pct
            ):
                flags.append("max_total_short_exposure_pct_exceeded")

        max_position_pct = instrument_limit.max_position_pct
        if max_position_pct is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "max_position_pct_missing")
        if instrument_exposure > max_position_pct:
            allowed_value = max(0.0, (max_position_pct * portfolio_equity) - abs(current_value))
            adjusted_quantity = floor_quantity(allowed_value / price)
            adjustments.append(self.adjustment(instrument_id, "quantity", requested_quantity, adjusted_quantity, "position_limit_check"))
            requested_quantity = adjusted_quantity
            proposed_trade_value = requested_quantity * price
            flags.append("position_limit_adjusted")
            if requested_quantity <= 0:
                flags.append("position_limit_failed")

        sector_limit = self.portfolio_limit_value(portfolio_limits, risk_policy, "max_sector_exposure_pct")
        if sector_limit is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "max_sector_exposure_missing")
        sector_exposure = self.sector_exposure_after_trade(feature_vector, instrument_limit, instrument_exposure)
        metrics["sector_exposure_after_trade"] = sector_exposure
        if sector_exposure > sector_limit:
            flags.append("sector_limit_exceeded")

        daily_loss_limit = self.max_daily_loss_rub(portfolio_limits, risk_policy, portfolio_snapshot)
        if daily_loss_limit is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "max_daily_loss_limit_missing")
        daily_usage = daily_loss_usage(self.daily_pnl(portfolio_snapshot), daily_loss_limit)
        metrics["daily_loss_limit_rub"] = daily_loss_limit
        metrics["daily_loss_usage"] = daily_usage
        if daily_usage >= 1.0:
            flags.append("daily_loss_limit_exceeded")

        drawdown_limit = self.portfolio_limit_value(portfolio_limits, risk_policy, "max_drawdown_limit")
        if drawdown_limit is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "max_drawdown_limit_missing")
        drawdown = self.current_drawdown(portfolio_snapshot)
        drawdown_used = drawdown_usage(drawdown, drawdown_limit)
        metrics["drawdown_usage"] = drawdown_used
        if drawdown_used >= 1.0:
            flags.append("drawdown_limit_exceeded")

        estimated_slippage_bps = self.estimated_slippage_bps(feature_vector)
        max_slippage_bps = instrument_limit.max_slippage_bps or self.portfolio_limit_value(portfolio_limits, risk_policy, "max_allowed_slippage_bps")
        if estimated_slippage_bps is None or max_slippage_bps is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "slippage_data_missing")
        liquidity_usage = liquidity_limit_usage(estimated_slippage_bps, max_slippage_bps)
        slippage_usage = slippage_limit_usage(estimated_slippage_bps, max_slippage_bps)
        metrics["liquidity_limit_usage"] = liquidity_usage
        metrics["slippage_limit_usage"] = slippage_usage
        if liquidity_usage > 1.0 or slippage_usage > 1.0:
            flags.append("slippage_limit_exceeded")

        risk_budget_used = self.portfolio_limit_value(portfolio_limits, risk_policy, "used_risk_budget")
        total_risk_budget = self.portfolio_limit_value(portfolio_limits, risk_policy, "total_risk_budget")
        metrics["risk_budget_usage"] = risk_budget_usage(risk_budget_used, total_risk_budget)

        daily_limit = self.arena_go_daily_trade_limit(risk_policy, portfolio_limits)
        if daily_limit is None:
            return self.manual_assessment(decision_set.decision_set_id, instrument_id, action, "arena_go_daily_trade_limit_missing")
        if existing_order_count + pending_order_count + 1 > daily_limit:
            flags.append("arena_go_daily_trade_limit_exceeded")

        metrics["proposed_trade_value_rub"] = proposed_trade_value
        metrics["approved_quantity"] = requested_quantity
        metrics["available_cash_after_trade"] = available_cash - proposed_trade_value if side == "buy" else available_cash + proposed_trade_value

        if requested_quantity <= 0:
            flags.append("adjusted_quantity_non_positive")

        requested_quantity = self.lot_aware_order_quantity(
            requested_quantity=requested_quantity,
            price=price,
            position_effect=position_effect,
            expected_edge_after_cost=expected_edge_after_cost,
            min_expected_edge=min_expected_edge,
            instrument_limit=instrument_limit,
            feature_vector=feature_vector,
            risk_policy=risk_policy,
            metrics=metrics,
            adjustments=adjustments,
            flags=flags,
            final_pass=True,
        )
        proposed_trade_value = requested_quantity * price
        metrics["proposed_trade_value_rub"] = proposed_trade_value
        metrics["approved_quantity"] = requested_quantity
        metrics["available_cash_after_trade"] = available_cash - proposed_trade_value if side == "buy" else available_cash + proposed_trade_value
        min_risk_order_value = self.min_risk_increasing_order_value_rub(risk_policy, portfolio_limits)
        metrics["min_risk_increasing_order_value_rub"] = min_risk_order_value
        if (new_or_add_long or new_or_add_short) and min_risk_order_value > 0 and proposed_trade_value < min_risk_order_value:
            flags.append("risk_increasing_order_value_below_minimum")
        if requested_quantity <= 0:
            flags.append("adjusted_quantity_non_positive")

        reject_flags = tuple(
            flag
            for flag in flags
            if flag.endswith("_exceeded")
            or flag.endswith("_failed")
            or flag
            in {
                "data_quality_score_below_threshold",
                "adjusted_quantity_non_positive",
                "order_quantity_below_min_executable_lot",
                "min_lot_edge_not_justified",
                "expected_edge_after_cost_below_threshold",
                "short_expected_edge_after_cost_above_threshold",
                "turnover_trade_without_positive_edge",
                "turnover_trade_without_negative_short_edge",
                "short_selling_not_supported",
                "risk_increasing_order_value_below_minimum",
            }
        )
        if reject_flags:
            return RiskDecisionAssessment(
                instrument_id=instrument_id,
                action=action,
                status="rejected",
                flags=tuple(dict.fromkeys(flags)),
                metrics=metrics,
                adjustments=tuple(adjustments),
                rejected_decision_ref=self.decision_ref(decision_set.decision_set_id, instrument_id),
            )

        order = OrderIntentRecord(
            order_intent_id=stable_record_id(
                "order_intent",
                {
                    "risk_check_id": risk_check_id,
                    "decision_set_id": decision_set.decision_set_id,
                    "instrument_id": instrument_id,
                    "side": side,
                    "quantity": requested_quantity,
                    "run_mode": request.run_mode,
                },
            ),
            instrument_id=instrument_id,
            side=side,
            quantity=requested_quantity,
            order_type=str(_rule_value(risk_policy.rules, "order_type") or DEFAULT_ORDER_TYPE),
            limit_price=self.limit_price(price, side, max_slippage_bps),
            time_in_force=str(_rule_value(risk_policy.rules, "time_in_force") or DEFAULT_TIME_IN_FORCE),
            max_slippage_bps=float(max_slippage_bps),
            execution_ttl_seconds=self.execution_ttl_seconds(risk_policy, portfolio_limits, feature_vector),
            decision_set_id=decision_set.decision_set_id,
            risk_check_id=risk_check_id,
            run_mode=request.run_mode,
            payload={
                "source_module": self.module_name,
                "generated_by": "agent",
                "calculation_version": f"{CALCULATION_VERSION}:{job.config_ref}",
                "timestamp": to_utc_iso(utc_now()),
                "confidence_score": feature_vector.data_quality_score,
                "arena_go_secid": arena_go_secid,
                "decision_set_id": decision_set.decision_set_id,
                "risk_check_id": risk_check_id,
                "run_mode": request.run_mode,
                "system_mode": __import__("os").getenv("SYSTEM_MODE", "automatic_live_trading"),
                "source_feature_refs": (
                    f"features.feature_vector:{feature_vector.feature_vector_id}",
                    *_string_tuple(feature_vector.features.get("_meta", {}).get("source_refs") if isinstance(feature_vector.features.get("_meta"), Mapping) else ()),
                ),
                "market_session_status": market_session_status,
                "position_effect": position_effect,
                "short_selling_enabled": self.short_selling_allowed(risk_policy),
                "arena_go_shorts_allowed": self.arena_go_shorts_allowed(risk_policy),
                "risk_metrics": metrics,
            },
        )
        status = "approved_with_changes" if adjustments else "approved"
        return RiskDecisionAssessment(
            instrument_id=instrument_id,
            action=action,
            status=status,
            flags=tuple(dict.fromkeys(flags)),
            metrics=metrics,
            adjustments=tuple(adjustments),
            order_intent=order,
        )

    def apply_approved_order_to_shadow_state(
        self,
        portfolio_snapshot: PortfolioSnapshot,
        positions_by_instrument: Mapping[str, PositionState],
        order: OrderIntentRecord,
        metrics: Mapping[str, float],
    ) -> tuple[PortfolioSnapshot, dict[str, PositionState]]:
        price = order.limit_price or _payload_float(order.payload.get("risk_metrics", {}), "reference_price") or 0.0
        trade_value = _payload_float(metrics, "proposed_trade_value_rub")
        if trade_value is None:
            trade_value = max(0.0, float(order.quantity or 0.0) * float(price or 0.0))
        equity = (
            portfolio_snapshot.equity
            or portfolio_snapshot.cash
            or portfolio_snapshot.initial_capital_rub
            or 0.0
        )
        current_exposure = float(portfolio_snapshot.gross_exposure or 0.0)
        current_gross_value = current_exposure * float(equity) if 0.0 <= current_exposure <= 2.0 else current_exposure
        signed_trade_value = trade_value if order.side == "buy" else -trade_value

        current_cash = float(portfolio_snapshot.cash or 0.0)
        new_cash = current_cash - trade_value if order.side == "buy" else current_cash + trade_value
        payload = dict(portfolio_snapshot.payload or {})
        current_turnover = _payload_float(payload, "gross_turnover_rub_1d") or 0.0
        payload["gross_turnover_rub_1d"] = current_turnover + trade_value
        payload["cash_after_pending_orders"] = new_cash
        payload["shadow_reserved_cash_rub"] = max(0.0, current_cash - new_cash)
        payload["shadow_pending_order_count"] = int(payload.get("shadow_pending_order_count") or 0) + 1

        positions_copy = dict(positions_by_instrument)
        current_position = positions_copy.get(order.instrument_id)
        current_quantity = float(current_position.quantity) if current_position is not None else 0.0
        current_value = (
            float(current_position.market_value)
            if current_position is not None and current_position.market_value is not None
            else current_quantity * float(price or 0.0)
        )
        signed_quantity = float(order.quantity or 0.0) if order.side == "buy" else -float(order.quantity or 0.0)
        new_quantity = current_quantity + signed_quantity
        new_value = current_value + signed_trade_value
        new_gross_value = max(0.0, current_gross_value - abs(current_value) + abs(new_value))
        new_gross_exposure = new_gross_value / float(equity) if equity and equity > 0 else 0.0
        average_price = (abs(new_value) / abs(new_quantity)) if new_quantity != 0 else current_position.average_price if current_position else None
        market_price = float(price or 0.0) if price else current_position.market_price if current_position else None
        shadow_snapshot = replace(
            portfolio_snapshot,
            cash=new_cash,
            gross_exposure=new_gross_exposure,
            payload=payload,
        )
        position_payload = dict(current_position.payload or {}) if current_position is not None else {}
        position_payload["shadow_pending_order_id"] = order.order_intent_id
        position_payload["shadow_position_state"] = True
        if current_position is not None:
            positions_copy[order.instrument_id] = replace(
                current_position,
                quantity=new_quantity,
                average_price=average_price,
                market_price=market_price,
                market_value=new_value,
                payload=position_payload,
            )
        else:
            positions_copy[order.instrument_id] = PositionState(
                position_state_id=f"shadow:{portfolio_snapshot.portfolio_id}:{order.instrument_id}",
                portfolio_id=portfolio_snapshot.portfolio_id,
                instrument_id=order.instrument_id,
                as_of_ts=portfolio_snapshot.as_of_ts,
                quantity=new_quantity,
                average_price=average_price,
                market_price=market_price,
                market_value=new_value,
                unrealized_pnl=0.0,
                payload=position_payload,
            )
        return shadow_snapshot, positions_copy

    def result_status(
        self,
        assessments: list[RiskDecisionAssessment],
        adjustments: tuple[Mapping[str, Any], ...],
        orders: tuple[OrderIntentRecord, ...],
        risk_flags: tuple[str, ...],
    ) -> str:
        del risk_flags
        statuses = {item.status for item in assessments}
        if "manual_review_required" in statuses:
            return "manual_review_required"
        if statuses and statuses <= {"rejected"}:
            return "rejected"
        if "rejected" in statuses or adjustments:
            return "approved_with_changes" if orders else "rejected"
        if orders or statuses <= {"approved"}:
            return "approved"
        return "manual_review_required"

    def manual_assessment(self, decision_set_id: str, instrument_id: str, action: str, flag: str) -> RiskDecisionAssessment:
        return RiskDecisionAssessment(
            instrument_id=instrument_id,
            action=action,
            status="manual_review_required",
            flags=(flag,),
            metrics={},
            adjustments=(),
            rejected_decision_ref=self.decision_ref(decision_set_id, instrument_id),
        )

    def build_audit_record(
        self,
        job: ModuleJob,
        request: RiskCheckRequest,
        risk_check: RiskCheckResultRecord,
        orders: tuple[OrderIntentRecord, ...],
    ) -> AuditRecord:
        severity = "info" if risk_check.status == "approved" else "warning"
        return AuditRecord(
            audit_record_id=stable_record_id(
                "audit_risk_control",
                {
                    "job_id": job.job_id,
                    "risk_check_id": risk_check.risk_check_id,
                    "status": risk_check.status,
                },
            ),
            module_name=self.module_name,
            job_id=job.job_id,
            severity=severity,
            event_type="risk_check_completed",
            message="Risk Control completed pre-trade checks without broker side effects",
            object_type="risk_check_result",
            object_ref=f"risk.risk_check_result:{risk_check.risk_check_id}",
            reason_codes=risk_check.risk_flags,
            payload={
                "decision_set_id": request.decision_set_id,
                "risk_policy_id": request.risk_policy_id,
                "run_mode": request.run_mode,
                "orders_created": len(orders),
                "no_broker_calls": True,
                "risk_adjustments_logged": bool(risk_check.adjustments),
            },
        )

    def _failed_result(self, job: ModuleJob, started_at: str, error: Exception) -> RiskControlExecutionResult:
        audit_ref: tuple[str, ...] = ()
        try:
            audit_record = AuditRecord(
                audit_record_id=stable_record_id(
                    "audit_risk_control_failed",
                    {"job_id": job.job_id, "error": str(error), "calculation_version": CALCULATION_VERSION},
                ),
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="risk_control_failed",
                message=str(error),
                object_type="module_job",
                object_ref=f"audit.module_job:{job.job_id}",
                reason_codes=("risk_control_failed",),
                payload={"calculation_version": CALCULATION_VERSION},
            )
            audit_ref = (self.repository.save_audit_record(audit_record),)
        except Exception:
            audit_ref = ()
        return RiskControlExecutionResult(
            module_job_result=ModuleJobResult(
                job_id=job.job_id,
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=audit_ref,
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
                events_written=0,
                data_quality_score=0.0,
            ),
            risk_check_result=None,
            order_intents=(),
            risk_events=(),
            audit_refs=audit_ref,
        )

    def _audit_missing_job(self) -> tuple[str, ...]:
        try:
            return (
                self.repository.save_audit_record(
                    AuditRecord(
                        audit_record_id=stable_record_id(
                            "audit_risk_control_missing_job",
                            {"module_name": self.module_name, "error": "module_job_required"},
                        ),
                        module_name=self.module_name,
                        job_id="missing",
                        severity="error",
                        event_type="risk_control_failed",
                        message=f"{self.module_name} requires module_job",
                        object_type="module_job",
                        object_ref="audit.module_job:missing",
                        reason_codes=("module_job_required",),
                        payload={"calculation_version": CALCULATION_VERSION},
                    )
                ),
            )
        except Exception:
            return ()

    def portfolio_stale_seconds(self, risk_policy: RiskPolicy | None, portfolio_limits: tuple[PortfolioLimit, ...]) -> float:
        value = self.portfolio_limit_value(portfolio_limits, risk_policy, "portfolio_snapshot_ttl_seconds")
        return value if value is not None and value > 0 else DEFAULT_PORTFOLIO_STALE_SECONDS

    def min_data_quality_score(self, risk_policy: RiskPolicy, portfolio_limits: tuple[PortfolioLimit, ...]) -> float:
        return self.portfolio_limit_value(portfolio_limits, risk_policy, "min_data_quality_score") or 0.0

    def max_order_value_rub(
        self,
        instrument_limit: InstrumentLimit,
        risk_policy: RiskPolicy,
        portfolio_limits: tuple[PortfolioLimit, ...],
    ) -> float | None:
        return (
            instrument_limit.max_order_value_rub
            or _rule_float(instrument_limit.payload, "max_order_value_rub")
            or self.portfolio_limit_value(portfolio_limits, risk_policy, "max_order_value_rub")
        )

    def arena_go_daily_trade_limit(self, risk_policy: RiskPolicy, portfolio_limits: tuple[PortfolioLimit, ...]) -> float | None:
        return self.portfolio_limit_value(portfolio_limits, risk_policy, "arena_go_daily_trade_limit")

    def daily_submitted_order_count(self, portfolio_snapshot: PortfolioSnapshot | None) -> int:
        if portfolio_snapshot is None or not isinstance(portfolio_snapshot.payload, Mapping):
            return 0
        for key in (
            "arena_go_daily_submitted_order_count",
            "arena_go_daily_trade_count",
            "daily_submitted_order_count",
            "daily_submitted_trade_count",
        ):
            value = _rule_float(portfolio_snapshot.payload, key)
            if value is not None and value >= 0:
                return int(value)
        return 0

    def execution_ttl_seconds(
        self,
        risk_policy: RiskPolicy,
        portfolio_limits: tuple[PortfolioLimit, ...],
        feature_vector: FeatureVector,
    ) -> int:
        configured_ttl = int(_rule_float(risk_policy.rules, "execution_ttl_seconds") or DEFAULT_ORDER_TTL_SECONDS)
        portfolio_ttl = int(self.portfolio_stale_seconds(risk_policy, portfolio_limits))
        liquidity_ttl = self.liquidity_ttl_seconds(feature_vector)
        ttl_candidates = [configured_ttl, portfolio_ttl]
        if liquidity_ttl is not None and liquidity_ttl > 0:
            ttl_candidates.append(int(liquidity_ttl))
        return max(1, min(ttl_candidates))

    def liquidity_ttl_seconds(self, feature_vector: FeatureVector) -> float | None:
        for metric_name in ("estimated_order_slippage_bps", "estimated_slippage_bps", "spread_bps"):
            payload = feature_vector.features.get(metric_name)
            if isinstance(payload, Mapping):
                value = _payload_float(payload, "ttl_seconds")
                if value is not None:
                    return value
        return None

    def feature_freshness_flags(self, feature_vector: FeatureVector) -> tuple[str, ...]:
        required_metrics = (
            "latest_price",
            "market_price",
            "close_price",
            "last_price",
            "price",
            "estimated_order_slippage_bps",
            "estimated_slippage_bps",
            "spread_bps",
            "market_session_status",
            "market_regime",
            "arena_go_secid",
            "secid",
        )
        flags: list[str] = []
        for metric_name in required_metrics:
            payload = feature_vector.features.get(metric_name)
            if not isinstance(payload, Mapping):
                continue
            ttl_status = str(payload.get("ttl_status") or "fresh")
            if ttl_status != "fresh":
                flags.append(f"stale_feature_blocked:{metric_name}")
            quality_flags = {str(flag) for flag in (payload.get("quality_flags") or ())}
            if "future_timestamp" in quality_flags:
                flags.append(f"future_timestamp_blocked:{metric_name}")
        meta = feature_vector.features.get("_meta")
        if isinstance(meta, Mapping):
            meta_flags = {str(flag) for flag in (meta.get("quality_flags") or ())}
            if "future_timestamp" in meta_flags or str(meta.get("ttl_status") or "") == "invalid":
                flags.append("future_timestamp_blocked:feature_vector")
        return tuple(dict.fromkeys(flags))


    def max_daily_loss_rub(
        self,
        portfolio_limits: tuple[PortfolioLimit, ...],
        risk_policy: RiskPolicy | None,
        portfolio_snapshot: PortfolioSnapshot,
    ) -> float | None:
        """Return the daily loss hard limit in RUB.

        Older bootstrap policies stored only ``max_daily_loss_pct``.  Treating a
        ratio such as ``0.02`` as two kopecks would make live trading reject
        almost every decision.  The risk gate now prefers explicit RUB limits
        and converts percentage limits using current portfolio equity.
        """
        explicit_rub = self.portfolio_limit_value(portfolio_limits, risk_policy, "max_daily_loss_limit")
        if explicit_rub is not None and explicit_rub > 1.0:
            return explicit_rub
        daily_loss_pct = self.portfolio_limit_value(portfolio_limits, risk_policy, "max_daily_loss_pct")
        equity = portfolio_snapshot.equity or portfolio_snapshot.cash or portfolio_snapshot.initial_capital_rub
        if daily_loss_pct is not None and daily_loss_pct > 0 and equity and equity > 0:
            return float(daily_loss_pct) * float(equity)
        # If an explicit value <= 1 was supplied under a legacy alias, treat it
        # as a ratio rather than RUB.
        if explicit_rub is not None and explicit_rub > 0 and equity and equity > 0:
            return float(explicit_rub) * float(equity)
        return None

    def expected_edge_after_cost_score(self, decision: Mapping[str, Any], feature_vector: FeatureVector) -> float:
        explicit = _payload_float(decision, "expected_edge_after_cost_score")
        if explicit is not None:
            return explicit
        raw_edge = _payload_float(decision, "expected_edge_score")
        cost_bps = 0.0
        for metric_name in ("estimated_order_slippage_bps", "estimated_slippage_bps", "spread_bps", "commission_bps"):
            value = _feature_numeric(feature_vector.features, metric_name)
            if value is not None and value > 0:
                cost_bps += float(value)
        if cost_bps <= 0:
            try:
                cost_bps = max(0.0, float(os.getenv("RISK_CONSERVATIVE_COST_PROXY_BPS", "8.0")))
            except ValueError:
                cost_bps = 8.0
        # Decision edge is a signed normalized score: positive means long edge,
        # negative means short edge.  Costs reduce absolute edge on both sides,
        # so a short signal moves toward zero instead of becoming artificially
        # more negative.
        cost_score = cost_bps / 10_000.0
        if raw_edge > 0:
            return raw_edge - cost_score
        if raw_edge < 0:
            return raw_edge + cost_score
        return 0.0

    def portfolio_limit_value(
        self,
        portfolio_limits: tuple[PortfolioLimit, ...],
        risk_policy: RiskPolicy | None,
        name: str,
    ) -> float | None:
        aliases = {
            "max_portfolio_exposure_pct": ("max_portfolio_exposure_pct", "max_portfolio_gross_exposure_pct", "max_gross_exposure_pct"),
            "max_daily_loss_limit": ("max_daily_loss_limit", "max_daily_loss_rub", "daily_loss_limit_rub"),
            "max_daily_loss_pct": ("max_daily_loss_pct", "daily_loss_limit_pct"),
            "max_drawdown_limit": ("max_drawdown_limit", "max_drawdown_pct"),
            "max_allowed_slippage_bps": ("max_allowed_slippage_bps", "max_slippage_bps"),
            "max_order_value_rub": ("max_order_value_rub", "default_max_order_value_rub"),
            "arena_go_daily_trade_limit": ("arena_go_daily_trade_limit", "daily_trade_limit"),
            "max_daily_turnover_rub": ("max_daily_turnover_rub", "daily_turnover_limit_rub"),
            "min_expected_edge_after_cost_score": ("min_expected_edge_after_cost_score", "min_expected_edge_after_cost"),
            "max_short_position_pct": ("max_short_position_pct", "single_short_exposure_pct"),
            "max_total_short_exposure_pct": ("max_total_short_exposure_pct", "total_short_exposure_pct"),
            "max_single_short_order_value_rub": ("max_single_short_order_value_rub",),
            "min_risk_increasing_order_value_rub": ("min_risk_increasing_order_value_rub", "min_order_value_rub"),
            "max_sector_exposure_pct": ("max_sector_exposure_pct", "sector_limit_pct"),
            "portfolio_snapshot_ttl_seconds": ("portfolio_snapshot_ttl_seconds", "portfolio_state_ttl_seconds"),
            "min_data_quality_score": ("min_data_quality_score",),
            "used_risk_budget": ("used_risk_budget",),
            "total_risk_budget": ("total_risk_budget",),
        }.get(name, (name,))
        for alias in aliases:
            for limit in portfolio_limits:
                if limit.limit_name == alias and limit.limit_value is not None:
                    return limit.limit_value
            if risk_policy is not None:
                value = _rule_float(risk_policy.rules, alias)
                if value is not None:
                    return value
        return None

    def min_risk_increasing_order_value_rub(
        self,
        risk_policy: RiskPolicy,
        portfolio_limits: tuple[PortfolioLimit, ...],
    ) -> float:
        configured = self.portfolio_limit_value(portfolio_limits, risk_policy, "min_risk_increasing_order_value_rub")
        if configured is None:
            configured = _env_float("RISK_MIN_RISK_INCREASING_ORDER_VALUE_RUB", 0.0)
        return max(0.0, float(configured or 0.0))

    def daily_turnover_limit_mode(
        self,
        portfolio_limits: tuple[PortfolioLimit, ...],
        risk_policy: RiskPolicy | None,
    ) -> str:
        """Return whether daily turnover is a hard cap or an audit-only soft limit.

        The live autonomous mandate targets enough gross turnover over the stage
        window, but a per-day turnover ceiling should not become an accidental
        "no more trades today" kill switch when positive-edge sandbox orders are
        available.  Other policies keep the historical hard-cap behavior unless
        they explicitly opt in to monitor-only mode.
        """

        aliases = ("max_daily_turnover_rub", "daily_turnover_limit_rub")
        for limit in portfolio_limits:
            if limit.limit_name in aliases:
                mode = _rule_text(limit.payload, "limit_mode") or _rule_text(limit.payload, "enforcement_mode")
                if mode:
                    normalized = mode.strip().lower().replace("-", "_")
                    if normalized in {"monitor_only", "soft", "soft_warn", "audit_only", "warning"}:
                        return "monitor_only"
                    if normalized in {"hard", "hard_cap", "block"}:
                        return "hard"
                hard_block = _rule_value(limit.payload, "hard_block_enabled")
                if isinstance(hard_block, bool) and not hard_block:
                    return "monitor_only"

        if risk_policy is not None:
            for key in ("daily_turnover_limit_mode", "max_daily_turnover_mode", "max_daily_turnover_limit_mode"):
                mode = _rule_text(risk_policy.rules, key)
                if mode:
                    normalized = mode.strip().lower().replace("-", "_")
                    if normalized in {"monitor_only", "soft", "soft_warn", "audit_only", "warning"}:
                        return "monitor_only"
                    if normalized in {"hard", "hard_cap", "block"}:
                        return "hard"
            hard_block = _rule_value(risk_policy.rules, "max_daily_turnover_hard_block_enabled")
            if isinstance(hard_block, bool) and not hard_block:
                return "monitor_only"

        return "hard"

    def latest_price(self, feature_vector: FeatureVector, position: PositionState | None) -> float | None:
        for metric_name in ("latest_price", "market_price", "close_price", "last_price", "price"):
            value = _feature_numeric(feature_vector.features, metric_name)
            if value is not None and value > 0:
                return value
        if position is not None:
            return position.market_price or position.average_price
        return None

    def estimated_slippage_bps(self, feature_vector: FeatureVector) -> float | None:
        for metric_name in ("estimated_order_slippage_bps", "estimated_slippage_bps", "spread_bps"):
            value = _feature_numeric(feature_vector.features, metric_name)
            if value is not None:
                return value
        return None

    def market_session_status(
        self,
        feature_vector: FeatureVector,
        risk_policy: RiskPolicy,
        portfolio_snapshot: PortfolioSnapshot,
    ) -> str | None:
        for source in (
            _feature_text(feature_vector.features, "market_session_status"),
            _rule_text(risk_policy.rules, "market_session_status"),
            _rule_text(portfolio_snapshot.payload, "market_session_status"),
            current_market_session().market_session_status,
        ):
            if source:
                return source.strip().lower()
        return None

    def market_regime(
        self,
        feature_vector: FeatureVector,
        risk_policy: RiskPolicy,
        portfolio_snapshot: PortfolioSnapshot,
    ) -> str | None:
        for source in (
            _feature_text(feature_vector.features, "market_regime"),
            _rule_text(risk_policy.rules, "market_regime"),
            _rule_text(portfolio_snapshot.payload, "market_regime"),
        ):
            if source:
                return source
        return None

    def blocked_market_regimes(self, risk_policy: RiskPolicy) -> tuple[str, ...]:
        value = risk_policy.rules.get("blocked_market_regimes") if isinstance(risk_policy.rules, Mapping) else None
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value if item not in (None, ""))
        return ("risk_off", "stress", "halt")

    def sector_exposure_after_trade(
        self,
        feature_vector: FeatureVector,
        instrument_limit: InstrumentLimit,
        instrument_exposure: float,
    ) -> float:
        for source in (
            _feature_numeric(feature_vector.features, "sector_exposure_after_trade"),
            _feature_numeric(feature_vector.features, "sector_exposure_pct"),
            _rule_float(instrument_limit.payload, "sector_exposure_after_trade"),
            _rule_float(instrument_limit.payload, "sector_exposure_pct"),
        ):
            if source is not None:
                return max(0.0, source)
        return instrument_exposure

    def arena_go_secid(self, feature_vector: FeatureVector, instrument_limit: InstrumentLimit) -> str | None:
        for source in (
            _rule_text(instrument_limit.payload, "arena_go_secid"),
            _feature_text(feature_vector.features, "arena_go_secid"),
            _feature_text(feature_vector.features, "secid"),
        ):
            if source:
                return source
        return None

    def lot_aware_order_quantity(
        self,
        *,
        requested_quantity: float,
        price: float,
        position_effect: str,
        expected_edge_after_cost: float,
        min_expected_edge: float | None,
        instrument_limit: InstrumentLimit,
        feature_vector: FeatureVector,
        risk_policy: RiskPolicy,
        metrics: dict[str, float],
        adjustments: list[Mapping[str, Any]],
        flags: list[str],
        final_pass: bool,
    ) -> float:
        requested = max(0.0, float(requested_quantity or 0.0))
        lot_size = self.executable_lot_size(instrument_limit, feature_vector)
        submit_units = self.arena_go_submit_quantity_units(risk_policy)
        quantity_mode = self.arena_go_quantity_mode(instrument_limit, feature_vector)
        min_executable_quantity = float(lot_size if self.requires_lot_quantity(submit_units, quantity_mode) else 1)
        metrics["arena_go_lot_size"] = float(lot_size)
        metrics["min_executable_quantity"] = min_executable_quantity
        metrics["order_quantity_before_lot_normalization"] = requested
        metrics["arena_go_submit_units_lots"] = 1.0 if submit_units == "lots" else 0.0
        if requested <= 0:
            metrics["order_quantity_after_lot_normalization"] = 0.0
            return 0.0
        if not self.requires_lot_quantity(submit_units, quantity_mode):
            normalized = floor_quantity(requested, 1.0)
            if normalized != requested:
                adjustments.append(self.adjustment(instrument_limit.instrument_id, "quantity", requested, normalized, "share_quantity_floor"))
            metrics["order_quantity_after_lot_normalization"] = normalized
            return normalized

        floored = floor_quantity(requested, float(lot_size))
        if floored > 0:
            if floored != requested:
                adjustments.append(self.adjustment(instrument_limit.instrument_id, "quantity", requested, floored, "lot_size_floor"))
                flags.append("order_quantity_rounded_to_lot")
            metrics["order_quantity_after_lot_normalization"] = floored
            return floored

        if final_pass or not self.allow_min_lot_round_up(risk_policy):
            flags.append("order_quantity_below_min_executable_lot")
            metrics["order_quantity_after_lot_normalization"] = 0.0
            return 0.0
        if not self.min_lot_edge_justified(
            position_effect=position_effect,
            expected_edge_after_cost=expected_edge_after_cost,
            min_expected_edge=min_expected_edge,
            risk_policy=risk_policy,
        ):
            flags.append("min_lot_edge_not_justified")
            metrics["order_quantity_after_lot_normalization"] = 0.0
            return 0.0

        rounded = float(lot_size)
        adjustments.append(self.adjustment(instrument_limit.instrument_id, "quantity", requested, rounded, "min_lot_round_up"))
        flags.append("order_quantity_rounded_up_to_min_lot")
        metrics["min_lot_order_value_rub"] = rounded * float(price or 0.0)
        metrics["order_quantity_after_lot_normalization"] = rounded
        return rounded

    def executable_lot_size(self, instrument_limit: InstrumentLimit, feature_vector: FeatureVector) -> int:
        value = (
            _rule_float(instrument_limit.payload, "lot_size")
            or _feature_numeric(feature_vector.features, "lot_size")
            or 1.0
        )
        return max(1, int(value))

    def arena_go_submit_quantity_units(self, risk_policy: RiskPolicy) -> str:
        value = str(
            _rule_value(risk_policy.rules, "arena_go_submit_quantity_units")
            or os.getenv("ARENA_GO_SUBMIT_QUANTITY_UNITS")
            or "shares"
        ).strip().lower()
        return "lots" if value == "lots" else "shares"

    def arena_go_quantity_mode(self, instrument_limit: InstrumentLimit, feature_vector: FeatureVector) -> str:
        value = str(
            _rule_text(instrument_limit.payload, "arena_go_quantity_mode")
            or _feature_text(feature_vector.features, "arena_go_quantity_mode")
            or "shares"
        ).strip().lower()
        return "lots" if value == "lots" else "shares"

    def requires_lot_quantity(self, submit_units: str, quantity_mode: str) -> bool:
        return submit_units == "lots" or quantity_mode == "lots"

    def allow_min_lot_round_up(self, risk_policy: RiskPolicy) -> bool:
        configured = _rule_value(risk_policy.rules, "allow_min_lot_round_up")
        if configured is not None:
            return _coerce_bool(configured)
        return _env_bool("RISK_ALLOW_MIN_LOT_ROUND_UP", True)

    def min_lot_edge_justified(
        self,
        *,
        position_effect: str,
        expected_edge_after_cost: float,
        min_expected_edge: float | None,
        risk_policy: RiskPolicy,
    ) -> bool:
        if position_effect in {"reduce_long", "close_long", "reduce_short", "close_short"}:
            return True
        configured = _rule_float(risk_policy.rules, "min_lot_round_up_edge_after_cost_score")
        if configured is None:
            configured = _payload_float(os.environ, "RISK_MIN_LOT_ROUND_UP_EDGE_AFTER_COST")
        threshold_floor = _env_float("RISK_MIN_LOT_ROUND_UP_EDGE_AFTER_COST_FLOOR", 0.05)
        threshold = max(
            abs(configured if configured is not None else (min_expected_edge or 0.0)),
            abs(threshold_floor),
        )
        if position_effect in {"open_short", "increase_short"}:
            return expected_edge_after_cost <= -threshold
        if position_effect in {"open_long", "increase_long"}:
            return expected_edge_after_cost >= threshold
        return False

    def requested_order_quantity(
        self,
        action: str,
        decision: Mapping[str, Any],
        current_quantity: float,
        equity: float | None,
        price: float,
    ) -> float:
        target_quantity = _payload_float(decision, "target_quantity") or 0.0
        target_pct = _payload_float(decision, "target_position_pct") or 0.0
        if action == "buy":
            if target_quantity != 0:
                return floor_quantity(max(0.0, target_quantity - current_quantity))
            if equity and target_pct != 0:
                total_target_quantity = (float(equity) * target_pct) / price
                return floor_quantity(max(0.0, total_target_quantity - current_quantity))
            return 0.0
        if action in {"sell", "close"}:
            if target_quantity != 0:
                return floor_quantity(max(0.0, current_quantity - target_quantity))
            if target_pct < 0 and equity:
                total_target_quantity = (float(equity) * target_pct) / price
                return floor_quantity(max(0.0, current_quantity - total_target_quantity))
            return floor_quantity(abs(current_quantity))
        if action == "reduce":
            if current_quantity > 0:
                if target_quantity < current_quantity:
                    return floor_quantity(current_quantity - max(target_quantity, 0.0))
                return 0.0
            if current_quantity < 0:
                if target_quantity > current_quantity:
                    return floor_quantity(abs(current_quantity - min(target_quantity, 0.0)))
                return 0.0
            return 0.0
        return 0.0

    def limit_price(self, price: float, side: str, max_slippage_bps: float) -> float:
        multiplier = 1.0 + (max_slippage_bps / 10_000.0 if side == "buy" else -max_slippage_bps / 10_000.0)
        return max(0.0, price * multiplier)

    def available_cash(self, portfolio_snapshot: PortfolioSnapshot) -> float:
        for key in ("remaining_cash", "cash_balance", "cash"):
            value = _rule_float(portfolio_snapshot.payload, key)
            if value is not None:
                return value
        return float(portfolio_snapshot.cash or 0.0)

    def daily_pnl(self, portfolio_snapshot: PortfolioSnapshot) -> float:
        value = _rule_float(portfolio_snapshot.payload, "daily_pnl")
        if value is not None:
            return value
        return float(portfolio_snapshot.realized_pnl or 0.0) + float(portfolio_snapshot.unrealized_pnl or 0.0)

    def current_drawdown(self, portfolio_snapshot: PortfolioSnapshot) -> float:
        value = _rule_float(portfolio_snapshot.payload, "current_drawdown")
        if value is not None:
            return value
        initial = portfolio_snapshot.initial_capital_rub
        equity = portfolio_snapshot.equity
        if initial is None or initial <= 0 or equity is None:
            return 0.0
        return min(0.0, (equity - initial) / initial)

    def aggregate_data_quality(self, feature_vectors: tuple[FeatureVector, ...]) -> float:
        values = [item.data_quality_score for item in feature_vectors]
        if not values:
            return 0.0
        return max(0.0, min(1.0, sum(values) / len(values)))

    def decision_ref(self, decision_set_id: str, instrument_id: str) -> str:
        return f"decisions.decision_set:{decision_set_id}:{instrument_id}"

    def adjustment(self, instrument_id: str, field: str, old_value: float, new_value: float, reason_code: str) -> dict[str, Any]:
        return {
            "instrument_id": instrument_id,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "reason_code": reason_code,
        }


def _payload_float(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rule_value(payload: Mapping[str, Any], key: str) -> Any:
    return payload.get(key) if isinstance(payload, Mapping) else None


def _rule_float(payload: Mapping[str, Any], key: str) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    return _payload_float(payload, key)


def _rule_bool(payload: Mapping[str, Any], key: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    value = payload.get(key)
    return _coerce_bool(value)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return _coerce_bool(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rule_text(payload: Mapping[str, Any], key: str) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item or ""))
    if value is None or value == "":
        return ()
    return (str(value),)


def _feature_numeric(features: Mapping[str, Mapping[str, Any]], metric_name: str) -> float | None:
    payload = features.get(metric_name)
    if not isinstance(payload, Mapping):
        return None
    for key in ("raw_value", "value", "price", "normalized_value"):
        value = _payload_float(payload, key)
        if value is not None:
            return value
    return None


def _feature_text(features: Mapping[str, Mapping[str, Any]], metric_name: str) -> str | None:
    payload = features.get(metric_name)
    if not isinstance(payload, Mapping):
        return None
    for key in ("raw_value", "value", "status", "secid", "text"):
        value = payload.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None
