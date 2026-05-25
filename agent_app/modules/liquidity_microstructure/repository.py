from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class InstrumentProfile:
    instrument_id: str
    universe_id: str
    ticker: str
    sector: str | None = None
    is_active: bool = True
    tradable: bool = True
    board_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentProfile":
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            ticker=str(payload.get("ticker") or ""),
            board_id=_optional_text(payload.get("board_id")),
            sector=_optional_text(payload.get("sector")),
            is_active=bool(payload.get("is_active", True)),
            tradable=bool(payload.get("tradable", True)),
        )


@dataclass(frozen=True)
class RawOrderBook:
    raw_orderbook_id: str
    instrument_id: str
    universe_id: str
    snapshot_ts: str
    bids: object
    asks: object
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawOrderBook":
        return cls(
            raw_orderbook_id=str(payload.get("raw_orderbook_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            snapshot_ts=str(payload.get("snapshot_ts") or ""),
            bids=payload.get("bids") or (),
            asks=payload.get("asks") or (),
            provider=str(payload.get("provider") or ""),
            source_payload=payload.get("source_payload") or {},
            received_at=_optional_text(payload.get("received_at")),
        )


@dataclass(frozen=True)
class RawTrade:
    raw_trade_id: str
    instrument_id: str
    universe_id: str
    trade_ts: str
    price: float | None
    quantity: float | None
    side: str | None = None
    trade_value: float | None = None
    provider: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawTrade":
        return cls(
            raw_trade_id=str(payload.get("raw_trade_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            trade_ts=str(payload.get("trade_ts") or ""),
            price=_optional_float(payload.get("price")),
            quantity=_optional_float(payload.get("quantity")),
            side=_optional_text(payload.get("side")),
            trade_value=_optional_float(payload.get("trade_value")),
            provider=str(payload.get("provider") or ""),
        )


@dataclass(frozen=True)
class RawCandle:
    raw_candle_id: str
    instrument_id: str
    universe_id: str
    timeframe: str
    close_ts: str
    close_price: float | None
    high_price: float | None = None
    low_price: float | None = None
    volume: float | None = None
    turnover: float | None = None
    provider: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawCandle":
        return cls(
            raw_candle_id=str(payload.get("raw_candle_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            timeframe=str(payload.get("timeframe") or ""),
            close_ts=str(payload.get("close_ts") or ""),
            close_price=_optional_float(payload.get("close_price")),
            high_price=_optional_float(payload.get("high_price")),
            low_price=_optional_float(payload.get("low_price")),
            volume=_optional_float(payload.get("volume")),
            turnover=_optional_float(payload.get("turnover")),
            provider=str(payload.get("provider") or ""),
        )


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    instrument_id: str
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float
    normalized_value: float | None
    unit: str
    horizon: str
    contour: str
    timestamp: str
    ttl_seconds: int
    confidence_score: float
    source_module: str
    source_refs: tuple[str, ...]
    calculation_version: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "instrument_id": self.instrument_id,
            "metric_name": self.metric_name,
            "metric_group": self.metric_group,
            "metric_type": self.metric_type,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "unit": self.unit,
            "horizon": self.horizon,
            "contour": self.contour,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "confidence_score": self.confidence_score,
            "source_module": self.source_module,
            "source_refs": list(self.source_refs),
            "calculation_version": self.calculation_version,
            "quality_flags": list(self.quality_flags),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ExecutionConstraintHint:
    constraint_id: str
    instrument_id: str
    universe_id: str
    as_of_ts: str
    spread_bps: float | None
    estimated_slippage_bps: float | None
    max_suggested_order_notional: float
    market_order_allowed: bool
    reason_codes: tuple[str, ...]
    source_module: str
    calculation_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_constraint_id": self.constraint_id,
            "instrument_id": self.instrument_id,
            "universe_id": self.universe_id,
            "as_of_ts": self.as_of_ts,
            "spread_bps": self.spread_bps,
            "estimated_slippage_bps": self.estimated_slippage_bps,
            "max_suggested_order_notional": self.max_suggested_order_notional,
            "market_order_allowed": self.market_order_allowed,
            "reason_codes": list(self.reason_codes),
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class AuditRecord:
    module_name: str
    severity: str
    event_type: str
    message: str
    job_id: str | None = None
    object_type: str | None = None
    object_ref: str | None = None
    reason_codes: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)


class LiquidityMicrostructureRepository(Protocol):
    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        ...

    def list_orderbooks(
        self,
        orderbook_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawOrderBook, ...]:
        ...

    def list_trades(
        self,
        trades_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTrade, ...]:
        ...

    def list_candles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        ...

    def list_active_metric_weights(
        self,
        horizon: str,
        metric_names: tuple[str, ...],
    ) -> Mapping[str, float]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_execution_constraint_hint(self, hint: ExecutionConstraintHint) -> str:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryLiquidityMicrostructureRepository:
    def __init__(
        self,
        profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
        orderbooks: tuple[Mapping[str, Any] | RawOrderBook, ...] = (),
        trades: tuple[Mapping[str, Any] | RawTrade, ...] = (),
        candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
        active_weights: Mapping[tuple[str, tuple[str, ...]], Mapping[str, float]] | None = None,
    ) -> None:
        self.profiles = tuple(
            profile if isinstance(profile, InstrumentProfile) else InstrumentProfile.from_mapping(profile)
            for profile in profiles
        )
        self.orderbooks = tuple(
            orderbook if isinstance(orderbook, RawOrderBook) else RawOrderBook.from_mapping(orderbook)
            for orderbook in orderbooks
        )
        self.trades = tuple(
            trade if isinstance(trade, RawTrade) else RawTrade.from_mapping(trade)
            for trade in trades
        )
        self.candles = tuple(
            candle if isinstance(candle, RawCandle) else RawCandle.from_mapping(candle)
            for candle in candles
        )
        self.active_weights = dict(active_weights or {})
        self.feature_records: list[FeatureRecord] = []
        self.execution_constraint_hints: list[ExecutionConstraintHint] = []
        self.audit_records: list[AuditRecord] = []

    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        requested = set(instrument_ids)
        profiles = [
            profile
            for profile in self.profiles
            if profile.universe_id == universe_id
            and profile.is_active
            and (not requested or profile.instrument_id in requested)
        ]
        return tuple(sorted(profiles, key=lambda profile: profile.instrument_id))

    def list_orderbooks(
        self,
        orderbook_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawOrderBook, ...]:
        requested = set(instrument_ids)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        in_window = [
            orderbook
            for orderbook in self.orderbooks
            if orderbook.universe_id == universe_id
            and orderbook.instrument_id in requested
            and from_dt <= parse_utc_iso(orderbook.snapshot_ts) <= to_dt
        ]
        latest_before_window: dict[str, RawOrderBook] = {}
        for orderbook in self.orderbooks:
            if orderbook.universe_id != universe_id or orderbook.instrument_id not in requested:
                continue
            snapshot_dt = parse_utc_iso(orderbook.snapshot_ts)
            if snapshot_dt > to_dt or snapshot_dt >= from_dt:
                continue
            current = latest_before_window.get(orderbook.instrument_id)
            if current is None or parse_utc_iso(current.snapshot_ts) < snapshot_dt:
                latest_before_window[orderbook.instrument_id] = orderbook
        combined = {orderbook.raw_orderbook_id: orderbook for orderbook in in_window}
        combined.update({orderbook.raw_orderbook_id: orderbook for orderbook in latest_before_window.values()})
        return tuple(sorted(combined.values(), key=lambda item: (item.instrument_id, item.snapshot_ts)))

    def list_trades(
        self,
        trades_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTrade, ...]:
        requested = set(instrument_ids)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        trades = [
            trade
            for trade in self.trades
            if trade.universe_id == universe_id
            and trade.instrument_id in requested
            and from_dt <= parse_utc_iso(trade.trade_ts) <= to_dt
        ]
        return tuple(sorted(trades, key=lambda item: (item.instrument_id, item.trade_ts)))

    def list_candles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        requested = set(instrument_ids)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        candles = [
            candle
            for candle in self.candles
            if candle.universe_id == universe_id
            and candle.instrument_id in requested
            and candle.close_ts
            and from_dt <= parse_utc_iso(candle.close_ts) <= to_dt
        ]
        return tuple(sorted(candles, key=lambda item: (item.instrument_id, item.timeframe, item.close_ts)))

    def list_active_metric_weights(
        self,
        horizon: str,
        metric_names: tuple[str, ...],
    ) -> Mapping[str, float]:
        key = (horizon, tuple(metric_names))
        if key in self.active_weights:
            return dict(self.active_weights[key])
        metric_name_set = set(metric_names)
        merged: dict[str, float] = {}
        for (stored_horizon, stored_names), weights in self.active_weights.items():
            if stored_horizon == horizon and set(stored_names) == metric_name_set:
                merged.update(weights)
        return merged

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_execution_constraint_hint(self, hint: ExecutionConstraintHint) -> str:
        self.execution_constraint_hints.append(hint)
        return f"raw_market.execution_constraint:{hint.constraint_id}"

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit:memory:{len(self.audit_records)}"


class PostgresLiquidityMicrostructureRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        query = """
            SELECT instrument_id, universe_id, ticker, board_id, sector, is_active, tradable
              FROM registry.instrument_profile
             WHERE universe_id = %s
               AND is_active = true
        """
        params: list[Any] = [universe_id]
        if instrument_ids:
            query += " AND instrument_id = ANY(%s)"
            params.append(list(instrument_ids))
        query += " ORDER BY instrument_id"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return tuple(
            InstrumentProfile(
                instrument_id=row[0],
                universe_id=row[1],
                ticker=row[2],
                board_id=row[3],
                sector=row[4],
                is_active=bool(row[5]),
                tradable=bool(row[6]),
            )
            for row in rows
        )

    def list_orderbooks(
        self,
        orderbook_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawOrderBook, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH in_window AS (
                        SELECT raw_orderbook_id, instrument_id, universe_id,
                               snapshot_ts, bids, asks, provider, source_payload,
                               received_at
                          FROM raw_market.raw_orderbook
                         WHERE universe_id = %s
                           AND instrument_id = ANY(%s)
                           AND snapshot_ts BETWEEN %s AND %s
                    ),
                    latest_before_window AS (
                        SELECT DISTINCT ON (instrument_id)
                               raw_orderbook_id, instrument_id, universe_id,
                               snapshot_ts, bids, asks, provider, source_payload,
                               received_at
                          FROM raw_market.raw_orderbook
                         WHERE universe_id = %s
                           AND instrument_id = ANY(%s)
                           AND snapshot_ts < %s
                           AND snapshot_ts <= %s
                         ORDER BY instrument_id, snapshot_ts DESC
                    )
                    SELECT DISTINCT ON (raw_orderbook_id)
                           raw_orderbook_id, instrument_id, universe_id,
                           snapshot_ts, bids, asks, provider, source_payload,
                           received_at
                      FROM (
                        SELECT * FROM in_window
                        UNION ALL
                        SELECT * FROM latest_before_window
                      ) candidate
                     ORDER BY raw_orderbook_id, instrument_id, snapshot_ts
                    """,
                    (
                        universe_id,
                        list(instrument_ids),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                        universe_id,
                        list(instrument_ids),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        orderbooks = tuple(
            RawOrderBook(
                raw_orderbook_id=str(row[0]),
                instrument_id=row[1] or "",
                universe_id=row[2] or "",
                snapshot_ts=_iso(row[3]),
                bids=row[4] or (),
                asks=row[5] or (),
                provider=row[6] or "",
                source_payload=row[7] or {},
                received_at=_iso(row[8]) if row[8] else None,
            )
            for row in rows
        )
        return tuple(sorted(orderbooks, key=lambda item: (item.instrument_id, item.snapshot_ts)))

    def list_trades(
        self,
        trades_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTrade, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_trade_id, instrument_id, universe_id, trade_ts,
                           price, quantity, side, trade_value, provider
                      FROM raw_market.raw_trade
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND trade_ts BETWEEN %s AND %s
                     ORDER BY instrument_id, trade_ts
                    """,
                    (
                        universe_id,
                        list(instrument_ids),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        return tuple(
            RawTrade(
                raw_trade_id=str(row[0]),
                instrument_id=row[1] or "",
                universe_id=row[2] or "",
                trade_ts=_iso(row[3]),
                price=_optional_float(row[4]),
                quantity=_optional_float(row[5]),
                side=_optional_text(row[6]),
                trade_value=_optional_float(row[7]),
                provider=row[8] or "",
            )
            for row in rows
        )

    def list_candles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                           close_ts, close_price, high_price, low_price,
                           volume, turnover, provider
                      FROM raw_market.raw_candle
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND close_ts IS NOT NULL
                       AND close_ts BETWEEN %s AND %s
                     ORDER BY instrument_id, timeframe, close_ts
                    """,
                    (
                        universe_id,
                        list(instrument_ids),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        return tuple(
            RawCandle(
                raw_candle_id=str(row[0]),
                instrument_id=row[1] or "",
                universe_id=row[2] or "",
                timeframe=row[3] or "",
                close_ts=_iso(row[4]),
                close_price=_optional_float(row[5]),
                high_price=_optional_float(row[6]),
                low_price=_optional_float(row[7]),
                volume=_optional_float(row[8]),
                turnover=_optional_float(row[9]),
                provider=row[10] or "",
            )
            for row in rows
        )

    def list_active_metric_weights(
        self,
        horizon: str,
        metric_names: tuple[str, ...],
    ) -> Mapping[str, float]:
        if not metric_names:
            return {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rule.metric_name, rule.weight
                      FROM weights.metric_weight_rule rule
                      JOIN weights.weights_profile profile
                        ON profile.weights_profile_id = rule.weights_profile_id
                     WHERE profile.status = 'active'
                       AND profile.profile_name = 'liquidity_microstructure_component_weights'
                       AND profile.horizon = %s
                       AND rule.metric_group = 'liquidity'
                       AND rule.metric_name = ANY(%s)
                    """,
                    (horizon, list(metric_names)),
                )
                rows = cur.fetchall()
        return {row[0]: float(row[1]) for row in rows}

    def save_feature_record(self, record: FeatureRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO features.feature_record (
                        feature_id, instrument_id, metric_name, metric_group,
                        metric_type, raw_value, normalized_value, unit, horizon,
                        contour, timestamp, ttl_seconds, confidence_score,
                        source_module, source_refs, calculation_version,
                        quality_flags, payload
                    ) VALUES (
                        %(feature_id)s, %(instrument_id)s, %(metric_name)s,
                        %(metric_group)s, %(metric_type)s, %(raw_value)s,
                        %(normalized_value)s, %(unit)s, %(horizon)s,
                        %(contour)s, %(timestamp)s, %(ttl_seconds)s,
                        %(confidence_score)s, %(source_module)s, %(source_refs)s,
                        %(calculation_version)s, %(quality_flags)s, %(payload)s
                    )
                    ON CONFLICT (feature_id) DO UPDATE SET
                        raw_value = EXCLUDED.raw_value,
                        normalized_value = EXCLUDED.normalized_value,
                        timestamp = EXCLUDED.timestamp,
                        ttl_seconds = EXCLUDED.ttl_seconds,
                        confidence_score = EXCLUDED.confidence_score,
                        source_refs = EXCLUDED.source_refs,
                        calculation_version = EXCLUDED.calculation_version,
                        quality_flags = EXCLUDED.quality_flags,
                        payload = EXCLUDED.payload
                    """,
                    {
                        **record.to_dict(),
                        "timestamp": parse_utc_iso(record.timestamp),
                        "payload": Jsonb(dict(record.payload)),
                    },
                )
        return f"features.feature_record:{record.feature_id}"

    def save_execution_constraint_hint(self, hint: ExecutionConstraintHint) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_market.execution_constraint (
                        instrument_id, universe_id, as_of_ts, spread_bps,
                        estimated_slippage_bps, max_order_value_rub,
                        constraint_payload, source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING execution_constraint_id
                    """,
                    (
                        hint.instrument_id,
                        hint.universe_id,
                        parse_utc_iso(hint.as_of_ts),
                        hint.spread_bps,
                        hint.estimated_slippage_bps,
                        hint.max_suggested_order_notional,
                        Jsonb(
                            {
                                **dict(hint.payload),
                                "market_order_allowed": hint.market_order_allowed,
                                "reason_codes": list(hint.reason_codes),
                            }
                        ),
                        hint.source_module,
                        hint.calculation_version,
                    ),
                )
                row = cur.fetchone()
        return f"raw_market.execution_constraint:{row[0]}"

    def write_audit_record(self, record: AuditRecord) -> str:
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
                        record.module_name,
                        record.job_id,
                        record.severity,
                        record.event_type,
                        record.message,
                        record.object_type,
                        record.object_ref,
                        list(record.reason_codes),
                        Jsonb(dict(record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.audit_record:{row[0]}"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
