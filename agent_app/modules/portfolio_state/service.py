from __future__ import annotations

import os
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from typing import Any, Mapping

from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, ModuleJob, ModuleJobResult, RetryPolicy
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)

from .gateway import PortfolioGatewayError, request_via_gateway
from .metrics import (
    apply_fill_to_position,
    available_risk_budget,
    daily_pnl,
    drawdown,
    equity_value,
    gross_exposure,
    instrument_exposure,
    net_exposure,
    position_market_value,
    sector_exposure,
    unrealized_pnl,
)
from .repository import (
    AuditRecord,
    FillReportRecord,
    InMemoryPortfolioStateRepository,
    OrderIntentRecord,
    PortfolioSnapshotRecord,
    PortfolioStateRepository,
    PositionStateRecord,
    RawMarketPriceRecord,
    ref_tail,
    stable_record_id,
)


MODULE_NAME = "Portfolio State Module"
CALCULATION_VERSION = "portfolio_state_v1"
VALID_CONTOURS = {"execution_contour", "daily_contour", "decision_contour"}
VALID_RUN_MODES = {"analysis_only", "paper_trading", "live_trading"}
INPUT_FIELDS = {
    "portfolio_id",
    "fill_report_refs",
    "broker_snapshot_ref",
    "price_snapshot_ref",
    "run_mode",
    "as_of_ts",
}
DEFAULT_PORTFOLIO_ID = "arena_go_default"


class PortfolioStateError(ValueError):
    """Raised when module 20 would violate its documented contract."""


@dataclass(frozen=True)
class PortfolioConfig:
    initial_capital_rub: float = 1_000_000.0
    currency: str = "RUB"
    reconciliation_ttl_seconds: int = 300
    mismatch_threshold_cash: float = 1_000.0
    mismatch_threshold_quantity: float = 0.01
    gateway_timeout_ms: int = 10_000
    total_risk_budget: float = 1.0
    used_risk_budget: float = 0.0
    arena_go_bot_name: str = ""
    arena_go_portfolio: str = ""
    arena_go_position_units: str = "lots"
    arena_go_trade_quantity_units: str = "lots"
    target_gross_turnover_rub_14d: float = 10_000_000.0
    turnover_window_days: int = 14


    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None = None) -> "PortfolioConfig":
        payload = payload or {}
        return cls(
            initial_capital_rub=_float(payload.get("initial_capital_rub"))
            or _float(os.getenv("INITIAL_CAPITAL_RUB"))
            or 1_000_000.0,
            currency=str(payload.get("currency") or "RUB"),
            reconciliation_ttl_seconds=int(payload.get("reconciliation_ttl_seconds") or 300),
            mismatch_threshold_cash=_float(payload.get("mismatch_threshold_cash"))
            or _float(os.getenv("PORTFOLIO_CASH_MISMATCH_THRESHOLD_RUB"))
            or 1_000.0,
            mismatch_threshold_quantity=_float(payload.get("mismatch_threshold_quantity"))
            or _float(os.getenv("PORTFOLIO_QUANTITY_MISMATCH_THRESHOLD"))
            or 0.01,
            gateway_timeout_ms=int(payload.get("gateway_timeout_ms") or 10_000),
            total_risk_budget=_float(payload.get("total_risk_budget")) or 1.0,
            used_risk_budget=_float(payload.get("used_risk_budget")) or 0.0,
            arena_go_bot_name=str(payload.get("arena_go_bot_name") or ""),
            arena_go_portfolio=str(payload.get("arena_go_portfolio") or ""),
            arena_go_position_units=str(
                payload.get("arena_go_position_units")
                or os.getenv("ARENA_GO_POSITION_UNITS")
                or "lots"
            ).strip().lower(),
            arena_go_trade_quantity_units=str(
                payload.get("arena_go_trade_quantity_units")
                or os.getenv("ARENA_GO_TRADE_QUANTITY_UNITS")
                or "lots"
            ).strip().lower(),
            target_gross_turnover_rub_14d=_float(payload.get("target_gross_turnover_rub_14d")) or _float(os.getenv("TARGET_GROSS_TURNOVER_RUB_14D")) or 10_000_000.0,
            turnover_window_days=int(payload.get("turnover_window_days") or os.getenv("TURNOVER_WINDOW_DAYS", "14")),
        )

    @property
    def bot_name(self) -> str:
        return self.arena_go_bot_name or os.getenv("ARENA_GO_BOT_NAME") or os.getenv("ARENA_GO_PORTFOLIO") or ""

    @property
    def provider_portfolio(self) -> str:
        return self.arena_go_portfolio or os.getenv("ARENA_GO_PORTFOLIO", DEFAULT_PORTFOLIO_ID)


