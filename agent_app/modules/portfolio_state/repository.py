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
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OrderIntentRecord":
        return cls(
            order_intent_id=str(payload.get("order_intent_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            side=str(payload.get("side") or ""),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class FillReportRecord:
    fill_report_id: str
    order_intent_id: str
    provider_fill_id: str | None
    fill_ts: str
    filled_quantity: float
    fill_price: float
    fees: float
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FillReportRecord":
        return cls(
            fill_report_id=str(payload.get("fill_report_id") or payload.get("id") or ""),
            order_intent_id=str(payload.get("order_intent_id") or ""),
            provider_fill_id=_optional_text(payload.get("provider_fill_id")),
            fill_ts=str(payload.get("fill_ts") or ""),
            filled_quantity=_optional_float(payload.get("filled_quantity")) or 0.0,
            fill_price=_optional_float(payload.get("fill_price")) or 0.0,
            fees=_optional_float(payload.get("fees")) or 0.0,
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class RawMarketPriceRecord:
    instrument_id: str
    price: float
    price_ts: str
    source_ref: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawMarketPriceRecord":
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            price=_optional_float(payload.get("price")) or _optional_float(payload.get("close_price")) or 0.0,
            price_ts=str(payload.get("price_ts") or payload.get("trade_ts") or payload.get("close_ts") or payload.get("as_of_ts") or ""),
            source_ref=str(payload.get("source_ref") or payload.get("price_snapshot_id") or ""),
            payload=_mapping(payload.get("payload") or payload.get("source_payload")),
        )


@dataclass(frozen=True)
class PortfolioSnapshotRecord:
    portfolio_snapshot_id: str
    portfolio_id: str
    universe_id: str | None
    as_of_ts: str
    initial_capital_rub: float | None
    cash: float
    equity: float
    gross_exposure: float
    net_exposure: float
    realized_pnl: float
    unrealized_pnl: float
    source_module: str
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortfolioSnapshotRecord":
        return cls(
            portfolio_snapshot_id=str(payload.get("portfolio_snapshot_id") or payload.get("id") or ""),
            portfolio_id=str(payload.get("portfolio_id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            initial_capital_rub=_optional_float(payload.get("initial_capital_rub")),
            cash=_optional_float(payload.get("cash")) or 0.0,
            equity=_optional_float(payload.get("equity") or payload.get("equity_value")) or 0.0,
            gross_exposure=_optional_float(payload.get("gross_exposure")) or 0.0,
            net_exposure=_optional_float(payload.get("net_exposure")) or 0.0,
            realized_pnl=_optional_float(payload.get("realized_pnl")) or 0.0,
            unrealized_pnl=_optional_float(payload.get("unrealized_pnl")) or 0.0,
            source_module=str(payload.get("source_module") or ""),
            source_refs=_string_tuple(payload.get("source_refs")),
            payload=_mapping(payload.get("payload")),
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "portfolio_snapshot_id": self.portfolio_snapshot_id,
            "portfolio_id": self.portfolio_id,
            "as_of_ts": self.as_of_ts,
            "cash": self.cash,
            "equity_value": self.equity,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "positions": list(self.payload.get("positions") or ()),
            "data_quality_score": self.payload.get("data_quality_score", 0.0),
        }


@dataclass(frozen=True)
class PositionStateRecord:
    position_state_id: str
    portfolio_id: str
    instrument_id: str
    as_of_ts: str
    quantity: float
    average_price: float | None
    market_price: float | None
    market_value: float
    unrealized_pnl: float
    source_module: str
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PositionStateRecord":
        return cls(
            position_state_id=str(payload.get("position_state_id") or payload.get("id") or ""),
            portfolio_id=str(payload.get("portfolio_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            quantity=_optional_float(payload.get("quantity")) or 0.0,
            average_price=_optional_float(payload.get("average_price") or payload.get("avg_price")),
            market_price=_optional_float(payload.get("market_price")),
            market_value=_optional_float(payload.get("market_value")) or 0.0,
            unrealized_pnl=_optional_float(payload.get("unrealized_pnl")) or 0.0,
            source_module=str(payload.get("source_module") or ""),
            source_refs=_string_tuple(payload.get("source_refs")),
            payload=_mapping(payload.get("payload")),
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "quantity": self.quantity,
            "avg_price": self.average_price,
            "market_price": self.market_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
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


class PortfolioStateRepository(Protocol):
    def get_fill_report(self, fill_report_ref: str) -> FillReportRecord | None:
        ...

    def get_order_intent(self, order_intent_id: str) -> OrderIntentRecord | None:
        ...

    def get_latest_market_prices(
        self,
        instrument_ids: tuple[str, ...],
        universe_id: str,
        as_of_ts: str,
    ) -> Mapping[str, RawMarketPriceRecord]:
        ...

    def get_previous_snapshot(self, portfolio_id: str, as_of_ts: str) -> PortfolioSnapshotRecord | None:
        ...

    def list_previous_positions(self, portfolio_id: str, as_of_ts: str) -> tuple[PositionStateRecord, ...]:
        ...

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshotRecord) -> str:
        ...

    def save_position_state(self, position: PositionStateRecord) -> str:
        ...

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        ...


class InMemoryPortfolioStateRepository:
    def __init__(
        self,
        fill_reports: tuple[Mapping[str, Any] | FillReportRecord, ...] = (),
        order_intents: tuple[Mapping[str, Any] | OrderIntentRecord, ...] = (),
        market_prices: tuple[Mapping[str, Any] | RawMarketPriceRecord, ...] = (),
        portfolio_snapshots: tuple[Mapping[str, Any] | PortfolioSnapshotRecord, ...] = (),
        position_states: tuple[Mapping[str, Any] | PositionStateRecord, ...] = (),
    ) -> None:
        self.fill_reports = tuple(
            item if isinstance(item, FillReportRecord) else FillReportRecord.from_mapping(item)
            for item in fill_reports
        )
        self.order_intents = tuple(
            item if isinstance(item, OrderIntentRecord) else OrderIntentRecord.from_mapping(item)
            for item in order_intents
        )
        self.market_prices = tuple(
            item if isinstance(item, RawMarketPriceRecord) else RawMarketPriceRecord.from_mapping(item)
            for item in market_prices
        )
        self.saved_portfolio_snapshots: list[PortfolioSnapshotRecord] = [
            item if isinstance(item, PortfolioSnapshotRecord) else PortfolioSnapshotRecord.from_mapping(item)
            for item in portfolio_snapshots
        ]
        self.saved_position_states: list[PositionStateRecord] = [
            item if isinstance(item, PositionStateRecord) else PositionStateRecord.from_mapping(item)
            for item in position_states
        ]
        self.saved_audit_records: list[AuditRecord] = []

    def get_fill_report(self, fill_report_ref: str) -> FillReportRecord | None:
        requested = ref_tail(fill_report_ref)
        for fill in self.fill_reports:
            if fill.fill_report_id == requested or fill.provider_fill_id == requested:
                return fill
        return None

    def get_order_intent(self, order_intent_id: str) -> OrderIntentRecord | None:
        requested = ref_tail(order_intent_id)
        for order in self.order_intents:
            if order.order_intent_id == requested:
                return order
        return None

    def get_latest_market_prices(
        self,
        instrument_ids: tuple[str, ...],
        universe_id: str,
        as_of_ts: str,
    ) -> Mapping[str, RawMarketPriceRecord]:
        del universe_id
        as_of = parse_utc_iso(as_of_ts)
        requested = set(instrument_ids)
        latest: dict[str, RawMarketPriceRecord] = {}
        for price in self.market_prices:
            if requested and price.instrument_id not in requested:
                continue
            if price.price_ts and parse_utc_iso(price.price_ts) > as_of:
                continue
            current = latest.get(price.instrument_id)
            if current is None or (price.price_ts, price.source_ref) > (current.price_ts, current.source_ref):
                latest[price.instrument_id] = price
        return latest

    def get_previous_snapshot(self, portfolio_id: str, as_of_ts: str) -> PortfolioSnapshotRecord | None:
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            snapshot
            for snapshot in self.saved_portfolio_snapshots
            if snapshot.portfolio_id == portfolio_id
            and snapshot.as_of_ts
            and parse_utc_iso(snapshot.as_of_ts) <= as_of
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item.as_of_ts, item.portfolio_snapshot_id))[-1]

    def list_previous_positions(self, portfolio_id: str, as_of_ts: str) -> tuple[PositionStateRecord, ...]:
        as_of = parse_utc_iso(as_of_ts)
        latest: dict[str, PositionStateRecord] = {}
        for position in self.saved_position_states:
            if position.portfolio_id != portfolio_id:
                continue
            if position.as_of_ts and parse_utc_iso(position.as_of_ts) > as_of:
                continue
            current = latest.get(position.instrument_id)
            if current is None or (position.as_of_ts, position.position_state_id) > (current.as_of_ts, current.position_state_id):
                latest[position.instrument_id] = position
        return tuple(sorted(latest.values(), key=lambda item: item.instrument_id))

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshotRecord) -> str:
        self.saved_portfolio_snapshots = [
            item
            for item in self.saved_portfolio_snapshots
            if item.portfolio_snapshot_id != snapshot.portfolio_snapshot_id
        ]
        self.saved_portfolio_snapshots.append(snapshot)
        return f"portfolio.portfolio_snapshot:{snapshot.portfolio_snapshot_id}"

    def save_position_state(self, position: PositionStateRecord) -> str:
        self.saved_position_states = [
            item
            for item in self.saved_position_states
            if item.position_state_id != position.position_state_id
        ]
        self.saved_position_states.append(position)
        return f"portfolio.position_state:{position.position_state_id}"

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        self.saved_audit_records.append(audit_record)
        return f"audit.audit_record:{audit_record.audit_record_id}"


class PostgresPortfolioStateRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def get_fill_report(self, fill_report_ref: str) -> FillReportRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT fill_report_id, order_intent_id, provider_fill_id,
                           fill_ts, filled_quantity, fill_price, fees, payload
                      FROM orders.fill_report
                     WHERE fill_report_id::text = %s
                        OR provider_fill_id = %s
                    """,
                    (ref_tail(fill_report_ref), ref_tail(fill_report_ref)),
                )
                row = cur.fetchone()
        return _fill_report_from_row(row) if row else None

    def get_order_intent(self, order_intent_id: str) -> OrderIntentRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT order_intent_id, instrument_id, side, payload
                      FROM orders.order_intent
                     WHERE order_intent_id = %s
                    """,
                    (ref_tail(order_intent_id),),
                )
                row = cur.fetchone()
        return _order_intent_from_row(row) if row else None

    def get_latest_market_prices(
        self,
        instrument_ids: tuple[str, ...],
        universe_id: str,
        as_of_ts: str,
    ) -> Mapping[str, RawMarketPriceRecord]:
        if not instrument_ids:
            return {}
        as_of = parse_utc_iso(as_of_ts)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           raw_trade_id::text, instrument_id, trade_ts, price, source_payload
                      FROM raw_market.raw_trade
                     WHERE instrument_id = ANY(%s)
                       AND (%s = '' OR universe_id = %s OR universe_id IS NULL)
                       AND trade_ts <= %s
                     ORDER BY instrument_id, trade_ts DESC, raw_trade_id DESC
                    """,
                    (list(instrument_ids), universe_id, universe_id, as_of),
                )
                trade_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           raw_candle_id::text, instrument_id,
                           COALESCE(close_ts, open_ts) AS price_ts,
                           close_price, source_payload
                      FROM raw_market.raw_candle
                     WHERE instrument_id = ANY(%s)
                       AND (%s = '' OR universe_id = %s OR universe_id IS NULL)
                       AND COALESCE(close_ts, open_ts) <= %s
                       AND close_price IS NOT NULL
                     ORDER BY instrument_id, COALESCE(close_ts, open_ts) DESC, raw_candle_id DESC
                    """,
                    (list(instrument_ids), universe_id, universe_id, as_of),
                )
                candle_rows = cur.fetchall()
        prices: dict[str, RawMarketPriceRecord] = {}
        for row in candle_rows:
            record = RawMarketPriceRecord(
                source_ref=f"raw_market.raw_candle:{row[0]}",
                instrument_id=row[1] or "",
                price=_optional_float(row[3]) or 0.0,
                price_ts=_iso(row[2]),
                payload=row[4] or {},
            )
            prices[record.instrument_id] = record
        for row in trade_rows:
            record = RawMarketPriceRecord(
                source_ref=f"raw_market.raw_trade:{row[0]}",
                instrument_id=row[1] or "",
                price=_optional_float(row[3]) or 0.0,
                price_ts=_iso(row[2]),
                payload=row[4] or {},
            )
            prices[record.instrument_id] = record
        return prices

    def get_previous_snapshot(self, portfolio_id: str, as_of_ts: str) -> PortfolioSnapshotRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT portfolio_snapshot_id, portfolio_id, universe_id, as_of_ts,
                           initial_capital_rub, cash, equity, gross_exposure,
                           net_exposure, realized_pnl, unrealized_pnl,
                           source_module, source_refs, payload
                      FROM portfolio.portfolio_snapshot
                     WHERE portfolio_id = %s
                       AND as_of_ts <= %s
                     ORDER BY as_of_ts DESC, portfolio_snapshot_id DESC
                     LIMIT 1
                    """,
                    (portfolio_id, parse_utc_iso(as_of_ts)),
                )
                row = cur.fetchone()
        return _portfolio_snapshot_from_row(row) if row else None

    def list_previous_positions(self, portfolio_id: str, as_of_ts: str) -> tuple[PositionStateRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           position_state_id, portfolio_id, instrument_id, as_of_ts,
                           quantity, average_price, market_price, market_value,
                           unrealized_pnl, source_module, source_refs, payload
                      FROM portfolio.position_state
                     WHERE portfolio_id = %s
                       AND as_of_ts <= %s
                     ORDER BY instrument_id, as_of_ts DESC, position_state_id DESC
                    """,
                    (portfolio_id, parse_utc_iso(as_of_ts)),
                )
                rows = cur.fetchall()
        return tuple(_position_state_from_row(row) for row in rows)

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshotRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio.portfolio_snapshot (
                        portfolio_snapshot_id, portfolio_id, universe_id, as_of_ts,
                        initial_capital_rub, cash, equity, gross_exposure,
                        net_exposure, realized_pnl, unrealized_pnl,
                        source_module, source_refs, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (portfolio_snapshot_id) DO UPDATE SET
                        cash = EXCLUDED.cash,
                        equity = EXCLUDED.equity,
                        gross_exposure = EXCLUDED.gross_exposure,
                        net_exposure = EXCLUDED.net_exposure,
                        realized_pnl = EXCLUDED.realized_pnl,
                        unrealized_pnl = EXCLUDED.unrealized_pnl,
                        source_refs = EXCLUDED.source_refs,
                        payload = EXCLUDED.payload
                    """,
                    (
                        snapshot.portfolio_snapshot_id,
                        snapshot.portfolio_id,
                        snapshot.universe_id,
                        parse_utc_iso(snapshot.as_of_ts),
                        snapshot.initial_capital_rub,
                        snapshot.cash,
                        snapshot.equity,
                        snapshot.gross_exposure,
                        snapshot.net_exposure,
                        snapshot.realized_pnl,
                        snapshot.unrealized_pnl,
                        snapshot.source_module,
                        list(snapshot.source_refs),
                        Jsonb(dict(snapshot.payload)),
                    ),
                )
        return f"portfolio.portfolio_snapshot:{snapshot.portfolio_snapshot_id}"

    def save_position_state(self, position: PositionStateRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio.position_state (
                        position_state_id, portfolio_id, instrument_id, as_of_ts,
                        quantity, average_price, market_price, market_value,
                        unrealized_pnl, source_module, source_refs, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (position_state_id) DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        average_price = EXCLUDED.average_price,
                        market_price = EXCLUDED.market_price,
                        market_value = EXCLUDED.market_value,
                        unrealized_pnl = EXCLUDED.unrealized_pnl,
                        source_refs = EXCLUDED.source_refs,
                        payload = EXCLUDED.payload
                    """,
                    (
                        position.position_state_id,
                        position.portfolio_id,
                        position.instrument_id,
                        parse_utc_iso(position.as_of_ts),
                        position.quantity,
                        position.average_price,
                        position.market_price,
                        position.market_value,
                        position.unrealized_pnl,
                        position.source_module,
                        list(position.source_refs),
                        Jsonb(dict(position.payload)),
                    ),
                )
        return f"portfolio.position_state:{position.position_state_id}"

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


def _fill_report_from_row(row: tuple[Any, ...]) -> FillReportRecord:
    return FillReportRecord(
        fill_report_id=str(row[0]),
        order_intent_id=row[1] or "",
        provider_fill_id=row[2],
        fill_ts=_iso(row[3]),
        filled_quantity=_optional_float(row[4]) or 0.0,
        fill_price=_optional_float(row[5]) or 0.0,
        fees=_optional_float(row[6]) or 0.0,
        payload=row[7] or {},
    )


def _order_intent_from_row(row: tuple[Any, ...]) -> OrderIntentRecord:
    return OrderIntentRecord(
        order_intent_id=row[0] or "",
        instrument_id=row[1] or "",
        side=row[2] or "",
        payload=row[3] or {},
    )


def _portfolio_snapshot_from_row(row: tuple[Any, ...]) -> PortfolioSnapshotRecord:
    return PortfolioSnapshotRecord(
        portfolio_snapshot_id=row[0] or "",
        portfolio_id=row[1] or "",
        universe_id=row[2],
        as_of_ts=_iso(row[3]),
        initial_capital_rub=_optional_float(row[4]),
        cash=_optional_float(row[5]) or 0.0,
        equity=_optional_float(row[6]) or 0.0,
        gross_exposure=_optional_float(row[7]) or 0.0,
        net_exposure=_optional_float(row[8]) or 0.0,
        realized_pnl=_optional_float(row[9]) or 0.0,
        unrealized_pnl=_optional_float(row[10]) or 0.0,
        source_module=row[11] or "",
        source_refs=tuple(row[12] or ()),
        payload=row[13] or {},
    )


def _position_state_from_row(row: tuple[Any, ...]) -> PositionStateRecord:
    return PositionStateRecord(
        position_state_id=row[0] or "",
        portfolio_id=row[1] or "",
        instrument_id=row[2] or "",
        as_of_ts=_iso(row[3]),
        quantity=_optional_float(row[4]) or 0.0,
        average_price=_optional_float(row[5]),
        market_price=_optional_float(row[6]),
        market_value=_optional_float(row[7]) or 0.0,
        unrealized_pnl=_optional_float(row[8]) or 0.0,
        source_module=row[9] or "",
        source_refs=tuple(row[10] or ()),
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
