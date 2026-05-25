from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, ModuleJob, ModuleJobResult, RetryPolicy
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)
from agent_app.runtime_calendar import current_market_session

from .gateway import GatewayUnavailableError, submit_via_gateway
from .metrics import arena_go_quantity, avg_fill_price, fee_estimate, fill_ratio, slippage_bps, time_to_fill_ms
from .repository import (
    AuditRecord,
    ExecutionEngineRepository,
    ExecutionResultRecord,
    FillReportRecord,
    InMemoryExecutionEngineRepository,
    InstrumentProfileRecord,
    OrderIntentRecord,
    OrderStatusRecord,
    PortfolioSnapshotRecord,
    RiskCheckResultRecord,
    ref_tail,
    stable_record_id,
)


MODULE_NAME = "Execution Engine Module"
CALCULATION_VERSION = "execution_engine_v1"
VALID_CONTOURS = {"execution_contour", "realtime_contour"}
VALID_RUN_MODES = {"paper_trading", "live_trading"}
APPROVED_RISK_STATUSES = {"approved", "approved_with_changes"}
INPUT_FIELDS = {
    "order_intent_refs",
    "market_session_status_ref",
    "execution_policy_id",
    "run_mode",
    "idempotency_key",
}
SUCCESS_STATUSES = {"submitted", "partially_filled", "filled"}
TERMINAL_FAILURE_STATUSES = {"rejected", "expired", "failed", "cancelled"}
ORDER_ALLOWED_MARKET_STATUSES = {
    "open",
    "trading",
    "orders_allowed",
    "open_for_trading",
    "main_session",
    "evening_session",
}


class ExecutionEngineError(ValueError):
    """Raised when module 19 would violate its documented contract."""


@dataclass(frozen=True)
class ExecutionPolicy:
    execution_policy_id: str = "default"
    provider: str = "arena_go"
    min_data_quality_score: float = 0.0
    max_spread_bps: float | None = None
    max_estimated_slippage_bps: float | None = None
    portfolio_snapshot_ttl_seconds: int = 300
    fee_rate_bps: float = 0.0
    gateway_timeout_ms: int = 10_000
    arena_go_bot_name: str = ""
    arena_go_submit_quantity_units: str = "lots"

    @classmethod
    def from_mapping(cls, execution_policy_id: str, payload: Mapping[str, Any] | None = None) -> "ExecutionPolicy":
        payload = payload or {}
        return cls(
            execution_policy_id=execution_policy_id,
            provider=str(payload.get("provider") or "arena_go"),
            min_data_quality_score=_optional_float(payload.get("min_data_quality_score")) or 0.0,
            max_spread_bps=_optional_float(payload.get("max_spread_bps")),
            max_estimated_slippage_bps=_optional_float(payload.get("max_estimated_slippage_bps")),
            portfolio_snapshot_ttl_seconds=int(payload.get("portfolio_snapshot_ttl_seconds") or 300),
            fee_rate_bps=_optional_float(payload.get("fee_rate_bps")) or 0.0,
            gateway_timeout_ms=int(payload.get("gateway_timeout_ms") or 10_000),
            arena_go_bot_name=str(payload.get("arena_go_bot_name") or ""),
            arena_go_submit_quantity_units=str(
                payload.get("arena_go_submit_quantity_units")
                or os.getenv("ARENA_GO_SUBMIT_QUANTITY_UNITS")
                or "lots"
            ).strip().lower(),
        )

    @property
    def bot_name(self) -> str:
        return self.arena_go_bot_name or os.getenv("ARENA_GO_BOT_NAME") or os.getenv("ARENA_GO_PORTFOLIO") or ""


@dataclass(frozen=True)
class ExecutionRequest:
    order_intent_refs: tuple[str, ...]
    market_session_status_ref: str
    execution_policy_id: str
    run_mode: str
    idempotency_key: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "ExecutionRequest":
        extra_top_level = sorted(set(payload) - {"execution_request"})
        if extra_top_level:
            raise ExecutionEngineError(f"payload has undocumented fields: {extra_top_level}")
        request_payload = payload.get("execution_request")
        if not isinstance(request_payload, Mapping):
            raise ExecutionEngineError("payload must contain execution_request")
        missing_fields = sorted(INPUT_FIELDS - set(request_payload))
        if missing_fields:
            raise ExecutionEngineError(f"execution_request missing required fields: {missing_fields}")
        extra_fields = sorted(set(request_payload) - INPUT_FIELDS)
        if extra_fields:
            raise ExecutionEngineError(f"execution_request has undocumented fields: {extra_fields}")

        order_refs_payload = request_payload.get("order_intent_refs")
        if not isinstance(order_refs_payload, (list, tuple)):
            raise ExecutionEngineError("execution_request.order_intent_refs must be a list")
        order_intent_refs = tuple(str(item) for item in order_refs_payload if str(item or ""))

        run_mode = str(request_payload.get("run_mode") or "")
        if run_mode not in VALID_RUN_MODES:
            raise ExecutionEngineError("execution_request.run_mode must be paper_trading or live_trading")
        if run_mode != job.run_mode:
            raise ExecutionEngineError("execution_request.run_mode must match module_job.run_mode")

        idempotency_key = str(request_payload.get("idempotency_key") or "")
        if not idempotency_key:
            raise ExecutionEngineError("execution_request.idempotency_key is required")
        if idempotency_key != job.idempotency_key:
            raise ExecutionEngineError("execution_request.idempotency_key must match module_job.idempotency_key")

        market_session_status_ref = str(request_payload.get("market_session_status_ref") or "")
        execution_policy_id = str(request_payload.get("execution_policy_id") or "")
        missing_text = [
            name
            for name, value in {
                "market_session_status_ref": market_session_status_ref,
                "execution_policy_id": execution_policy_id,
            }.items()
            if not value
        ]
        if missing_text:
            raise ExecutionEngineError(f"execution_request missing text fields: {missing_text}")

        if tuple(job.input_refs):
            ref_tails = {ref_tail(ref) for ref in job.input_refs}
            required_refs = {
                "market_session_status_ref": market_session_status_ref,
                "execution_policy_id": execution_policy_id,
                **{f"order_intent_refs[{index}]": ref for index, ref in enumerate(order_intent_refs)},
            }
            missing_refs = [
                name
                for name, value in required_refs.items()
                if ref_tail(value) not in ref_tails
            ]
            if missing_refs:
                raise ExecutionEngineError(f"execution_request refs must be present in module_job.input_refs: {missing_refs}")

        return cls(
            order_intent_refs=order_intent_refs,
            market_session_status_ref=market_session_status_ref,
            execution_policy_id=execution_policy_id,
            run_mode=run_mode,
            idempotency_key=idempotency_key,
        )