@dataclass(frozen=True)
class PortfolioUpdateRequest:
    portfolio_id: str
    fill_report_refs: tuple[str, ...]
    broker_snapshot_ref: str
    price_snapshot_ref: str
    run_mode: str
    as_of_ts: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "PortfolioUpdateRequest":
        extra_top_level = sorted(set(payload) - {"portfolio_update_request"})
        if extra_top_level:
            raise PortfolioStateError(f"payload has undocumented fields: {extra_top_level}")
        request_payload = payload.get("portfolio_update_request")
        if not isinstance(request_payload, Mapping):
            raise PortfolioStateError("payload must contain portfolio_update_request")
        missing_fields = sorted(INPUT_FIELDS - set(request_payload))
        if missing_fields:
            raise PortfolioStateError(f"portfolio_update_request missing required fields: {missing_fields}")
        extra_fields = sorted(set(request_payload) - INPUT_FIELDS)
        if extra_fields:
            raise PortfolioStateError(f"portfolio_update_request has undocumented fields: {extra_fields}")

        run_mode = str(request_payload.get("run_mode") or "")
        if run_mode not in VALID_RUN_MODES:
            raise PortfolioStateError("portfolio_update_request.run_mode is invalid")
        if run_mode != job.run_mode:
            raise PortfolioStateError("portfolio_update_request.run_mode must match module_job.run_mode")

        portfolio_id = str(request_payload.get("portfolio_id") or "")
        broker_snapshot_ref = str(request_payload.get("broker_snapshot_ref") or "")
        price_snapshot_ref = str(request_payload.get("price_snapshot_ref") or "")
        as_of_ts = str(request_payload.get("as_of_ts") or "")
        fill_refs_payload = request_payload.get("fill_report_refs")
        if not isinstance(fill_refs_payload, (list, tuple)):
            raise PortfolioStateError("portfolio_update_request.fill_report_refs must be a list")
        fill_report_refs = tuple(str(item) for item in fill_refs_payload if str(item or ""))

        missing_text = [
            name
            for name, value in {
                "portfolio_id": portfolio_id,
                "broker_snapshot_ref": broker_snapshot_ref,
                "price_snapshot_ref": price_snapshot_ref,
                "as_of_ts": as_of_ts,
            }.items()
            if not value
        ]
        if missing_text:
            raise PortfolioStateError(f"portfolio_update_request missing text fields: {missing_text}")
        parse_utc_iso(as_of_ts)

        if tuple(job.input_refs):
            ref_tails = {ref_tail(ref) for ref in job.input_refs}
            required_refs = {
                "broker_snapshot_ref": broker_snapshot_ref,
                "price_snapshot_ref": price_snapshot_ref,
                **{f"fill_report_refs[{index}]": ref for index, ref in enumerate(fill_report_refs)},
            }
            missing_refs = [
                name
                for name, value in required_refs.items()
                if ref_tail(value) not in ref_tails
            ]
            if missing_refs:
                raise PortfolioStateError(f"portfolio_update_request refs must be present in module_job.input_refs: {missing_refs}")

        return cls(
            portfolio_id=portfolio_id,
            fill_report_refs=fill_report_refs,
            broker_snapshot_ref=broker_snapshot_ref,
            price_snapshot_ref=price_snapshot_ref,
            run_mode=run_mode,
            as_of_ts=as_of_ts,
        )


@dataclass(frozen=True)
class BrokerSyncResult:
    status: str
    cash_balance: float | None
    positions: tuple[Mapping[str, Any], ...]
    trades: tuple[Mapping[str, Any], ...]
    errors: tuple[str, ...]
    request_refs: tuple[str, ...]
    portfolio_id: str = ""


@dataclass(frozen=True)
class PortfolioBuildResult:
    snapshot: PortfolioSnapshotRecord
    positions: tuple[PositionStateRecord, ...]
    reconciliation_report: Mapping[str, Any]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioStateRunResult:
    module_job_result: ModuleJobResult
    portfolio_snapshot: PortfolioSnapshotRecord | None
    positions: tuple[PositionStateRecord, ...]
    reconciliation_report: Mapping[str, Any]
    portfolio_snapshot_ref: str | None = None
    position_state_refs: tuple[str, ...] = ()
    audit_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "portfolio_snapshot": self.portfolio_snapshot.to_contract() if self.portfolio_snapshot else None,
            "positions": [position.to_contract() for position in self.positions],
            "portfolio_snapshot_ref": self.portfolio_snapshot_ref,
            "position_state_refs": list(self.position_state_refs),
            "portfolio_reconciliation_report": dict(self.reconciliation_report),
            "audit_refs": list(self.audit_refs),
        }


