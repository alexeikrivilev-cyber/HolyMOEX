from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class OrderIntentRecord:
    order_intent_id: str
    instrument_id: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None
    time_in_force: str
    max_slippage_bps: float | None
    execution_ttl_seconds: int | None
    decision_set_id: str | None
    risk_check_id: str | None
    run_mode: str
    created_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OrderIntentRecord":
        source_payload = _mapping(payload.get("payload"))
        return cls(
            order_intent_id=str(payload.get("order_intent_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            side=str(payload.get("side") or ""),
            quantity=_optional_float(payload.get("quantity")) or 0.0,
            order_type=str(payload.get("order_type") or ""),
            limit_price=_optional_float(payload.get("limit_price")),
            time_in_force=str(payload.get("time_in_force") or ""),
            max_slippage_bps=_optional_float(payload.get("max_slippage_bps")),
            execution_ttl_seconds=_optional_int(payload.get("execution_ttl_seconds")),
            decision_set_id=_optional_text(payload.get("decision_set_id")),
            risk_check_id=_optional_text(payload.get("risk_check_id")),
            run_mode=str(payload.get("run_mode") or ""),
            created_at=str(payload.get("created_at") or ""),
            payload=source_payload,
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "order_intent_id": self.order_intent_id,
            "instrument_id": self.instrument_id,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "time_in_force": self.time_in_force,
            "max_slippage_bps": self.max_slippage_bps,
            "execution_ttl_seconds": self.execution_ttl_seconds,
            "decision_set_id": self.decision_set_id,
            "risk_check_id": self.risk_check_id,
            "run_mode": self.run_mode,
        }


@dataclass(frozen=True)
class RiskCheckResultRecord:
    risk_check_id: str
    decision_set_id: str
    status: str
    approved_order_intents: tuple[str, ...]
    rejected_decisions: tuple[str, ...]
    risk_flags: tuple[str, ...]
    adjustments: tuple[Mapping[str, Any], ...]
    checked_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskCheckResultRecord":
        adjustments = payload.get("adjustments") or ()
        if not isinstance(adjustments, (list, tuple)):
            adjustments = ()
        return cls(
            risk_check_id=str(payload.get("risk_check_id") or payload.get("id") or ""),
            decision_set_id=str(payload.get("decision_set_id") or ""),
            status=str(payload.get("status") or ""),
            approved_order_intents=_string_tuple(payload.get("approved_order_intents")),
            rejected_decisions=_string_tuple(payload.get("rejected_decisions")),
            risk_flags=_string_tuple(payload.get("risk_flags")),
            adjustments=tuple(dict(item) for item in adjustments if isinstance(item, Mapping)),
            checked_at=str(payload.get("checked_at") or ""),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class InstrumentProfileRecord:
    instrument_id: str
    universe_id: str
    ticker: str
    lot_size: int | None
    tradable: bool
    execution_enabled: bool
    arena_go_secid: str | None
    arena_go_quantity_mode: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentProfileRecord":
        return cls(
            instrument_id=str(payload.get("instrument_id") or payload.get("id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            ticker=str(payload.get("ticker") or ""),
            lot_size=_optional_int(payload.get("lot_size")),
            tradable=bool(payload.get("tradable", True)),
            execution_enabled=bool(payload.get("execution_enabled", True)),
            arena_go_secid=_optional_text(payload.get("arena_go_secid")),
            arena_go_quantity_mode=str(payload.get("arena_go_quantity_mode") or "shares"),
            metadata=_mapping(payload.get("metadata")),
        )


@dataclass(frozen=True)
class PortfolioSnapshotRecord:
    portfolio_snapshot_id: str
    portfolio_id: str
    universe_id: str | None
    as_of_ts: str
    cash: float | None
    equity: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortfolioSnapshotRecord":
        return cls(
            portfolio_snapshot_id=str(payload.get("portfolio_snapshot_id") or payload.get("id") or ""),
            portfolio_id=str(payload.get("portfolio_id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            cash=_optional_float(payload.get("cash")),
            equity=_optional_float(payload.get("equity")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class OrderStatusRecord:
    order_intent_id: str
    status: str
    status_ts: str
    provider: str | None = None
    provider_order_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    order_status_id: str | None = None


@dataclass(frozen=True)
class FillReportRecord:
    order_intent_id: str
    fill_ts: str
    filled_quantity: float
    fill_price: float
    fees: float
    provider_fill_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    fill_report_id: str | None = None

    def to_contract(self) -> dict[str, Any]:
        return {
            "order_intent_id": self.order_intent_id,
            "provider_fill_id": self.provider_fill_id,
            "fill_ts": self.fill_ts,
            "filled_quantity": self.filled_quantity,
            "fill_price": self.fill_price,
            "fees": self.fees,
        }


@dataclass(frozen=True)
class ExecutionResultRecord:
    execution_result_id: str
    order_intent_id: str
    status: str
    broker_order_id: str | None
    submitted_at: str | None
    last_update_at: str
    filled_quantity: float
    avg_fill_price: float | None
    fees: float
    slippage_bps: float | None
    errors: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExecutionResultRecord":
        return cls(
            execution_result_id=str(payload.get("execution_result_id") or payload.get("id") or ""),
            order_intent_id=str(payload.get("order_intent_id") or ""),
            status=str(payload.get("status") or ""),
            broker_order_id=_optional_text(payload.get("broker_order_id")),
            submitted_at=_optional_text(payload.get("submitted_at")),
            last_update_at=str(payload.get("last_update_at") or ""),
            filled_quantity=_optional_float(payload.get("filled_quantity")) or 0.0,
            avg_fill_price=_optional_float(payload.get("avg_fill_price")),
            fees=_optional_float(payload.get("fees")) or 0.0,
            slippage_bps=_optional_float(payload.get("slippage_bps")),
            errors=_string_tuple(payload.get("errors")),
            payload=_mapping(payload.get("payload")),
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "execution_result_id": self.execution_result_id,
            "order_intent_id": self.order_intent_id,
            "status": self.status,
            "broker_order_id": self.broker_order_id,
            "submitted_at": self.submitted_at,
            "last_update_at": self.last_update_at,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "fees": self.fees,
            "slippage_bps": self.slippage_bps,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class AuditRecord:
    audit_record_id: str
    module_name: str
    job_id: str
    severity: str
    event_type: str
    message: str
    object_type: str
    object_ref: str
    reason_codes: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)


class ExecutionEngineRepository(Protocol):
    def get_order_intent(self, order_intent_ref: str) -> OrderIntentRecord | None:
        ...

    def get_risk_check_result(self, risk_check_ref: str) -> RiskCheckResultRecord | None:
        ...

    def get_instrument_profile(self, instrument_id: str) -> InstrumentProfileRecord | None:
        ...

    def get_latest_portfolio_snapshot(self, universe_id: str, as_of_ts: str) -> PortfolioSnapshotRecord | None:
        ...

    def get_execution_result(self, execution_result_id: str) -> ExecutionResultRecord | None:
        ...

    def save_order_status(self, order_status: OrderStatusRecord) -> str:
        ...

    def save_fill_report(self, fill_report: FillReportRecord) -> str:
        ...

    def save_execution_result(self, execution_result: ExecutionResultRecord) -> str:
        ...

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        ...


class InMemoryExecutionEngineRepository:
    def __init__(
        self,
        order_intents: tuple[Mapping[str, Any] | OrderIntentRecord, ...] = (),
        risk_check_results: tuple[Mapping[str, Any] | RiskCheckResultRecord, ...] = (),
        instrument_profiles: tuple[Mapping[str, Any] | InstrumentProfileRecord, ...] = (),
        portfolio_snapshots: tuple[Mapping[str, Any] | PortfolioSnapshotRecord, ...] = (),
        execution_results: tuple[Mapping[str, Any] | ExecutionResultRecord, ...] = (),
    ) -> None:
        self.order_intents = tuple(
            item if isinstance(item, OrderIntentRecord) else OrderIntentRecord.from_mapping(item)
            for item in order_intents
        )
        self.risk_check_results = tuple(
            item if isinstance(item, RiskCheckResultRecord) else RiskCheckResultRecord.from_mapping(item)
            for item in risk_check_results
        )
        self.instrument_profiles = tuple(
            item if isinstance(item, InstrumentProfileRecord) else InstrumentProfileRecord.from_mapping(item)
            for item in instrument_profiles
        )
        self.portfolio_snapshots = tuple(
            item if isinstance(item, PortfolioSnapshotRecord) else PortfolioSnapshotRecord.from_mapping(item)
            for item in portfolio_snapshots
        )
        self.saved_order_statuses: list[OrderStatusRecord] = []
        self.saved_fill_reports: list[FillReportRecord] = []
        self.saved_execution_results: list[ExecutionResultRecord] = [
            item if isinstance(item, ExecutionResultRecord) else ExecutionResultRecord.from_mapping(item)
            for item in execution_results
        ]
        self.saved_audit_records: list[AuditRecord] = []

    def get_order_intent(self, order_intent_ref: str) -> OrderIntentRecord | None:
        requested = ref_tail(order_intent_ref)
        if requested in {"", "latest", "scheduled"}:
            return sorted(self.order_intents, key=lambda item: (item.created_at, item.order_intent_id))[-1] if self.order_intents else None
        for order_intent in self.order_intents:
            if order_intent.order_intent_id == requested:
                return order_intent
        return None

    def get_risk_check_result(self, risk_check_ref: str) -> RiskCheckResultRecord | None:
        requested = ref_tail(risk_check_ref)
        if requested in {"", "latest", "scheduled"}:
            return sorted(self.risk_check_results, key=lambda item: (item.checked_at, item.risk_check_id))[-1] if self.risk_check_results else None
        for result in self.risk_check_results:
            if result.risk_check_id == requested:
                return result
        return None

    def get_instrument_profile(self, instrument_id: str) -> InstrumentProfileRecord | None:
        requested = instrument_id_tail(instrument_id)
        for profile in self.instrument_profiles:
            if profile.instrument_id == requested:
                return profile
        return None

    def get_latest_portfolio_snapshot(self, universe_id: str, as_of_ts: str) -> PortfolioSnapshotRecord | None:
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            snapshot
            for snapshot in self.portfolio_snapshots
            if snapshot.as_of_ts
            and parse_utc_iso(snapshot.as_of_ts) <= as_of
            and (not universe_id or snapshot.universe_id in {None, universe_id})
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item.as_of_ts, item.portfolio_snapshot_id))[-1]

    def get_execution_result(self, execution_result_id: str) -> ExecutionResultRecord | None:
        for result in self.saved_execution_results:
            if result.execution_result_id == execution_result_id:
                return result
        return None

    def save_order_status(self, order_status: OrderStatusRecord) -> str:
        order_status_id = order_status.order_status_id or stable_record_id(
            "order_status",
            {
                "order_intent_id": order_status.order_intent_id,
                "status": order_status.status,
                "status_ts": order_status.status_ts,
                "provider_order_id": order_status.provider_order_id,
            },
        )
        self.saved_order_statuses.append(order_status)
        return f"orders.order_status:{order_status_id}"

    def save_fill_report(self, fill_report: FillReportRecord) -> str:
        fill_report_id = fill_report.fill_report_id or stable_record_id(
            "fill_report",
            {
                "order_intent_id": fill_report.order_intent_id,
                "fill_ts": fill_report.fill_ts,
                "filled_quantity": fill_report.filled_quantity,
                "fill_price": fill_report.fill_price,
                "provider_fill_id": fill_report.provider_fill_id,
            },
        )
        self.saved_fill_reports.append(fill_report)
        return f"orders.fill_report:{fill_report_id}"

    def save_execution_result(self, execution_result: ExecutionResultRecord) -> str:
        self.saved_execution_results = [
            item
            for item in self.saved_execution_results
            if item.execution_result_id != execution_result.execution_result_id
        ]
        self.saved_execution_results.append(execution_result)
        return f"orders.execution_result:{execution_result.execution_result_id}"

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        self.saved_audit_records.append(audit_record)
        return f"audit.audit_record:{audit_record.audit_record_id}"


class PostgresExecutionEngineRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def get_order_intent(self, order_intent_ref: str) -> OrderIntentRecord | None:
        requested = ref_tail(order_intent_ref)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if requested in {"", "latest", "scheduled"}:
                    cur.execute(
                        """
                        SELECT order_intent_id, instrument_id, side, quantity, order_type,
                               limit_price, time_in_force, max_slippage_bps,
                               execution_ttl_seconds, decision_set_id, risk_check_id,
                               run_mode, created_at, payload
                          FROM orders.order_intent
                         ORDER BY created_at DESC, order_intent_id DESC
                         LIMIT 1
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT order_intent_id, instrument_id, side, quantity, order_type,
                               limit_price, time_in_force, max_slippage_bps,
                               execution_ttl_seconds, decision_set_id, risk_check_id,
                               run_mode, created_at, payload
                          FROM orders.order_intent
                         WHERE order_intent_id = %s
                        """,
                        (requested,),
                    )
                row = cur.fetchone()
        return _order_intent_from_row(row) if row else None

    def get_risk_check_result(self, risk_check_ref: str) -> RiskCheckResultRecord | None:
        requested = ref_tail(risk_check_ref)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if requested in {"", "latest", "scheduled"}:
                    cur.execute(
                        """
                        SELECT risk_check_id, decision_set_id, status,
                               approved_order_intents, rejected_decisions, risk_flags,
                               adjustments, checked_at, payload
                          FROM risk.risk_check_result
                         ORDER BY checked_at DESC, risk_check_id DESC
                         LIMIT 1
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT risk_check_id, decision_set_id, status,
                               approved_order_intents, rejected_decisions, risk_flags,
                               adjustments, checked_at, payload
                          FROM risk.risk_check_result
                         WHERE risk_check_id = %s
                        """,
                        (requested,),
                    )
                row = cur.fetchone()
        return _risk_check_result_from_row(row) if row else None

    def get_instrument_profile(self, instrument_id: str) -> InstrumentProfileRecord | None:
        requested = instrument_id_tail(instrument_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_id, universe_id, ticker, lot_size, tradable,
                           execution_enabled, arena_go_secid, arena_go_quantity_mode,
                           metadata
                      FROM registry.instrument_profile
                     WHERE instrument_id = %s
                    """,
                    (requested,),
                )
                row = cur.fetchone()
        return _instrument_profile_from_row(row) if row else None

    def get_latest_portfolio_snapshot(self, universe_id: str, as_of_ts: str) -> PortfolioSnapshotRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT portfolio_snapshot_id, portfolio_id, universe_id,
                           as_of_ts, cash, equity, payload
                      FROM portfolio.portfolio_snapshot
                     WHERE (%s = '' OR universe_id = %s OR universe_id IS NULL)
                       AND as_of_ts <= %s
                     ORDER BY as_of_ts DESC, portfolio_snapshot_id DESC
                     LIMIT 1
                    """,
                    (universe_id, universe_id, parse_utc_iso(as_of_ts)),
                )
                row = cur.fetchone()
        return _portfolio_snapshot_from_row(row) if row else None

    def get_execution_result(self, execution_result_id: str) -> ExecutionResultRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT execution_result_id, order_intent_id, status,
                           broker_order_id, submitted_at, last_update_at,
                           filled_quantity, avg_fill_price, fees, slippage_bps,
                           errors, payload
                      FROM orders.execution_result
                     WHERE execution_result_id = %s
                    """,
                    (execution_result_id,),
                )
                row = cur.fetchone()
        return _execution_result_from_row(row) if row else None

    def save_order_status(self, order_status: OrderStatusRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders.order_status (
                        order_intent_id, status, status_ts, provider,
                        provider_order_id, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING order_status_id
                    """,
                    (
                        order_status.order_intent_id,
                        order_status.status,
                        parse_utc_iso(order_status.status_ts),
                        order_status.provider,
                        order_status.provider_order_id,
                        Jsonb(dict(order_status.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"orders.order_status:{row[0]}"

    def save_fill_report(self, fill_report: FillReportRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders.fill_report (
                        order_intent_id, provider_fill_id, fill_ts,
                        filled_quantity, fill_price, fees, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING fill_report_id
                    """,
                    (
                        fill_report.order_intent_id,
                        fill_report.provider_fill_id,
                        parse_utc_iso(fill_report.fill_ts),
                        fill_report.filled_quantity,
                        fill_report.fill_price,
                        fill_report.fees,
                        Jsonb(dict(fill_report.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"orders.fill_report:{row[0]}"

    def save_execution_result(self, execution_result: ExecutionResultRecord) -> str:
        from psycopg.types.json import Jsonb

        submitted_at = parse_utc_iso(execution_result.submitted_at) if execution_result.submitted_at else None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders.execution_result (
                        execution_result_id, order_intent_id, status,
                        broker_order_id, submitted_at, last_update_at,
                        filled_quantity, avg_fill_price, fees, slippage_bps,
                        errors, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (execution_result_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        broker_order_id = EXCLUDED.broker_order_id,
                        submitted_at = EXCLUDED.submitted_at,
                        last_update_at = EXCLUDED.last_update_at,
                        filled_quantity = EXCLUDED.filled_quantity,
                        avg_fill_price = EXCLUDED.avg_fill_price,
                        fees = EXCLUDED.fees,
                        slippage_bps = EXCLUDED.slippage_bps,
                        errors = EXCLUDED.errors,
                        payload = EXCLUDED.payload
                    """,
                    (
                        execution_result.execution_result_id,
                        execution_result.order_intent_id,
                        execution_result.status,
                        execution_result.broker_order_id,
                        submitted_at,
                        parse_utc_iso(execution_result.last_update_at),
                        execution_result.filled_quantity,
                        execution_result.avg_fill_price,
                        execution_result.fees,
                        execution_result.slippage_bps,
                        list(execution_result.errors),
                        Jsonb(dict(execution_result.payload)),
                    ),
                )
        return f"orders.execution_result:{execution_result.execution_result_id}"

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.audit_record (
                        module_name, job_id, severity, event_type, message,
                        object_type, object_ref, reason_codes, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING audit_record_id
                    """,
                    (
                        audit_record.module_name,
                        audit_record.job_id,
                        audit_record.severity,
                        audit_record.event_type,
                        audit_record.message,
                        audit_record.object_type,
                        audit_record.object_ref,
                        list(audit_record.reason_codes),
                        Jsonb(dict(audit_record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.audit_record:{row[0]}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def ref_tail(ref: str | None) -> str:
    value = str(ref or "")
    return value.rsplit(":", 1)[-1] if ":" in value else value


def instrument_id_tail(ref: str | None) -> str:
    value = str(ref or "")
    for prefix in (
        "registry.instrument_profile:",
        "registry.instrument:",
        "instrument_profile:",
        "instrument:",
    ):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _order_intent_from_row(row: tuple[Any, ...]) -> OrderIntentRecord:
    return OrderIntentRecord(
        order_intent_id=row[0] or "",
        instrument_id=row[1] or "",
        side=row[2] or "",
        quantity=_optional_float(row[3]) or 0.0,
        order_type=row[4] or "",
        limit_price=_optional_float(row[5]),
        time_in_force=row[6] or "",
        max_slippage_bps=_optional_float(row[7]),
        execution_ttl_seconds=_optional_int(row[8]),
        decision_set_id=_optional_text(row[9]),
        risk_check_id=_optional_text(row[10]),
        run_mode=row[11] or "",
        created_at=_iso(row[12]),
        payload=row[13] or {},
    )


def _risk_check_result_from_row(row: tuple[Any, ...]) -> RiskCheckResultRecord:
    return RiskCheckResultRecord(
        risk_check_id=row[0] or "",
        decision_set_id=row[1] or "",
        status=row[2] or "",
        approved_order_intents=tuple(row[3] or ()),
        rejected_decisions=tuple(row[4] or ()),
        risk_flags=tuple(row[5] or ()),
        adjustments=tuple(dict(item) for item in (row[6] or ()) if isinstance(item, Mapping)),
        checked_at=_iso(row[7]),
        payload=row[8] or {},
    )


def _instrument_profile_from_row(row: tuple[Any, ...]) -> InstrumentProfileRecord:
    return InstrumentProfileRecord(
        instrument_id=row[0] or "",
        universe_id=row[1] or "",
        ticker=row[2] or "",
        lot_size=_optional_int(row[3]),
        tradable=bool(row[4]),
        execution_enabled=bool(row[5]),
        arena_go_secid=row[6],
        arena_go_quantity_mode=row[7] or "shares",
        metadata=row[8] or {},
    )


def _portfolio_snapshot_from_row(row: tuple[Any, ...]) -> PortfolioSnapshotRecord:
    return PortfolioSnapshotRecord(
        portfolio_snapshot_id=row[0] or "",
        portfolio_id=row[1] or "",
        universe_id=row[2],
        as_of_ts=_iso(row[3]),
        cash=_optional_float(row[4]),
        equity=_optional_float(row[5]),
        payload=row[6] or {},
    )


def _execution_result_from_row(row: tuple[Any, ...]) -> ExecutionResultRecord:
    return ExecutionResultRecord(
        execution_result_id=row[0] or "",
        order_intent_id=row[1] or "",
        status=row[2] or "",
        broker_order_id=row[3],
        submitted_at=_optional_text(_iso(row[4]) if row[4] else None),
        last_update_at=_iso(row[5]),
        filled_quantity=_optional_float(row[6]) or 0.0,
        avg_fill_price=_optional_float(row[7]),
        fees=_optional_float(row[8]) or 0.0,
        slippage_bps=_optional_float(row[9]),
        errors=tuple(row[10] or ()),
        payload=row[11] or {},
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        iterator = iter(value)
    except TypeError:
        return (str(value),)
    return tuple(str(item) for item in iterator if item not in (None, ""))


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