@dataclass(frozen=True)
class ExecutionEngineRunResult:
    module_job_result: ModuleJobResult
    execution_results: tuple[ExecutionResultRecord, ...]
    order_status_refs: tuple[str, ...] = ()
    fill_report_refs: tuple[str, ...] = ()
    execution_result_refs: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "execution_results": [item.to_contract() for item in self.execution_results],
            "order_status_refs": list(self.order_status_refs),
            "fill_report_refs": list(self.fill_report_refs),
            "execution_result_refs": list(self.execution_result_refs),
            "audit_refs": list(self.audit_refs),
        }


@dataclass(frozen=True)
class OrderExecutionOutcome:
    execution_result: ExecutionResultRecord
    execution_result_ref: str
    order_status_refs: tuple[str, ...] = ()
    fill_report_refs: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()


class ExecutionEngineService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: ExecutionEngineRepository | None = None,
        gateway: Any = None,
        execution_policies: Mapping[str, ExecutionPolicy | Mapping[str, Any]] | None = None,
    ) -> None:
        self.repository = repository or InMemoryExecutionEngineRepository()
        self.gateway = gateway
        self.execution_policies = {
            ref_tail(key): value if isinstance(value, ExecutionPolicy) else ExecutionPolicy.from_mapping(ref_tail(key), value)
            for key, value in (execution_policies or {}).items()
        }

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> ExecutionEngineRunResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> ExecutionEngineRunResult:
        return self.execute(payload, job)

    def execute(self, payload: Mapping[str, Any], job: ModuleJob | None) -> ExecutionEngineRunResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            return self._missing_job_result(started_at)
        try:
            self.validate_module_job(job)
            request = ExecutionRequest.from_dict(payload, job)
            policy = self.execution_policy(request.execution_policy_id)
            outcomes = tuple(self.execute_order(request, job, policy, order_ref) for order_ref in request.order_intent_refs)
            execution_results = tuple(outcome.execution_result for outcome in outcomes)
            execution_result_refs = tuple(outcome.execution_result_ref for outcome in outcomes)
            order_status_refs = tuple(ref for outcome in outcomes for ref in outcome.order_status_refs)
            fill_report_refs = tuple(ref for outcome in outcomes for ref in outcome.fill_report_refs)
            audit_refs = tuple(ref for outcome in outcomes for ref in outcome.audit_refs)
            output_refs = (*execution_result_refs, *order_status_refs, *fill_report_refs, *audit_refs)
            statuses = tuple(item.status for item in execution_results)
            module_status = self.module_result_status(statuses)
            warnings = tuple(dict.fromkeys(error for item in execution_results for error in item.errors))
            return ExecutionEngineRunResult(
                module_job_result=ModuleJobResult(
                    job_id=job.job_id,
                    module_name=self.module_name,
                    status=module_status,
                    started_at=started_at,
                    finished_at=to_utc_iso(utc_now()),
                    output_refs=output_refs,
                    warnings=warnings,
                    errors=(),
                    metrics_written=len(execution_result_refs),
                    events_written=len(order_status_refs) + len(fill_report_refs),
                    data_quality_score=self.aggregate_confidence(execution_results),
                ),
                execution_results=execution_results,
                order_status_refs=order_status_refs,
                fill_report_refs=fill_report_refs,
                execution_result_refs=execution_result_refs,
                audit_refs=audit_refs,
            )
        except (ExecutionEngineError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise ExecutionEngineError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise ExecutionEngineError("module_job.module_name must be Execution Engine Module")
        if job.contour not in VALID_CONTOURS:
            raise ExecutionEngineError("module_job.contour is not valid for Execution Engine Module")
        if not job.input_refs:
            raise ExecutionEngineError("module_job.input_refs is required")
        if job.run_mode not in VALID_RUN_MODES:
            raise ExecutionEngineError("module_job.run_mode must be paper_trading or live_trading")
        if not job.idempotency_key:
            raise ExecutionEngineError("module_job.idempotency_key is required")

    def execution_policy(self, execution_policy_id: str) -> ExecutionPolicy:
        policy = self.execution_policies.get(ref_tail(execution_policy_id))
        if policy is not None:
            return policy
        return ExecutionPolicy(execution_policy_id=execution_policy_id)

    def execute_order(
        self,
        request: ExecutionRequest,
        job: ModuleJob,
        policy: ExecutionPolicy,
        order_ref: str,
    ) -> OrderExecutionOutcome:
        order_id = ref_tail(order_ref)
        execution_result_id = self.execution_result_id(order_id, request.idempotency_key)
        existing = self.repository.get_execution_result(execution_result_id)
        if existing is not None:
            audit_ref = self.repository.save_audit_record(
                self.audit_record(
                    job=job,
                    event_type="execution_idempotency_reused",
                    severity="info",
                    object_type="execution_result",
                    object_ref=f"orders.execution_result:{existing.execution_result_id}",
                    reason_codes=("idempotency_enforced",),
                    message="Execution result already exists for this idempotency key; broker resubmit skipped",
                    payload={
                        "order_intent_id": order_id,
                        "idempotency_key": request.idempotency_key,
                        "source_module": self.module_name,
                        "calculation_version": CALCULATION_VERSION,
                        "timestamp": to_utc_iso(utc_now()),
                        "confidence_score": _confidence(existing.payload),
                    },
                )
            )
            return OrderExecutionOutcome(
                execution_result=existing,
                execution_result_ref=f"orders.execution_result:{existing.execution_result_id}",
                audit_refs=(audit_ref,),
            )

        order = self.repository.get_order_intent(order_ref)
        if order is None:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order_id,
                status="rejected",
                errors=("order_intent_missing",),
                audit_event_type="execution_rejected",
                audit_message="Order intent is missing; execution blocked",
            )

        risk_result = self.repository.get_risk_check_result(order.risk_check_id or "")
        instrument_profile = self.repository.get_instrument_profile(order.instrument_id)
        portfolio_snapshot = self.repository.get_latest_portfolio_snapshot(job.universe_id, job.time_range.to_ts)
        validation_errors = self.pre_execution_errors(
            request=request,
            job=job,
            policy=policy,
            order=order,
            risk_result=risk_result,
            instrument_profile=instrument_profile,
            portfolio_snapshot=portfolio_snapshot,
        )
        if validation_errors:
            status = "expired" if "order_ttl_expired" in validation_errors else "rejected"
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status=status,
                errors=validation_errors,
                audit_event_type="execution_blocked",
                audit_message="Execution blocked by documented pre-trade checks",
            )

        if request.run_mode == "paper_trading":
            return self.simulate_paper_order(
                request=request,
                job=job,
                policy=policy,
                order=order,
                risk_result=risk_result,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
            )
        return self.submit_live_order(
            request=request,
            job=job,
            policy=policy,
            order=order,
            risk_result=risk_result,
            instrument_profile=instrument_profile,
            portfolio_snapshot=portfolio_snapshot,
        )

    def pre_execution_errors(
        self,
        *,
        request: ExecutionRequest,
        job: ModuleJob,
        policy: ExecutionPolicy,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        instrument_profile: InstrumentProfileRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if request.run_mode not in VALID_RUN_MODES or job.run_mode not in VALID_RUN_MODES:
            errors.append("run_mode_not_executable")
        if request.run_mode == "live_trading" and policy.provider != "arena_go":
            errors.append("execution_provider_not_allowed")
        if order.run_mode != request.run_mode:
            errors.append("order_run_mode_mismatch")
        if order.quantity <= 0:
            errors.append("order_quantity_invalid")
        if order.side not in {"buy", "sell"}:
            errors.append("order_side_invalid")
        position_effect = str((order.payload or {}).get("position_effect") or "").strip()
        valid_position_effects = {
            "open_long",
            "increase_long",
            "reduce_long",
            "close_long",
            "open_short",
            "increase_short",
            "reduce_short",
            "close_short",
        }
        if request.run_mode == "live_trading" and position_effect not in valid_position_effects:
            errors.append("position_effect_missing")
        expected_side = self.side_for_position_effect(position_effect)
        if expected_side is not None and expected_side != order.side:
            errors.append("position_effect_side_mismatch")
        if position_effect in {"open_short", "increase_short"} and not _env_bool("ARENA_GO_SHORTS_ALLOWED", False):
            errors.append("short_selling_not_supported")
        if risk_result is None:
            errors.append("risk_check_result_missing")
        elif risk_result.status not in APPROVED_RISK_STATUSES:
            errors.append("risk_check_result_not_approved")
        elif not self.risk_approves_order(order, risk_result):
            errors.append("order_intent_not_approved_by_risk")

        if instrument_profile is None:
            errors.append("instrument_profile_missing")
        else:
            if not instrument_profile.tradable:
                errors.append("instrument_not_tradable")
            if not instrument_profile.execution_enabled:
                errors.append("instrument_execution_disabled")
            if request.run_mode == "live_trading" and not instrument_profile.arena_go_secid:
                errors.append("arena_go_secid_missing")

        if order.execution_ttl_seconds is None or order.execution_ttl_seconds <= 0:
            errors.append("execution_ttl_missing")
        elif self.order_ttl_expired(order, job.time_range.to_ts):
            errors.append("order_ttl_expired")

        market_status = self.market_session_status(request, order, risk_result, portfolio_snapshot)
        if market_status not in ORDER_ALLOWED_MARKET_STATUSES:
            errors.append("market_session_not_open")

        if portfolio_snapshot is None:
            errors.append("portfolio_snapshot_missing")
        elif self.portfolio_snapshot_stale(portfolio_snapshot, job.time_range.to_ts, policy):
            errors.append("portfolio_snapshot_stale")

        if self.global_kill_switch_enabled(order, risk_result, portfolio_snapshot):
            errors.append("global_kill_switch_enabled")

        data_quality_score = self.data_quality_score(order, risk_result, portfolio_snapshot)
        if data_quality_score is not None and data_quality_score < policy.min_data_quality_score:
            errors.append("data_quality_score_below_min")

        spread_bps = self.metric_value(order, risk_result, "spread_bps")
        if policy.max_spread_bps is not None and spread_bps is not None and spread_bps > policy.max_spread_bps:
            errors.append("spread_bps_above_limit")

        estimated_slippage_bps = self.metric_value(order, risk_result, "estimated_slippage_bps")
        if estimated_slippage_bps is None:
            estimated_slippage_bps = self.metric_value(order, risk_result, "estimated_order_slippage_bps")
        if (
            policy.max_estimated_slippage_bps is not None
            and estimated_slippage_bps is not None
            and estimated_slippage_bps > policy.max_estimated_slippage_bps
        ):
            errors.append("estimated_slippage_bps_above_policy_limit")
        if (
            order.max_slippage_bps is not None
            and order.max_slippage_bps > 0
            and estimated_slippage_bps is not None
            and estimated_slippage_bps > order.max_slippage_bps
        ):
            errors.append("estimated_slippage_bps_above_order_limit")

        return tuple(dict.fromkeys(errors))

    def simulate_paper_order(
        self,
        *,
        request: ExecutionRequest,
        job: ModuleJob,
        policy: ExecutionPolicy,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        instrument_profile: InstrumentProfileRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> OrderExecutionOutcome:
        reference_price = self.reference_price(order, risk_result, portfolio_snapshot)
        if reference_price is None or reference_price <= 0:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status="rejected",
                errors=("reference_price_missing",),
                audit_event_type="execution_blocked",
                audit_message="Paper trading execution requires a reference price",
            )
        now = to_utc_iso(utc_now())
        fees = fee_estimate(order.quantity, reference_price, policy.fee_rate_bps)
        slip_bps = slippage_bps(order.side, reference_price, reference_price)
        execution_result = self.execution_result(
            request=request,
            policy=policy,
            order_id=order.order_intent_id,
            status="filled",
            broker_order_id=None,
            submitted_at=now,
            last_update_at=now,
            filled_quantity=order.quantity,
            avg_fill_price=reference_price,
            fees=fees,
            slippage=slip_bps,
            errors=(),
            payload={
                "execution_mode": "paper_trading",
                "route": "paper_simulation",
                "reference_price": reference_price,
                "fill_ratio": fill_ratio(order.quantity, order.quantity),
                "time_to_fill_ms": 0,
                "portfolio_update_hint": self.portfolio_update_hint(order, portfolio_snapshot, reference_price, order.quantity),
                "portfolio_update_triggered": True,
                "broker_calls_only_via_gateway": True,
                "paper_mode_no_broker_endpoint": True,
                "instrument_id": order.instrument_id,
                "market_session_status_ref": request.market_session_status_ref,
            },
        )
        order_status = self.order_status(
            order=order,
            status="filled",
            status_ts=now,
            provider=None,
            provider_order_id=None,
            payload={"execution_result_id": execution_result.execution_result_id, "route": "paper_simulation"},
        )
        fill = self.fill_report(
            order=order,
            fill_ts=now,
            quantity=order.quantity,
            price=reference_price,
            fees=fees,
            provider_fill_id=stable_record_id("paper_fill", {"execution_result_id": execution_result.execution_result_id}),
            payload={"execution_result_id": execution_result.execution_result_id, "route": "paper_simulation"},
        )
        execution_result_ref = self.repository.save_execution_result(execution_result)
        order_status_ref = self.repository.save_order_status(order_status)
        fill_ref = self.repository.save_fill_report(fill)
        audit_ref = self.repository.save_audit_record(
            self.audit_record(
                job=job,
                event_type="paper_order_filled",
                severity="info",
                object_type="execution_result",
                object_ref=execution_result_ref,
                reason_codes=(),
                message="Approved order intent was filled in paper trading mode",
                payload={
                    "order_intent_id": order.order_intent_id,
                    "execution_result_id": execution_result.execution_result_id,
                    "portfolio_update_triggered": True,
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "timestamp": now,
                    "confidence_score": _confidence(execution_result.payload),
                },
            )
        )
        return OrderExecutionOutcome(
            execution_result=execution_result,
            execution_result_ref=execution_result_ref,
            order_status_refs=(order_status_ref,),
            fill_report_refs=(fill_ref,),
            audit_refs=(audit_ref,),
        )

    def submit_live_order(
        self,
        *,
        request: ExecutionRequest,
        job: ModuleJob,
        policy: ExecutionPolicy,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        instrument_profile: InstrumentProfileRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> OrderExecutionOutcome:
        if os.getenv("SAFE_LIVE_SUBMIT", "").lower() not in {"1", "true", "yes"}:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status="rejected",
                errors=("safe_live_submit_disabled",),
                audit_event_type="execution_blocked",
                audit_message="Live ArenaGo submit_order is disabled until SAFE_LIVE_SUBMIT=true",
            )
        if os.getenv("ARENA_GO_SANDBOX", "").lower() not in {"1", "true", "yes"}:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status="rejected",
                errors=("arena_go_sandbox_required",),
                audit_event_type="execution_blocked",
                audit_message="Live submit_order is allowed only for ArenaGo sandbox/test contour",
            )
        if os.getenv("LIVE_READINESS_PASSED", "").lower() not in {"1", "true", "yes"}:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status="rejected",
                errors=("live_readiness_not_passed",),
                audit_event_type="execution_blocked",
                audit_message="Live submit_order is disabled until readiness checks pass in startup preflight",
            )
        if instrument_profile is None:
            raise ExecutionEngineError("instrument_profile_missing")
        arena_quantity = arena_go_quantity(
            order_quantity=order.quantity,
            quantity_mode=instrument_profile.arena_go_quantity_mode,
            lot_size=instrument_profile.lot_size,
            submit_units=policy.arena_go_submit_quantity_units,
        )
        if arena_quantity <= 0:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status="rejected",
                errors=("arena_go_quantity_invalid",),
                audit_event_type="execution_blocked",
                audit_message="ArenaGo quantity conversion produced a non-positive quantity",
            )
        external_request = self.arena_go_submit_order_request(request, job, policy, order, instrument_profile, arena_quantity)
        try:
            external_response = submit_via_gateway(self.gateway, external_request)
        except GatewayUnavailableError as error:
            return self.persist_blocked_result(
                job=job,
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                order=order,
                instrument_profile=instrument_profile,
                portfolio_snapshot=portfolio_snapshot,
                status="failed",
                errors=(str(error),),
                audit_event_type="execution_gateway_unavailable",
                audit_message="Live order was not submitted because Gateway adapter is unavailable",
            )

        now = to_utc_iso(utc_now())
        normalized = dict(external_response.data or {})
        data = normalized.get("data") if isinstance(normalized.get("data"), Mapping) else normalized
        data = dict(data) if isinstance(data, Mapping) else {}
        response_errors = tuple(str(item) for item in external_response.errors)
        response_errors = tuple(dict.fromkeys((*response_errors, *(str(item) for item in normalized.get("errors", ())))))
        if external_response.status in {"success", "partial_success"} and data.get("success", True) is not False:
            provider_quantity = _optional_float(data.get("quantity")) or 0.0
            provider_price = _optional_float(data.get("price")) or None
            provider_order_value = _optional_float(data.get("order_value"))
            filled_quantity = self.live_filled_quantity(
                provider_quantity=provider_quantity,
                provider_price=provider_price,
                provider_order_value=provider_order_value,
                instrument_profile=instrument_profile,
                policy=policy,
            )
            status = self.live_success_status(filled_quantity, order.quantity)
            broker_order_id = external_response.provider_tracking_id or _optional_text(data.get("order_id")) or external_response.request_id
            fees = _optional_float(data.get("fees"))
            if fees is None:
                fees = fee_estimate(filled_quantity, provider_price, policy.fee_rate_bps)
            reference_price = self.reference_price(order, risk_result, portfolio_snapshot)
            fill_average_price = provider_price if filled_quantity > 0 else None
            slip_bps = slippage_bps(order.side, fill_average_price, reference_price)
            execution_result = self.execution_result(
                request=request,
                policy=policy,
                order_id=order.order_intent_id,
                status=status,
                broker_order_id=broker_order_id,
                submitted_at=now,
                last_update_at=now,
                filled_quantity=filled_quantity,
                avg_fill_price=fill_average_price,
                fees=fees,
                slippage=slip_bps,
                errors=(),
                payload={
                    "execution_mode": "live_trading",
                    "system_mode": os.getenv("SYSTEM_MODE", "automatic_live_trading"),
                    "run_mode": request.run_mode,
                    "generated_by": "agent",
                    "decision_set_id": order.decision_set_id,
                    "risk_check_id": order.risk_check_id,
                    "route": "arena_go_gateway",
                    "external_request": external_request.to_dict(),
                    "external_response_ref": external_response.data_ref,
                    "provider_tracking_id": external_response.provider_tracking_id,
                    "arena_go_quantity": arena_quantity,
                    "arena_go_submit_quantity_units": policy.arena_go_submit_quantity_units,
                    "provider_quantity_raw": provider_quantity,
                    "provider_order_value": provider_order_value,
                    "filled_quantity_basis": "order_value"
                    if provider_order_value is not None and provider_price
                    else policy.arena_go_submit_quantity_units,
                    "portfolio_update_hint": {
                        "remaining_cash": _optional_float(data.get("remaining_cash")) or 0.0,
                        "provider_price": provider_price or 0.0,
                        "provider_quantity": provider_quantity,
                        "filled_quantity": filled_quantity,
                    },
                    "portfolio_update_triggered": True,
                    "fill_ratio": fill_ratio(filled_quantity, order.quantity),
                    "reference_price": reference_price,
                    "broker_calls_only_via_gateway": True,
                },
            )
            order_status = self.order_status(
                order=order,
                status=status,
                status_ts=now,
                provider=policy.provider,
                provider_order_id=broker_order_id,
                payload={"execution_result_id": execution_result.execution_result_id, "route": "arena_go_gateway"},
            )
            status_ref = self.repository.save_order_status(order_status)
            fill_refs: tuple[str, ...] = ()
            if filled_quantity > 0 and provider_price is not None:
                fill = self.fill_report(
                    order=order,
                    fill_ts=now,
                    quantity=filled_quantity,
                    price=provider_price,
                    fees=fees,
                    provider_fill_id=stable_record_id("arena_go_fill", {"request_id": external_response.request_id}),
                    payload={
                        "execution_result_id": execution_result.execution_result_id,
                        "provider_tracking_id": external_response.provider_tracking_id,
                    },
                )
                fill_refs = (self.repository.save_fill_report(fill),)
            execution_result_ref = self.repository.save_execution_result(execution_result)
            audit_ref = self.repository.save_audit_record(
                self.audit_record(
                    job=job,
                    event_type="live_order_submitted",
                    severity="info",
                    object_type="execution_result",
                    object_ref=execution_result_ref,
                    reason_codes=(),
                    message="Approved order intent was submitted through External Request Gateway",
                    payload={
                        "order_intent_id": order.order_intent_id,
                        "execution_result_id": execution_result.execution_result_id,
                        "external_request_id": external_request.request_id,
                        "portfolio_update_triggered": True,
                        "source_module": self.module_name,
                        "calculation_version": CALCULATION_VERSION,
                        "timestamp": now,
                        "confidence_score": _confidence(execution_result.payload),
                    },
                )
            )
            return OrderExecutionOutcome(
                execution_result=execution_result,
                execution_result_ref=execution_result_ref,
                order_status_refs=(status_ref,),
                fill_report_refs=fill_refs,
                audit_refs=(audit_ref,),
            )

        return self.persist_provider_error_result(
            job=job,
            request=request,
            policy=policy,
            order=order,
            instrument_profile=instrument_profile,
            portfolio_snapshot=portfolio_snapshot,
            external_request=external_request,
            external_response_data=normalized,
            response_errors=response_errors or ("provider_error",),
            provider_tracking_id=external_response.provider_tracking_id,
            response_status=external_response.status,
        )

    def persist_provider_error_result(
        self,
        *,
        job: ModuleJob,
        request: ExecutionRequest,
        policy: ExecutionPolicy,
        order: OrderIntentRecord,
        instrument_profile: InstrumentProfileRecord,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
        external_request: ExternalRequest,
        external_response_data: Mapping[str, Any],
        response_errors: tuple[str, ...],
        provider_tracking_id: str,
        response_status: str,
    ) -> OrderExecutionOutcome:
        error_codes = tuple(dict.fromkeys(self.normalized_error_codes(response_errors, response_status)))
        result_status = "failed" if "provider_timeout" in error_codes else "rejected"
        now = to_utc_iso(utc_now())
        execution_result = self.execution_result(
            request=request,
            policy=policy,
            order_id=order.order_intent_id,
            status=result_status,
            broker_order_id=provider_tracking_id or None,
            submitted_at=now,
            last_update_at=now,
            filled_quantity=0.0,
            avg_fill_price=None,
            fees=0.0,
            slippage=None,
            errors=error_codes,
            payload={
                "execution_mode": "live_trading",
                "route": "arena_go_gateway",
                "external_request": external_request.to_dict(),
                "external_response": dict(external_response_data),
                "provider_response_status": response_status,
                **self.provider_error_payload(error_codes),
                "portfolio_update_hint": self.provider_error_portfolio_hint(error_codes),
                "portfolio_update_triggered": "insufficient_cash" in error_codes,
                "arena_go_secid": instrument_profile.arena_go_secid,
                "portfolio_snapshot_id": portfolio_snapshot.portfolio_snapshot_id if portfolio_snapshot else None,
            },
        )
        execution_result_ref = self.repository.save_execution_result(execution_result)
        order_status_ref = self.repository.save_order_status(
            self.order_status(
                order=order,
                status=result_status,
                status_ts=now,
                provider=policy.provider,
                provider_order_id=provider_tracking_id or None,
                payload={"execution_result_id": execution_result.execution_result_id, "errors": list(error_codes)},
            )
        )
        audit_ref = self.repository.save_audit_record(
            self.audit_record(
                job=job,
                event_type="live_order_rejected",
                severity="error" if result_status == "failed" else "warning",
                object_type="execution_result",
                object_ref=execution_result_ref,
                reason_codes=error_codes,
                message="ArenaGo Gateway response blocked or rejected the order",
                payload={
                    "order_intent_id": order.order_intent_id,
                    "execution_result_id": execution_result.execution_result_id,
                    "external_request_id": external_request.request_id,
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "timestamp": now,
                    "confidence_score": _confidence(execution_result.payload),
                },
            )
        )
        return OrderExecutionOutcome(
            execution_result=execution_result,
            execution_result_ref=execution_result_ref,
            order_status_refs=(order_status_ref,),
            audit_refs=(audit_ref,),
        )

    def persist_blocked_result(
        self,
        *,
        job: ModuleJob,
        request: ExecutionRequest,
        policy: ExecutionPolicy,
        order_id: str,
        status: str,
        errors: tuple[str, ...],
        audit_event_type: str,
        audit_message: str,
        order: OrderIntentRecord | None = None,
        instrument_profile: InstrumentProfileRecord | None = None,
        portfolio_snapshot: PortfolioSnapshotRecord | None = None,
    ) -> OrderExecutionOutcome:
        now = to_utc_iso(utc_now())
        execution_result = self.execution_result(
            request=request,
            policy=policy,
            order_id=order_id,
            status=status,
            broker_order_id=None,
            submitted_at=None,
            last_update_at=now,
            filled_quantity=0.0,
            avg_fill_price=None,
            fees=0.0,
            slippage=None,
            errors=errors,
            payload={
                "execution_mode": request.run_mode,
                "route": "blocked_before_broker",
                "no_execution_without_risk_approval": "risk_check_result_not_approved" in errors
                or "risk_check_result_missing" in errors
                or "order_intent_not_approved_by_risk" in errors,
                "broker_calls_only_via_gateway": True,
                "portfolio_update_triggered": False,
                "market_session_status_ref": request.market_session_status_ref,
                "instrument_id": order.instrument_id if order else None,
                "instrument_profile_ref": f"registry.instrument_profile:{instrument_profile.instrument_id}" if instrument_profile else None,
                "portfolio_snapshot_id": portfolio_snapshot.portfolio_snapshot_id if portfolio_snapshot else None,
            },
        )
        execution_result_ref = self.repository.save_execution_result(execution_result)
        order_status_refs: tuple[str, ...] = ()
        if order is not None:
            order_status_refs = (
                self.repository.save_order_status(
                    self.order_status(
                        order=order,
                        status=status,
                        status_ts=now,
                        provider=policy.provider if request.run_mode == "live_trading" else None,
                        provider_order_id=None,
                        payload={"execution_result_id": execution_result.execution_result_id, "errors": list(errors)},
                    )
                ),
            )
        audit_ref = self.repository.save_audit_record(
            self.audit_record(
                job=job,
                event_type=audit_event_type,
                severity="error" if status == "failed" else "warning",
                object_type="execution_result",
                object_ref=execution_result_ref,
                reason_codes=errors,
                message=audit_message,
                payload={
                    "order_intent_id": order_id,
                    "execution_result_id": execution_result.execution_result_id,
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "timestamp": now,
                    "confidence_score": _confidence(execution_result.payload),
                },
            )
        )
        return OrderExecutionOutcome(
            execution_result=execution_result,
            execution_result_ref=execution_result_ref,
            order_status_refs=order_status_refs,
            audit_refs=(audit_ref,),
        )

    def arena_go_submit_order_request(
        self,
        request: ExecutionRequest,
        job: ModuleJob,
        policy: ExecutionPolicy,
        order: OrderIntentRecord,
        instrument_profile: InstrumentProfileRecord,
        quantity: int,
    ) -> ExternalRequest:
        request_id = stable_record_id(
            "arena_go_submit_order",
            {
                "order_intent_id": order.order_intent_id,
                "idempotency_key": request.idempotency_key,
                "provider": policy.provider,
            },
        )
        return ExternalRequest(
            request_id=request_id,
            caller_module=self.module_name,
            provider=policy.provider,
            request_type="submit_order",
            universe_id=job.universe_id,
            instrument_ids=(order.instrument_id,),
            payload={
                "direction": "B" if order.side == "buy" else "S",
                "secid": instrument_profile.arena_go_secid or "",
                "quantity": quantity,
                "bot": policy.bot_name,
                "quantity_units": policy.arena_go_submit_quantity_units,
                "order_quantity_shares": order.quantity,
                "lot_size": instrument_profile.lot_size or 1,
                "position_effect": (order.payload or {}).get("position_effect"),
            },
            cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
            timeout_ms=policy.gateway_timeout_ms,
            retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
            idempotency_key=f"{request.idempotency_key}:{order.order_intent_id}",
        )

    def side_for_position_effect(self, position_effect: str) -> str | None:
        if position_effect in {"open_long", "increase_long", "reduce_short", "close_short"}:
            return "buy"
        if position_effect in {"reduce_long", "close_long", "open_short", "increase_short"}:
            return "sell"
        return None

    def execution_result(
        self,
        *,
        request: ExecutionRequest,
        policy: ExecutionPolicy,
        order_id: str,
        status: str,
        broker_order_id: str | None,
        submitted_at: str | None,
        last_update_at: str,
        filled_quantity: float,
        avg_fill_price: float | None,
        fees: float,
        slippage: float | None,
        errors: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> ExecutionResultRecord:
        base_payload = {
            "source_module": self.module_name,
            "calculation_version": f"{CALCULATION_VERSION}:{policy.execution_policy_id}",
            "timestamp": last_update_at,
            "confidence_score": 1.0 if not errors else 0.0,
            "execution_policy_id": request.execution_policy_id,
            "idempotency_key": request.idempotency_key,
        }
        result_payload = {**base_payload, **dict(payload)}
        return ExecutionResultRecord(
            execution_result_id=self.execution_result_id(order_id, request.idempotency_key),
            order_intent_id=order_id,
            status=status,
            broker_order_id=broker_order_id,
            submitted_at=submitted_at,
            last_update_at=last_update_at,
            filled_quantity=filled_quantity,
            avg_fill_price=avg_fill_price,
            fees=fees,
            slippage_bps=slippage,
            errors=errors,
            payload=result_payload,
        )

    def order_status(
        self,
        *,
        order: OrderIntentRecord,
        status: str,
        status_ts: str,
        provider: str | None,
        provider_order_id: str | None,
        payload: Mapping[str, Any],
    ) -> OrderStatusRecord:
        enriched_payload = self.output_payload(payload, status_ts)
        return OrderStatusRecord(
            order_intent_id=order.order_intent_id,
            status=status,
            status_ts=status_ts,
            provider=provider,
            provider_order_id=provider_order_id,
            payload=enriched_payload,
        )

    def fill_report(
        self,
        *,
        order: OrderIntentRecord,
        fill_ts: str,
        quantity: float,
        price: float,
        fees: float,
        provider_fill_id: str,
        payload: Mapping[str, Any],
    ) -> FillReportRecord:
        enriched_payload = self.output_payload(payload, fill_ts)
        return FillReportRecord(
            order_intent_id=order.order_intent_id,
            provider_fill_id=provider_fill_id,
            fill_ts=fill_ts,
            filled_quantity=quantity,
            fill_price=price,
            fees=fees,
            payload=enriched_payload,
        )

    def audit_record(
        self,
        *,
        job: ModuleJob,
        event_type: str,
        severity: str,
        message: str,
        object_type: str,
        object_ref: str,
        reason_codes: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> AuditRecord:
        return AuditRecord(
            audit_record_id=stable_record_id(
                "audit_execution_engine",
                {
                    "job_id": job.job_id,
                    "event_type": event_type,
                    "object_ref": object_ref,
                    "reason_codes": reason_codes,
                },
            ),
            module_name=self.module_name,
            job_id=job.job_id,
            severity=severity,
            event_type=event_type,
            message=message,
            object_type=object_type,
            object_ref=object_ref,
            reason_codes=reason_codes,
            payload=dict(payload),
        )

    def output_payload(self, payload: Mapping[str, Any], timestamp: str) -> dict[str, Any]:
        return {
            "source_module": self.module_name,
            "calculation_version": CALCULATION_VERSION,
            "timestamp": timestamp,
            "confidence_score": 1.0,
            **dict(payload),
        }

    def execution_result_id(self, order_id: str, idempotency_key: str) -> str:
        return stable_record_id(
            "execution_result",
            {
                "order_intent_id": order_id,
                "idempotency_key": idempotency_key,
            },
        )

    def module_result_status(self, statuses: tuple[str, ...]) -> str:
        if not statuses:
            return "skipped"
        if all(status in SUCCESS_STATUSES for status in statuses):
            return "success"
        if any(status in SUCCESS_STATUSES for status in statuses):
            return "partial_success"
        if all(status in TERMINAL_FAILURE_STATUSES for status in statuses):
            return "partial_success"
        return "partial_success"

    def aggregate_confidence(self, execution_results: tuple[ExecutionResultRecord, ...]) -> float:
        if not execution_results:
            return 1.0
        values = [_confidence(item.payload) for item in execution_results]
        return max(0.0, min(1.0, sum(values) / len(values)))

    def risk_approves_order(self, order: OrderIntentRecord, risk_result: RiskCheckResultRecord) -> bool:
        approved_tails = {ref_tail(ref) for ref in risk_result.approved_order_intents}
        return order.order_intent_id in approved_tails

    def order_ttl_expired(self, order: OrderIntentRecord, as_of_ts: str) -> bool:
        created_at = parse_utc_iso(order.created_at)
        as_of = parse_utc_iso(as_of_ts)
        ttl = order.execution_ttl_seconds or 0
        return (as_of - created_at).total_seconds() >= ttl

    def portfolio_snapshot_stale(
        self,
        portfolio_snapshot: PortfolioSnapshotRecord,
        as_of_ts: str,
        policy: ExecutionPolicy,
    ) -> bool:
        snapshot_ts = parse_utc_iso(portfolio_snapshot.as_of_ts)
        as_of = parse_utc_iso(as_of_ts)
        return (as_of - snapshot_ts).total_seconds() > policy.portfolio_snapshot_ttl_seconds

    def market_session_status(
        self,
        request: ExecutionRequest,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> str | None:
        for source in (
            _payload_text(order.payload, "market_session_status"),
            _payload_text(_nested_mapping(order.payload, "risk_metrics"), "market_session_status"),
            _payload_text(risk_result.payload if risk_result else {}, "market_session_status"),
            _payload_text(portfolio_snapshot.payload if portfolio_snapshot else {}, "market_session_status"),
            ref_tail(request.market_session_status_ref),
            current_market_session().market_session_status,
        ):
            if source:
                return source.strip().lower()
        return None

    def global_kill_switch_enabled(
        self,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> bool:
        for payload in (
            order.payload,
            risk_result.payload if risk_result else {},
            portfolio_snapshot.payload if portfolio_snapshot else {},
        ):
            for key in ("global_kill_switch", "execution_kill_switch", "bot_execution_blocked"):
                if _payload_bool(payload, key):
                    return True
        return False

    def data_quality_score(
        self,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> float | None:
        for payload in (order.payload, risk_result.payload if risk_result else {}, portfolio_snapshot.payload if portfolio_snapshot else {}):
            value = _payload_float(payload, "data_quality_score")
            if value is not None:
                return value
            value = _payload_float(payload, "confidence_score")
            if value is not None:
                return value
        return None

    def metric_value(
        self,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        metric_name: str,
    ) -> float | None:
        for payload in (
            order.payload,
            _nested_mapping(order.payload, "risk_metrics"),
            risk_result.payload if risk_result else {},
        ):
            value = _payload_float(payload, metric_name)
            if value is not None:
                return value
        return None

    def reference_price(
        self,
        order: OrderIntentRecord,
        risk_result: RiskCheckResultRecord | None,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
    ) -> float | None:
        if order.limit_price is not None and order.limit_price > 0:
            return order.limit_price
        for key in ("reference_price", "latest_price", "market_price", "last_price", "price"):
            for payload in (
                order.payload,
                _nested_mapping(order.payload, "risk_metrics"),
                risk_result.payload if risk_result else {},
                portfolio_snapshot.payload if portfolio_snapshot else {},
            ):
                value = _payload_float(payload, key)
                if value is not None and value > 0:
                    return value
        return None

    def portfolio_update_hint(
        self,
        order: OrderIntentRecord,
        portfolio_snapshot: PortfolioSnapshotRecord | None,
        price: float,
        quantity: float,
    ) -> dict[str, float]:
        cash = portfolio_snapshot.cash if portfolio_snapshot else None
        signed_value = price * quantity * (1 if order.side == "buy" else -1)
        remaining_cash = float(cash) - signed_value if cash is not None else 0.0
        return {
            "remaining_cash": remaining_cash,
            "provider_price": price,
            "provider_quantity": quantity,
        }

    def live_success_status(self, provider_quantity: float, submitted_quantity: float) -> str:
        if provider_quantity <= 0:
            return "submitted"
        if provider_quantity < submitted_quantity:
            return "partially_filled"
        return "filled"

    def live_filled_quantity(
        self,
        *,
        provider_quantity: float,
        provider_price: float | None,
        provider_order_value: float | None,
        instrument_profile: InstrumentProfileRecord,
        policy: ExecutionPolicy,
    ) -> float:
        if provider_order_value is not None and provider_price is not None and provider_price > 0:
            return max(0.0, provider_order_value / provider_price)
        lot_size = instrument_profile.lot_size if instrument_profile.lot_size and instrument_profile.lot_size > 0 else 1
        if policy.arena_go_submit_quantity_units == "lots":
            return max(0.0, provider_quantity * lot_size)
        return max(0.0, provider_quantity)

    def normalized_error_codes(self, errors: tuple[str, ...], response_status: str) -> tuple[str, ...]:
        normalized: list[str] = []
        for error in errors:
            text = str(error)
            lower = text.lower()
            if text in {
                "market_closed",
                "invalid_instrument",
                "insufficient_cash",
                "daily_trade_limit_reached",
                "provider_timeout",
            }:
                normalized.append(text)
            elif "market closed" in lower:
                normalized.append("market_closed")
            elif "not valid secid" in lower or "invalid_instrument" in lower:
                normalized.append("invalid_instrument")
            elif "insufficient cash" in lower:
                normalized.append("insufficient_cash")
            elif "daily trade limit" in lower:
                normalized.append("daily_trade_limit_reached")
            elif response_status == "timeout" or "timeout" in lower:
                normalized.append("provider_timeout")
            else:
                normalized.append("provider_rejected")
        if not normalized and response_status == "timeout":
            normalized.append("provider_timeout")
        return tuple(dict.fromkeys(normalized or ["provider_rejected"]))

    def provider_error_payload(self, error_codes: tuple[str, ...]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if "market_closed" in error_codes:
            payload["market_session_status"] = "closed"
            payload["retry_allowed"] = False
        if "invalid_instrument" in error_codes:
            payload["alert"] = "registry_validation_required"
            payload["registry_validation_requested"] = True
        if "insufficient_cash" in error_codes:
            payload["portfolio_refresh_requested"] = True
        if "daily_trade_limit_reached" in error_codes:
            payload["execution_kill_switch"] = "bot_block_until_next_trading_day"
        if "provider_timeout" in error_codes:
            payload["retry_allowed"] = False
            payload["retry_blocked_without_idempotency_confirmation"] = True
        return payload

    def provider_error_portfolio_hint(self, error_codes: tuple[str, ...]) -> dict[str, Any]:
        if "insufficient_cash" in error_codes:
            return {"refresh_required": True}
        return {}

    def _failed_result(self, job: ModuleJob, started_at: str, error: Exception) -> ExecutionEngineRunResult:
        audit_ref: tuple[str, ...] = ()
        try:
            audit_ref = (
                self.repository.save_audit_record(
                    AuditRecord(
                        audit_record_id=stable_record_id(
                            "audit_execution_engine_failed",
                            {"job_id": job.job_id, "error": str(error), "calculation_version": CALCULATION_VERSION},
                        ),
                        module_name=self.module_name,
                        job_id=job.job_id,
                        severity="error",
                        event_type="execution_engine_failed",
                        message=str(error),
                        object_type="module_job",
                        object_ref=f"audit.module_job:{job.job_id}",
                        reason_codes=("execution_engine_failed",),
                        payload={"calculation_version": CALCULATION_VERSION, "source_module": self.module_name},
                    )
                ),
            )
        except Exception:
            audit_ref = ()
        return ExecutionEngineRunResult(
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
            execution_results=(),
            audit_refs=audit_ref,
        )

    def _missing_job_result(self, started_at: str) -> ExecutionEngineRunResult:
        audit_ref: tuple[str, ...] = ()
        try:
            audit_ref = (
                self.repository.save_audit_record(
                    AuditRecord(
                        audit_record_id=stable_record_id(
                            "audit_execution_engine_missing_job",
                            {"module_name": self.module_name, "error": "module_job_required"},
                        ),
                        module_name=self.module_name,
                        job_id="missing",
                        severity="error",
                        event_type="execution_engine_failed",
                        message=f"{self.module_name} requires module_job",
                        object_type="module_job",
                        object_ref="audit.module_job:missing",
                        reason_codes=("module_job_required",),
                        payload={"calculation_version": CALCULATION_VERSION, "source_module": self.module_name},
                    )
                ),
            )
        except Exception:
            audit_ref = ()
        return ExecutionEngineRunResult(
            module_job_result=ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=audit_ref,
                errors=(f"{self.module_name} requires module_job",),
                data_quality_score=0.0,
            ),
            execution_results=(),
            audit_refs=audit_ref,
        )


def _payload_float(payload: Mapping[str, Any], key: str) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(key)
    return _optional_float(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_text(payload: Mapping[str, Any], key: str) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    return _optional_text(payload.get(key))


def _payload_bool(payload: Mapping[str, Any], key: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    value = payload.get(key)
    return _coerce_bool(value)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return _coerce_bool(value)


def _nested_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key) if isinstance(payload, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _confidence(payload: Mapping[str, Any]) -> float:
    value = _payload_float(payload, "confidence_score")
    if value is None:
        return 0.0
    return max(0.0, min(1.0, value))