class PortfolioStateService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: PortfolioStateRepository | None = None,
        gateway: Any = None,
        config: PortfolioConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.repository = repository or InMemoryPortfolioStateRepository()
        self.gateway = gateway
        self.config = config if isinstance(config, PortfolioConfig) else PortfolioConfig.from_mapping(config)

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> PortfolioStateRunResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> PortfolioStateRunResult:
        return self.execute(payload, job)

    def execute(self, payload: Mapping[str, Any], job: ModuleJob | None) -> PortfolioStateRunResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            return self._missing_job_result(started_at)
        try:
            self.validate_module_job(job)
            request = PortfolioUpdateRequest.from_dict(payload, job)
            previous_snapshot = self.repository.get_previous_snapshot(request.portfolio_id, request.as_of_ts)
            broker_sync = self.sync_with_broker(request, job, previous_snapshot)
            if request.run_mode == "live_trading" and broker_sync.portfolio_id:
                canonical_request = replace(request, portfolio_id=broker_sync.portfolio_id)
                if canonical_request.portfolio_id != request.portfolio_id:
                    previous_snapshot = self.repository.get_previous_snapshot(canonical_request.portfolio_id, canonical_request.as_of_ts)
                    request = canonical_request
            if self.live_snapshot_forbidden(request, previous_snapshot, broker_sync):
                return self._stale_live_failure(job, request, started_at, broker_sync)

            build = self.build_portfolio_state(request, job, previous_snapshot, broker_sync)
            snapshot_ref = self.repository.save_portfolio_snapshot(build.snapshot)
            position_refs = tuple(self.repository.save_position_state(position) for position in build.positions)
            audit_ref = self.repository.save_audit_record(
                self.audit_record(
                    job=job,
                    event_type="portfolio_state_updated",
                    severity="warning" if build.warnings else "info",
                    object_type="portfolio_snapshot",
                    object_ref=snapshot_ref,
                    reason_codes=build.warnings,
                    message="Portfolio state updated from fills, market prices and broker reconciliation",
                    payload={
                        "portfolio_snapshot_id": build.snapshot.portfolio_snapshot_id,
                        "portfolio_id": request.portfolio_id,
                        "reconciliation_report": dict(build.reconciliation_report),
                        "source_module": self.module_name,
                        "calculation_version": CALCULATION_VERSION,
                        "timestamp": request.as_of_ts,
                        "confidence_score": _confidence(build.snapshot.payload),
                    },
                )
            )
            output_refs = (snapshot_ref, *position_refs, audit_ref)
            status = "partial_success" if build.warnings else "success"
            return PortfolioStateRunResult(
                module_job_result=ModuleJobResult(
                    job_id=job.job_id,
                    module_name=self.module_name,
                    status=status,
                    started_at=started_at,
                    finished_at=to_utc_iso(utc_now()),
                    output_refs=output_refs,
                    warnings=build.warnings,
                    errors=(),
                    metrics_written=1 + len(position_refs),
                    events_written=1,
                    data_quality_score=_confidence(build.snapshot.payload),
                ),
                portfolio_snapshot=build.snapshot,
                positions=build.positions,
                reconciliation_report=build.reconciliation_report,
                portfolio_snapshot_ref=snapshot_ref,
                position_state_refs=position_refs,
                audit_refs=(audit_ref,),
            )
        except (PortfolioStateError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise PortfolioStateError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise PortfolioStateError("module_job.module_name must be Portfolio State Module")
        if job.contour not in VALID_CONTOURS:
            raise PortfolioStateError("module_job.contour is not valid for Portfolio State Module")
        if not job.input_refs:
            raise PortfolioStateError("module_job.input_refs is required")
        if job.run_mode not in VALID_RUN_MODES:
            raise PortfolioStateError("module_job.run_mode must be analysis_only, paper_trading, or live_trading")

    def sync_with_broker(
        self,
        request: PortfolioUpdateRequest,
        job: ModuleJob,
        previous_snapshot: PortfolioSnapshotRecord | None,
    ) -> BrokerSyncResult:
        del previous_snapshot
        if request.run_mode != "live_trading":
            return BrokerSyncResult(
                status="not_required",
                cash_balance=None,
                positions=(),
                trades=(),
                errors=(),
                request_refs=(),
                portfolio_id=request.portfolio_id,
            )
        responses = []
        request_refs: list[str] = []
        errors: list[str] = []
        bots_request = self.broker_external_request("get_bots", request, job)
        try:
            bots_response = request_via_gateway(self.gateway, bots_request)
            request_refs.append(f"request_logs.external_response:{bots_response.request_id}")
            responses.append(("get_bots", bots_response))
            if bots_response.status not in {"success", "partial_success"}:
                errors.extend(bots_response.errors or ("get_bots_failed",))
        except PortfolioGatewayError as error:
            errors.append(str(error))
        if errors:
            return BrokerSyncResult(
                status="failed",
                cash_balance=None,
                positions=(),
                trades=(),
                errors=tuple(dict.fromkeys(errors)),
                request_refs=tuple(request_refs),
                portfolio_id=request.portfolio_id,
            )

        bots_payload = self.response_items(self.response_data(responses, "get_bots"))
        portfolio_name = self.resolve_arena_go_portfolio(bots_payload)
        if not portfolio_name:
            return BrokerSyncResult(
                status="failed",
                cash_balance=None,
                positions=(),
                trades=(),
                errors=("arena_go_bot_not_found",),
                request_refs=tuple(request_refs),
                portfolio_id=request.portfolio_id,
            )
        for request_type in ("get_positions", "get_trades"):
            external_request = self.broker_external_request(request_type, request, job, portfolio_name=portfolio_name)
            try:
                response = request_via_gateway(self.gateway, external_request)
                request_refs.append(f"request_logs.external_response:{response.request_id}")
                responses.append((request_type, response))
                if response.status not in {"success", "partial_success"}:
                    errors.extend(response.errors or (f"{request_type}_failed",))
            except PortfolioGatewayError as error:
                errors.append(str(error))
        if errors:
            return BrokerSyncResult(
                status="failed",
                cash_balance=None,
                positions=(),
                trades=(),
                errors=tuple(dict.fromkeys(errors)),
                request_refs=tuple(request_refs),
                portfolio_id=portfolio_name,
            )
        positions_payload = self.response_items(self.response_data(responses, "get_positions"))
        trades_payload = self.response_items(self.response_data(responses, "get_trades"))
        cash_balance = self.bot_cash_balance(bots_payload, portfolio_name=portfolio_name)
        return BrokerSyncResult(
            status="success",
            cash_balance=cash_balance,
            positions=positions_payload,
            trades=trades_payload,
            errors=(),
            request_refs=tuple(request_refs),
            portfolio_id=portfolio_name,
        )

    def build_portfolio_state(
        self,
        request: PortfolioUpdateRequest,
        job: ModuleJob,
        previous_snapshot: PortfolioSnapshotRecord | None,
        broker_sync: BrokerSyncResult,
    ) -> PortfolioBuildResult:
        previous_positions = self.repository.list_previous_positions(request.portfolio_id, request.as_of_ts)
        applied_fill_refs = set(_string_list(previous_snapshot.payload.get("applied_fill_refs")) if previous_snapshot else ())
        source_refs = [request.price_snapshot_ref, request.broker_snapshot_ref, *request.fill_report_refs, *broker_sync.request_refs]
        cash = previous_snapshot.cash if previous_snapshot else self.config.initial_capital_rub
        realized = previous_snapshot.realized_pnl if previous_snapshot else 0.0
        initial_capital = previous_snapshot.initial_capital_rub if previous_snapshot else self.config.initial_capital_rub
        positions = {position.instrument_id: position for position in previous_positions}
        position_quantities = {key: value.quantity for key, value in positions.items()}
        position_avg_prices = {key: value.average_price for key, value in positions.items()}
        position_payloads = {key: dict(value.payload) for key, value in positions.items()}
        warnings: list[str] = []
        missing_fills: list[str] = []
        newly_applied_fills: list[str] = []

        fill_contexts = self.load_fill_contexts(request.fill_report_refs)
        for fill_ref, fill, order in fill_contexts:
            if fill is None or order is None:
                missing_fills.append(fill_ref)
                continue
            fill_key = ref_tail(fill_ref)
            if fill_key in applied_fill_refs:
                continue
            instrument_id = self.fill_instrument_id(fill, order)
            side = self.fill_side(fill, order)
            if not instrument_id or side not in {"buy", "sell"}:
                warnings.append("fill_context_incomplete")
                continue
            current_qty = position_quantities.get(instrument_id, 0.0)
            current_avg = position_avg_prices.get(instrument_id)
            new_qty, new_avg, realized_delta = apply_fill_to_position(
                current_quantity=current_qty,
                current_average_price=current_avg,
                fill_side=side,
                fill_quantity=fill.filled_quantity,
                fill_price=fill.fill_price,
                fees=fill.fees,
            )
            position_quantities[instrument_id] = new_qty
            position_avg_prices[instrument_id] = new_avg
            position_payloads.setdefault(instrument_id, {})
            position_payloads[instrument_id]["last_fill_ref"] = fill_ref
            position_payloads[instrument_id]["last_fill_ts"] = fill.fill_ts
            cash += self.cash_delta(side, fill)
            realized += realized_delta
            applied_fill_refs.add(fill_key)
            newly_applied_fills.append(fill_ref)

        if missing_fills:
            warnings.append("fill_report_missing")

        broker_instrument_ids = tuple(
            sorted(
                {
                    instrument_id
                    for instrument_id in (
                        *(self.broker_position_instrument_id(position) for position in broker_sync.positions),
                        *(self.broker_trade_instrument_id(trade) for trade in broker_sync.trades),
                        *position_quantities.keys(),
                    )
                    if instrument_id
                }
            )
        )
        lot_sizes = dict(self.repository.get_instrument_lot_sizes(broker_instrument_ids))
        broker_report = self.apply_broker_reconciliation(
            request=request,
            cash=cash,
            realized_pnl=realized,
            quantities=position_quantities,
            average_prices=position_avg_prices,
            payloads=position_payloads,
            broker_sync=broker_sync,
            previous_snapshot=previous_snapshot,
            lot_sizes=lot_sizes,
        )
        cash = broker_report["cash"]
        realized = broker_report["realized_pnl"]
        reconciliation_report = broker_report["report"]
        warnings.extend(broker_report["warnings"])

        instrument_ids = tuple(sorted(position_quantities))
        price_records = self.repository.get_latest_market_prices(instrument_ids, job.universe_id, request.as_of_ts)
        position_records = self.mark_to_market_positions(
            request=request,
            quantities=position_quantities,
            average_prices=position_avg_prices,
            payloads=position_payloads,
            price_records=price_records,
            lot_sizes=lot_sizes,
            source_refs=tuple(source_refs),
        )
        if any(position.market_price is None for position in position_records if abs(position.quantity) > 0):
            warnings.append("market_price_missing")

        values = [position.market_value for position in position_records]
        equity = equity_value(cash, values)
        unrealized = sum(position.unrealized_pnl for position in position_records)
        gross = gross_exposure(values, equity)
        net = net_exposure(values, equity)
        previous_peak = None
        if previous_snapshot is not None:
            previous_peak = _float(previous_snapshot.payload.get("rolling_peak_equity"))
            if previous_peak is None:
                previous_peak = previous_snapshot.equity
        peak_equity = max(equity, previous_peak if previous_peak is not None else equity)
        start_equity = _float(previous_snapshot.payload.get("start_of_day_equity")) if previous_snapshot else None
        if start_equity is None:
            start_equity = previous_snapshot.equity if previous_snapshot else self.config.initial_capital_rub
        stale = (
            self.snapshot_stale(previous_snapshot, request.as_of_ts)
            if previous_snapshot and broker_sync.status != "success"
            else False
        )
        if stale:
            warnings.append("stale_portfolio_detected")
        inconsistent = "reconciliation_mismatch_exceeds_threshold" in warnings
        confidence = self.data_quality_score(warnings, broker_sync, request.run_mode)
        state_source = self.snapshot_source(previous_snapshot, broker_sync)
        turnover_profile = self.turnover_profile(
            request=request,
            previous_snapshot=previous_snapshot,
            broker_sync=broker_sync,
            newly_applied_fills=tuple(newly_applied_fills),
            fill_contexts=fill_contexts,
            lot_sizes=lot_sizes,
        )
        snapshot_payload = {
            "source_module": self.module_name,
            "calculation_version": CALCULATION_VERSION,
            "timestamp": request.as_of_ts,
            "confidence_score": confidence,
            "data_quality_score": confidence,
            "currency": self.config.currency,
            "source": state_source,
            "initial_seed_created": state_source == "initial_seed",
            "initial_capital_rub": initial_capital,
            "run_mode": request.run_mode,
            **turnover_profile,
            "positions": [position.to_contract() for position in position_records],
            "applied_fill_refs": sorted(applied_fill_refs),
            "newly_applied_fill_refs": newly_applied_fills,
            "fills_applied_once": True,
            "portfolio_reconciliation_report": dict(reconciliation_report),
            "broker_reconciliation_supported": True,
            "position_state_versioned": True,
            "ttl_status": "stale" if stale or inconsistent else "fresh",
            "portfolio_state_stale_or_inconsistent": stale or inconsistent,
            "daily_pnl": daily_pnl(equity, start_equity),
            "drawdown": drawdown(equity, peak_equity),
            "rolling_peak_equity": peak_equity,
            "start_of_day_equity": start_equity,
            "available_risk_budget": available_risk_budget(self.config.total_risk_budget, self.config.used_risk_budget),
            "instrument_exposure": {
                position.instrument_id: instrument_exposure(position.market_value, equity)
                for position in position_records
            },
            "sector_exposure": sector_exposure((position.payload for position in position_records), equity),
        }
        snapshot = PortfolioSnapshotRecord(
            portfolio_snapshot_id=stable_record_id(
                "portfolio_snapshot",
                {
                    "portfolio_id": request.portfolio_id,
                    "as_of_ts": request.as_of_ts,
                    "job_id": job.job_id,
                },
            ),
            portfolio_id=request.portfolio_id,
            universe_id=job.universe_id,
            as_of_ts=request.as_of_ts,
            initial_capital_rub=initial_capital,
            cash=cash,
            equity=equity,
            gross_exposure=gross,
            net_exposure=net,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            source_module=self.module_name,
            source_refs=tuple(dict.fromkeys(source_refs)),
            payload=snapshot_payload,
        )
        return PortfolioBuildResult(
            snapshot=snapshot,
            positions=position_records,
            reconciliation_report=reconciliation_report,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def turnover_profile(
        self,
        *,
        request: PortfolioUpdateRequest,
        previous_snapshot: PortfolioSnapshotRecord | None,
        broker_sync: BrokerSyncResult,
        newly_applied_fills: tuple[str, ...],
        fill_contexts: tuple[tuple[str, FillReportRecord | None, OrderIntentRecord | None], ...],
        lot_sizes: Mapping[str, int],
    ) -> dict[str, Any]:
        target = max(0.0, self.config.target_gross_turnover_rub_14d)
        window_days = max(1, self.config.turnover_window_days)
        recent_fills = self.repository.list_recent_fill_reports(request.portfolio_id, request.as_of_ts, window_days)
        recent_fill_ids = {fill.fill_report_id for fill in recent_fills}
        current_fills = tuple(
            fill
            for fill_ref, fill, _order in fill_contexts
            if fill is not None and fill_ref in newly_applied_fills and fill.fill_report_id not in recent_fill_ids
        )
        recent_turnover = sum(abs(fill.filled_quantity * fill.fill_price) for fill in recent_fills)
        current_fill_turnover = sum(abs(fill.filled_quantity * fill.fill_price) for fill in current_fills)
        recent_turnover += current_fill_turnover
        broker_turnover = self.broker_gross_turnover(broker_sync.trades, request.as_of_ts, window_days, lot_sizes)
        if request.run_mode == "live_trading" and broker_turnover > recent_turnover:
            recent_turnover = broker_turnover
        previous_payload = dict(previous_snapshot.payload) if previous_snapshot else {}
        as_of_dt = parse_utc_iso(request.as_of_ts)
        one_day_turnover = sum(
            abs(fill.filled_quantity * fill.fill_price)
            for fill in (*recent_fills, *current_fills)
            if fill.fill_ts and parse_utc_iso(fill.fill_ts).date() == as_of_dt.date()
        )
        if request.run_mode == "live_trading" and broker_sync.trades:
            broker_1d = self.broker_gross_turnover(broker_sync.trades, request.as_of_ts, 1, lot_sizes)
            one_day_turnover = max(one_day_turnover, broker_1d)
        window_started_at = str(previous_payload.get("turnover_window_started_at") or request.as_of_ts)
        try:
            elapsed_days = (as_of_dt.date() - parse_utc_iso(window_started_at).date()).days + 1
        except Exception:
            window_started_at = request.as_of_ts
            elapsed_days = 1
        elapsed_days = max(1, min(window_days, elapsed_days))
        remaining_days = max(1, window_days - elapsed_days + 1)
        remaining = max(0.0, target - recent_turnover)
        progress = 1.0 if target <= 0 else min(1.0, recent_turnover / target)
        required_daily = remaining / remaining_days
        average_daily_turnover = recent_turnover / elapsed_days
        projected = average_daily_turnover * window_days
        expected_progress = 1.0 if target <= 0 else min(1.0, elapsed_days / window_days)
        if progress >= 1.0:
            status = "achieved"
        elif progress < expected_progress * 0.65:
            status = "critically_behind"
        elif progress < expected_progress * 0.90:
            status = "behind"
        else:
            status = "on_track"
        initial_capital = _float(previous_payload.get("initial_capital_rub")) or self.config.initial_capital_rub
        ratio = recent_turnover / initial_capital if initial_capital > 0 else 0.0
        return {
            "turnover_mandate_enabled": True,
            "target_gross_turnover_rub_14d": target,
            "turnover_window_days": window_days,
            "gross_turnover_rub_1d": one_day_turnover,
            "gross_turnover_rub_14d": recent_turnover,
            "turnover_ratio_14d": ratio,
            "turnover_progress_ratio": progress,
            "target_completion_pct": progress * 100.0,
            "remaining_turnover_rub_14d": remaining,
            "required_daily_turnover_rub": required_daily,
            "average_daily_turnover_rub": average_daily_turnover,
            "projected_turnover_rub_14d": projected,
            "expected_turnover_progress_ratio": expected_progress,
            "turnover_window_started_at": window_started_at,
            "turnover_elapsed_days": elapsed_days,
            "turnover_remaining_days": remaining_days,
            "turnover_target_status": status,
            "turnover_source": "arena_go_trades" if request.run_mode == "live_trading" and broker_turnover >= recent_turnover and broker_turnover > 0 else "fill_reports",
        }

    def broker_gross_turnover(
        self,
        trades: tuple[Mapping[str, Any], ...],
        as_of_ts: str,
        window_days: int,
        lot_sizes: Mapping[str, int] | None = None,
    ) -> float:
        as_of = parse_utc_iso(as_of_ts)
        window_seconds = max(1, int(window_days)) * 86_400
        total = 0.0
        for trade in trades:
            trade_ts = self.broker_trade_timestamp(trade)
            if trade_ts is not None:
                age = (as_of - trade_ts).total_seconds()
                if age < 0 or age > window_seconds:
                    continue
            instrument_id = self.broker_trade_instrument_id(trade)
            lot_size = self.instrument_lot_size(instrument_id, lot_sizes or {})
            quantity = _float(trade.get("quantity") or trade.get("filled_quantity") or trade.get("qty")) or 0.0
            if self.config.arena_go_trade_quantity_units == "lots":
                quantity *= lot_size
            price = _float(trade.get("price") or trade.get("avg_fill_price") or trade.get("fill_price")) or 0.0
            total += abs(quantity * price)
        return total

    def broker_trade_timestamp(self, trade: Mapping[str, Any]):
        for key in ("timestamp", "trade_ts", "fill_ts", "datetime", "created_at"):
            value = trade.get(key)
            if value:
                try:
                    return parse_utc_iso(str(value))
                except Exception:
                    pass
        tradedate = str(trade.get("tradedate") or trade.get("trade_date") or "").strip()
        tradetime = str(trade.get("tradetime") or trade.get("trade_time") or "00:00:00").strip()
        if tradedate:
            candidate = f"{tradedate}T{tradetime}"
            if candidate.endswith("Z"):
                candidate = candidate[:-1]
            try:
                return datetime.fromisoformat(candidate).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def same_trading_day(self, snapshot: PortfolioSnapshotRecord | None, as_of_ts: str) -> bool:
        if snapshot is None or not snapshot.as_of_ts:
            return False
        return parse_utc_iso(snapshot.as_of_ts).date() == parse_utc_iso(as_of_ts).date()

    def load_fill_contexts(
        self,
        fill_report_refs: tuple[str, ...],
    ) -> tuple[tuple[str, FillReportRecord | None, OrderIntentRecord | None], ...]:
        contexts: list[tuple[str, FillReportRecord | None, OrderIntentRecord | None]] = []
        for fill_ref in fill_report_refs:
            fill = self.repository.get_fill_report(fill_ref)
            order = self.repository.get_order_intent(fill.order_intent_id) if fill else None
            contexts.append((fill_ref, fill, order))
        return tuple(contexts)

    def apply_broker_reconciliation(
        self,
        *,
        request: PortfolioUpdateRequest,
        cash: float,
        realized_pnl: float,
        quantities: dict[str, float],
        average_prices: dict[str, float | None],
        payloads: dict[str, dict[str, Any]],
        broker_sync: BrokerSyncResult,
        previous_snapshot: PortfolioSnapshotRecord | None,
        lot_sizes: Mapping[str, int],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        report: dict[str, Any] = {
            "status": broker_sync.status,
            "broker_snapshot_ref": request.broker_snapshot_ref,
            "cash_mismatch": 0.0,
            "position_mismatches": [],
            "broker_errors": list(broker_sync.errors),
            "trades_seen": len(broker_sync.trades),
            "broker_realized_pnl": None,
        }
        if request.run_mode != "live_trading" or broker_sync.status != "success":
            if request.run_mode == "live_trading":
                warnings.append("broker_reconciliation_failed")
            return {"cash": cash, "realized_pnl": realized_pnl, "report": report, "warnings": tuple(warnings)}

        first_broker_sync = previous_snapshot is None
        if broker_sync.cash_balance is not None:
            cash_mismatch = broker_sync.cash_balance - cash
            report["cash_mismatch"] = 0.0 if first_broker_sync else cash_mismatch
            if first_broker_sync:
                report["cash_source"] = "arena_go_initial_sync"
            elif abs(cash_mismatch) > self.config.mismatch_threshold_cash:
                warnings.append("reconciliation_mismatch_exceeds_threshold")
            cash = broker_sync.cash_balance

        broker_instruments_seen: set[str] = set()
        for broker_position in broker_sync.positions:
            instrument_id = self.broker_position_instrument_id(broker_position)
            if not instrument_id:
                continue
            lot_size = self.instrument_lot_size(instrument_id, lot_sizes)
            broker_units = _float(broker_position.get("position")) or _float(broker_position.get("quantity")) or 0.0
            direction = str(broker_position.get("direction") or broker_position.get("side") or "").strip().lower()
            direction_sign = -1.0 if direction in {"s", "sell", "short"} else 1.0
            broker_qty = broker_units * lot_size if self.config.arena_go_position_units == "lots" else broker_units
            broker_qty *= direction_sign
            broker_avg = _float(broker_position.get("average_price") or broker_position.get("avg_price"))
            internal_qty = quantities.get(instrument_id, 0.0)
            mismatch = broker_qty - internal_qty
            broker_instruments_seen.add(instrument_id)
            if first_broker_sync:
                mismatch = 0.0
            elif abs(mismatch) > self.config.mismatch_threshold_quantity:
                warnings.append("reconciliation_mismatch_exceeds_threshold")
                report["position_mismatches"].append(
                    {
                        "instrument_id": instrument_id,
                        "internal_quantity": internal_qty,
                        "broker_quantity": broker_qty,
                        "quantity_mismatch": mismatch,
                    }
                )
            quantities[instrument_id] = broker_qty
            if broker_avg is not None:
                average_prices[instrument_id] = broker_avg
            payloads.setdefault(instrument_id, {})
            payloads[instrument_id]["broker_reconciled"] = True
            payloads[instrument_id]["broker_position_closed"] = False
            payloads[instrument_id]["broker_position"] = dict(broker_position)
            payloads[instrument_id]["broker_quantity_raw"] = broker_units
            payloads[instrument_id]["broker_direction"] = direction or None
            payloads[instrument_id]["broker_quantity_units"] = self.config.arena_go_position_units
            payloads[instrument_id]["lot_size"] = lot_size
        for instrument_id in list(quantities):
            if instrument_id in broker_instruments_seen:
                continue
            if payloads.get(instrument_id, {}).get("broker_reconciled"):
                quantities[instrument_id] = 0.0
                average_prices[instrument_id] = None
                payloads[instrument_id]["broker_position_closed"] = True
                payloads[instrument_id]["broker_quantity_raw"] = 0.0
                payloads[instrument_id]["broker_position"] = {}
                payloads[instrument_id]["broker_quantity_units"] = self.config.arena_go_position_units
        report["status"] = "mismatch" if warnings else "matched"
        broker_realized_pnl = self.broker_realized_pnl(broker_sync.trades)
        if broker_realized_pnl is not None:
            report["broker_realized_pnl"] = broker_realized_pnl
            realized_pnl = broker_realized_pnl
        return {
            "cash": cash,
            "realized_pnl": realized_pnl,
            "report": report,
            "warnings": tuple(dict.fromkeys(warnings)),
        }

    def mark_to_market_positions(
        self,
        *,
        request: PortfolioUpdateRequest,
        quantities: Mapping[str, float],
        average_prices: Mapping[str, float | None],
        payloads: Mapping[str, Mapping[str, Any]],
        price_records: Mapping[str, RawMarketPriceRecord],
        lot_sizes: Mapping[str, int],
        source_refs: tuple[str, ...],
    ) -> tuple[PositionStateRecord, ...]:
        records: list[PositionStateRecord] = []
        for instrument_id in sorted(quantities):
            quantity = quantities[instrument_id]
            average_price = average_prices.get(instrument_id)
            price_record = price_records.get(instrument_id)
            market_price = price_record.price if price_record and price_record.price > 0 else average_price
            market_value = position_market_value(quantity, market_price)
            position_unrealized = unrealized_pnl(quantity, average_price, market_price)
            payload = dict(payloads.get(instrument_id) or {})
            lot_size = self.instrument_lot_size(instrument_id, lot_sizes)
            payload.update(
                {
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "timestamp": request.as_of_ts,
                    "confidence_score": 1.0 if market_price is not None else 0.0,
                    "market_price_ref": price_record.source_ref if price_record else None,
                    "price_snapshot_ref": request.price_snapshot_ref,
                    "instrument_exposure_source": "portfolio_state",
                    "market_value": market_value,
                    "lot_size": lot_size,
                    "quantity_units": "shares",
                }
            )
            records.append(
                PositionStateRecord(
                    position_state_id=stable_record_id(
                        "position_state",
                        {
                            "portfolio_id": request.portfolio_id,
                            "instrument_id": instrument_id,
                            "as_of_ts": request.as_of_ts,
                        },
                    ),
                    portfolio_id=request.portfolio_id,
                    instrument_id=instrument_id,
                    as_of_ts=request.as_of_ts,
                    quantity=quantity,
                    average_price=average_price,
                    market_price=market_price,
                    market_value=market_value,
                    unrealized_pnl=position_unrealized,
                    source_module=self.module_name,
                    source_refs=source_refs,
                    payload=payload,
                )
            )
        return tuple(records)

    def broker_external_request(
        self,
        request_type: str,
        request: PortfolioUpdateRequest,
        job: ModuleJob,
        *,
        portfolio_name: str | None = None,
    ) -> ExternalRequest:
        request_id = stable_record_id(
            f"arena_go_{request_type}",
            {
                "portfolio_id": request.portfolio_id,
                "as_of_ts": request.as_of_ts,
                "job_id": job.job_id,
            },
        )
        return ExternalRequest(
            request_id=request_id,
            caller_module=self.module_name,
            provider="arena_go",
            request_type=request_type,
            universe_id=job.universe_id,
            instrument_ids=job.instrument_ids,
            payload={
                "portfolio": portfolio_name or self.config.provider_portfolio,
                "bot": portfolio_name or self.config.bot_name,
            },
            cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
            timeout_ms=self.config.gateway_timeout_ms,
            retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
            idempotency_key=f"{job.idempotency_key}:{request_type}:{request.portfolio_id}:{request.as_of_ts}",
        )

    def live_snapshot_forbidden(
        self,
        request: PortfolioUpdateRequest,
        previous_snapshot: PortfolioSnapshotRecord | None,
        broker_sync: BrokerSyncResult,
    ) -> bool:
        if request.run_mode != "live_trading" or broker_sync.status == "success":
            return False
        if previous_snapshot is None:
            return True
        return self.snapshot_stale(previous_snapshot, request.as_of_ts)

    def snapshot_stale(self, snapshot: PortfolioSnapshotRecord | None, as_of_ts: str) -> bool:
        if snapshot is None:
            return True
        age = (parse_utc_iso(as_of_ts) - parse_utc_iso(snapshot.as_of_ts)).total_seconds()
        return age > self.config.reconciliation_ttl_seconds

    def fill_instrument_id(self, fill: FillReportRecord, order: OrderIntentRecord) -> str:
        return str(fill.payload.get("instrument_id") or order.instrument_id or "")

    def fill_side(self, fill: FillReportRecord, order: OrderIntentRecord) -> str:
        side = str(fill.payload.get("side") or order.side or "").lower()
        if side in {"b", "buy"}:
            return "buy"
        if side in {"s", "sell"}:
            return "sell"
        return side

    def cash_delta(self, side: str, fill: FillReportRecord) -> float:
        value = fill.filled_quantity * fill.fill_price
        if side == "buy":
            return -value - fill.fees
        if side == "sell":
            return value - fill.fees
        return 0.0

    def data_quality_score(
        self,
        warnings: list[str],
        broker_sync: BrokerSyncResult,
        run_mode: str,
    ) -> float:
        score = 1.0
        if "fill_report_missing" in warnings:
            score -= 0.25
        if "market_price_missing" in warnings:
            score -= 0.25
        if "reconciliation_mismatch_exceeds_threshold" in warnings:
            score = min(score, 0.25)
        if run_mode == "live_trading" and broker_sync.status != "success":
            score = min(score, 0.25)
        if "stale_portfolio_detected" in warnings:
            score = min(score, 0.25)
        return max(0.0, min(1.0, score))

    def response_data(self, responses: list[tuple[str, Any]], request_type: str) -> Mapping[str, Any]:
        for current_type, response in responses:
            if current_type == request_type:
                return dict(response.data or {})
        return {}

    def response_items(self, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        data = payload.get("data")
        if isinstance(data, Mapping):
            payload = data
        for key in ("items", "positions", "trades", "bots", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return tuple(dict(item) for item in value if isinstance(item, Mapping))
        if isinstance(payload, Mapping) and any(key in payload for key in ("secid", "name", "cash_balance")):
            return (dict(payload),)
        return ()

    def resolve_arena_go_portfolio(self, bots: tuple[Mapping[str, Any], ...]) -> str:
        names = [str(bot.get("name") or bot.get("bot") or "").strip() for bot in bots]
        names = [name for name in names if name]
        preferred = (self.config.provider_portfolio, self.config.bot_name)
        placeholders = {"", DEFAULT_PORTFOLIO_ID.lower(), "mybot", "mytradingbot", "portfolio"}
        for candidate in preferred:
            text = str(candidate or "").strip()
            if text and text in names:
                return text
        for candidate in preferred:
            text = str(candidate or "").strip().lower()
            if text and text not in placeholders:
                for name in names:
                    if name.lower() == text:
                        return name
        return names[0] if len(names) == 1 else ""

    def bot_cash_balance(self, bots: tuple[Mapping[str, Any], ...], *, portfolio_name: str | None = None) -> float | None:
        configured_bot = portfolio_name or self.config.bot_name
        for bot in bots:
            if str(bot.get("name") or bot.get("bot") or "") == configured_bot:
                value = _float(bot.get("cash_balance") or bot.get("cash"))
                if value is not None:
                    return value
        for bot in bots:
            value = _float(bot.get("cash_balance") or bot.get("cash"))
            if value is not None:
                return value
        return None

    def broker_position_instrument_id(self, position: Mapping[str, Any]) -> str:
        return self.canonical_instrument_id(position.get("instrument_id") or position.get("secid"))

    def broker_trade_instrument_id(self, trade: Mapping[str, Any]) -> str:
        return self.canonical_instrument_id(trade.get("instrument_id") or trade.get("secid"))

    def canonical_instrument_id(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if ":" in text:
            return text
        return f"moex:{text.upper()}"

    def instrument_lot_size(self, instrument_id: str, lot_sizes: Mapping[str, int]) -> int:
        ticker = instrument_id.rsplit(":", 1)[-1] if ":" in instrument_id else instrument_id
        for key in (instrument_id, ticker, f"moex:{ticker}" if ticker else ""):
            value = lot_sizes.get(key)
            if value and value > 0:
                return int(value)
        return 1

    def broker_realized_pnl(self, trades: tuple[Mapping[str, Any], ...]) -> float | None:
        values = []
        for trade in trades:
            value = _float(trade.get("realized_pnl"))
            if value is None:
                value = _float(trade.get("pnl"))
            if value is not None:
                values.append(value)
        if not values:
            return None
        return sum(values)

    def snapshot_source(
        self,
        previous_snapshot: PortfolioSnapshotRecord | None,
        broker_sync: BrokerSyncResult,
    ) -> str:
        if broker_sync.status == "success":
            return "broker_reconciled"
        if previous_snapshot is None:
            return "initial_seed"
        return "portfolio_update"

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
                "audit_portfolio_state",
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

    def _stale_live_failure(
        self,
        job: ModuleJob,
        request: PortfolioUpdateRequest,
        started_at: str,
        broker_sync: BrokerSyncResult,
    ) -> PortfolioStateRunResult:
        audit_ref = self.repository.save_audit_record(
            self.audit_record(
                job=job,
                event_type="portfolio_state_stale_or_inconsistent",
                severity="error",
                object_type="portfolio_snapshot",
                object_ref=f"portfolio.portfolio_snapshot:{request.portfolio_id}",
                reason_codes=("portfolio_state_stale_or_inconsistent", *broker_sync.errors),
                message="Live portfolio snapshot was not refreshed because ArenaGo sync failed and TTL expired",
                payload={
                    "portfolio_id": request.portfolio_id,
                    "broker_snapshot_ref": request.broker_snapshot_ref,
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "timestamp": request.as_of_ts,
                    "confidence_score": 0.0,
                    "broker_reconciliation_supported": True,
                    "stale_portfolio_detected": True,
                },
            )
        )
        return PortfolioStateRunResult(
            module_job_result=ModuleJobResult(
                job_id=job.job_id,
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=(audit_ref,),
                warnings=("portfolio_state_stale_or_inconsistent",),
                errors=broker_sync.errors,
                metrics_written=0,
                events_written=1,
                data_quality_score=0.0,
            ),
            portfolio_snapshot=None,
            positions=(),
            reconciliation_report={"status": "failed", "errors": list(broker_sync.errors)},
            audit_refs=(audit_ref,),
        )

    def _failed_result(self, job: ModuleJob, started_at: str, error: Exception) -> PortfolioStateRunResult:
        audit_ref: tuple[str, ...] = ()
        try:
            audit_ref = (
                self.repository.save_audit_record(
                    AuditRecord(
                        audit_record_id=stable_record_id(
                            "audit_portfolio_state_failed",
                            {"job_id": job.job_id, "error": str(error), "calculation_version": CALCULATION_VERSION},
                        ),
                        module_name=self.module_name,
                        job_id=job.job_id,
                        severity="error",
                        event_type="portfolio_state_failed",
                        message=str(error),
                        object_type="module_job",
                        object_ref=f"audit.module_job:{job.job_id}",
                        reason_codes=("portfolio_state_failed",),
                        payload={"calculation_version": CALCULATION_VERSION, "source_module": self.module_name},
                    )
                ),
            )
        except Exception:
            audit_ref = ()
        return PortfolioStateRunResult(
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
            portfolio_snapshot=None,
            positions=(),
            reconciliation_report={},
            audit_refs=audit_ref,
        )

    def _missing_job_result(self, started_at: str) -> PortfolioStateRunResult:
        audit_ref: tuple[str, ...] = ()
        try:
            audit_ref = (
                self.repository.save_audit_record(
                    AuditRecord(
                        audit_record_id=stable_record_id(
                            "audit_portfolio_state_missing_job",
                            {"module_name": self.module_name, "error": "module_job_required"},
                        ),
                        module_name=self.module_name,
                        job_id="missing",
                        severity="error",
                        event_type="portfolio_state_failed",
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
        return PortfolioStateRunResult(
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
            portfolio_snapshot=None,
            positions=(),
            reconciliation_report={},
            audit_refs=audit_ref,
        )


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence(payload: Mapping[str, Any]) -> float:
    value = _float(payload.get("confidence_score") if isinstance(payload, Mapping) else None)
    if value is None:
        return 0.0
    return max(0.0, min(1.0, value))


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    try:
        iterator = iter(value)
    except TypeError:
        return [str(value)]
    return [str(item) for item in iterator if item not in (None, "")]
