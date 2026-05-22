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
    mismatch_threshold_cash: float = 1.0
    mismatch_threshold_quantity: float = 0.000001
    gateway_timeout_ms: int = 10_000
    total_risk_budget: float = 1.0
    used_risk_budget: float = 0.0
    arena_go_bot_name: str = ""
    arena_go_portfolio: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None = None) -> "PortfolioConfig":
        payload = payload or {}
        return cls(
            initial_capital_rub=_float(payload.get("initial_capital_rub"))
            or _float(os.getenv("INITIAL_CAPITAL_RUB"))
            or 1_000_000.0,
            currency=str(payload.get("currency") or "RUB"),
            reconciliation_ttl_seconds=int(payload.get("reconciliation_ttl_seconds") or 300),
            mismatch_threshold_cash=_float(payload.get("mismatch_threshold_cash")) or 1.0,
            mismatch_threshold_quantity=_float(payload.get("mismatch_threshold_quantity")) or 0.000001,
            gateway_timeout_ms=int(payload.get("gateway_timeout_ms") or 10_000),
            total_risk_budget=_float(payload.get("total_risk_budget")) or 1.0,
            used_risk_budget=_float(payload.get("used_risk_budget")) or 0.0,
            arena_go_bot_name=str(payload.get("arena_go_bot_name") or ""),
            arena_go_portfolio=str(payload.get("arena_go_portfolio") or ""),
        )

    @property
    def bot_name(self) -> str:
        return self.arena_go_bot_name or os.getenv("ARENA_GO_BOT_NAME", "MyTradingBot")

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
            )
        responses = []
        request_refs: list[str] = []
        errors: list[str] = []
        for request_type in ("get_bots", "get_positions", "get_trades"):
            external_request = self.broker_external_request(request_type, request, job)
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
            )

        bots_payload = self.response_items(self.response_data(responses, "get_bots"))
        positions_payload = self.response_items(self.response_data(responses, "get_positions"))
        trades_payload = self.response_items(self.response_data(responses, "get_trades"))
        cash_balance = self.bot_cash_balance(bots_payload)
        return BrokerSyncResult(
            status="success",
            cash_balance=cash_balance,
            positions=positions_payload,
            trades=trades_payload,
            errors=(),
            request_refs=tuple(request_refs),
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

        broker_report = self.apply_broker_reconciliation(
            request=request,
            cash=cash,
            realized_pnl=realized,
            quantities=position_quantities,
            average_prices=position_avg_prices,
            payloads=position_payloads,
            broker_sync=broker_sync,
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
        stale = self.snapshot_stale(previous_snapshot, request.as_of_ts) if previous_snapshot else False
        if stale:
            warnings.append("stale_portfolio_detected")
        inconsistent = "reconciliation_mismatch_exceeds_threshold" in warnings
        confidence = self.data_quality_score(warnings, broker_sync, request.run_mode)
        state_source = self.snapshot_source(previous_snapshot, broker_sync)
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

        if broker_sync.cash_balance is not None:
            cash_mismatch = broker_sync.cash_balance - cash
            report["cash_mismatch"] = cash_mismatch
            if abs(cash_mismatch) > self.config.mismatch_threshold_cash:
                warnings.append("reconciliation_mismatch_exceeds_threshold")
            cash = broker_sync.cash_balance

        for broker_position in broker_sync.positions:
            instrument_id = self.broker_position_instrument_id(broker_position)
            if not instrument_id:
                continue
            broker_qty = _float(broker_position.get("position")) or _float(broker_position.get("quantity")) or 0.0
            broker_avg = _float(broker_position.get("average_price") or broker_position.get("avg_price"))
            internal_qty = quantities.get(instrument_id, 0.0)
            mismatch = broker_qty - internal_qty
            if abs(mismatch) > self.config.mismatch_threshold_quantity:
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
            payloads[instrument_id]["broker_position"] = dict(broker_position)
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
                "portfolio": self.config.provider_portfolio,
                "bot": self.config.bot_name,
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

    def bot_cash_balance(self, bots: tuple[Mapping[str, Any], ...]) -> float | None:
        configured_bot = self.config.bot_name
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
        return str(position.get("instrument_id") or position.get("secid") or "").strip()

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
