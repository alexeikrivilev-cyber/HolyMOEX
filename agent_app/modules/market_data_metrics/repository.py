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
        )


@dataclass(frozen=True)
class RawCandle:
    raw_candle_id: str
    instrument_id: str
    universe_id: str
    timeframe: str
    open_ts: str
    close_ts: str | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    volume: float | None = None
    turnover: float | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawCandle":
        return cls(
            raw_candle_id=str(payload.get("raw_candle_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            timeframe=str(payload.get("timeframe") or ""),
            open_ts=str(payload.get("open_ts") or ""),
            close_ts=_optional_text(payload.get("close_ts")),
            open_price=_optional_float(payload.get("open_price")),
            high_price=_optional_float(payload.get("high_price")),
            low_price=_optional_float(payload.get("low_price")),
            close_price=_optional_float(payload.get("close_price")),
            volume=_optional_float(payload.get("volume")),
            turnover=_optional_float(payload.get("turnover")),
            provider=str(payload.get("provider") or ""),
            source_payload=payload.get("source_payload") or {},
            received_at=_optional_text(payload.get("received_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_candle_id": self.raw_candle_id,
            "instrument_id": self.instrument_id,
            "universe_id": self.universe_id,
            "timeframe": self.timeframe,
            "open_ts": self.open_ts,
            "close_ts": self.close_ts,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
            "volume": self.volume,
            "turnover": self.turnover,
            "provider": self.provider,
            "source_payload": dict(self.source_payload),
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class RawTrade:
    raw_trade_id: str
    instrument_id: str
    universe_id: str
    trade_ts: str
    price: float | None
    quantity: float | None
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
            trade_value=_optional_float(payload.get("trade_value")),
            provider=str(payload.get("provider") or ""),
        )


@dataclass(frozen=True)
class RawIndexValue:
    raw_index_value_id: str
    index_id: str
    value_ts: str
    value: float | None
    provider: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawIndexValue":
        return cls(
            raw_index_value_id=str(payload.get("raw_index_value_id") or payload.get("id") or ""),
            index_id=str(payload.get("index_id") or ""),
            value_ts=str(payload.get("value_ts") or ""),
            value=_optional_float(payload.get("value")),
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


class MarketDataMetricsRepository(Protocol):
    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        ...

    def list_candles(
        self,
        candles_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        timeframes: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
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

    def list_index_values(self, index_ref: str, from_ts: str, to_ts: str) -> tuple[RawIndexValue, ...]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryMarketDataMetricsRepository:
    def __init__(
        self,
        profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
        candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
        trades: tuple[Mapping[str, Any] | RawTrade, ...] = (),
        index_values: Mapping[str, tuple[Mapping[str, Any] | RawIndexValue, ...]] | None = None,
    ) -> None:
        self.profiles = tuple(
            profile if isinstance(profile, InstrumentProfile) else InstrumentProfile.from_mapping(profile)
            for profile in profiles
        )
        self.candles = tuple(
            candle if isinstance(candle, RawCandle) else RawCandle.from_mapping(candle)
            for candle in candles
        )
        self.trades = tuple(
            trade if isinstance(trade, RawTrade) else RawTrade.from_mapping(trade)
            for trade in trades
        )
        self.index_values = {
            ref: tuple(
                item if isinstance(item, RawIndexValue) else RawIndexValue.from_mapping(item)
                for item in values
            )
            for ref, values in dict(index_values or {}).items()
        }
        self.feature_records: list[FeatureRecord] = []
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

    def list_candles(
        self,
        candles_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        timeframes: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        requested = set(instrument_ids)
        requested_timeframes = set(timeframes)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        candles = [
            candle
            for candle in self.candles
            if candle.universe_id == universe_id
            and candle.instrument_id in requested
            and candle.timeframe in requested_timeframes
            and candle.close_ts
            and from_dt <= parse_utc_iso(candle.close_ts) <= to_dt
        ]
        return tuple(sorted(candles, key=lambda candle: (candle.instrument_id, candle.timeframe, candle.close_ts or "")))

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
        return tuple(sorted(trades, key=lambda trade: (trade.instrument_id, trade.trade_ts)))

    def list_index_values(self, index_ref: str, from_ts: str, to_ts: str) -> tuple[RawIndexValue, ...]:
        if not index_ref:
            return ()
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        values = self.index_values.get(index_ref, ())
        filtered = [
            item
            for item in values
            if item.value is not None and from_dt <= parse_utc_iso(item.value_ts) <= to_dt
        ]
        return tuple(sorted(filtered, key=lambda item: item.value_ts))

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit:memory:{len(self.audit_records)}"


class PostgresMarketDataMetricsRepository:
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
        requested = list(instrument_ids)
        query = """
            SELECT instrument_id, universe_id, ticker, board_id, sector, is_active
              FROM registry.instrument_profile
             WHERE universe_id = %s
               AND is_active = true
        """
        params: list[Any] = [universe_id]
        if requested:
            query += " AND instrument_id = ANY(%s)"
            params.append(requested)
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
            )
            for row in rows
        )

    def list_candles(
        self,
        candles_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        timeframes: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                           open_ts, close_ts, open_price, high_price, low_price,
                           close_price, volume, turnover, provider, source_payload,
                           received_at
                      FROM raw_market.raw_candle
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND timeframe = ANY(%s)
                       AND close_ts IS NOT NULL
                       AND close_ts BETWEEN %s AND %s
                     ORDER BY instrument_id, timeframe, close_ts
                    """,
                    (
                        universe_id,
                        list(instrument_ids),
                        list(timeframes),
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
                open_ts=_iso(row[4]),
                close_ts=_iso(row[5]) if row[5] else None,
                open_price=_optional_float(row[6]),
                high_price=_optional_float(row[7]),
                low_price=_optional_float(row[8]),
                close_price=_optional_float(row[9]),
                volume=_optional_float(row[10]),
                turnover=_optional_float(row[11]),
                provider=row[12] or "",
                source_payload=row[13] or {},
                received_at=_iso(row[14]) if row[14] else None,
            )
            for row in rows
        )

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
                           price, quantity, trade_value, provider
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
                trade_value=_optional_float(row[6]),
                provider=row[7] or "",
            )
            for row in rows
        )

    def list_index_values(self, index_ref: str, from_ts: str, to_ts: str) -> tuple[RawIndexValue, ...]:
        if not index_ref:
            return ()
        index_id = index_ref.rsplit(":", 1)[-1] if index_ref.startswith("raw_market.raw_index_value:") else index_ref
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_index_value_id, index_id, value_ts, value, provider
                      FROM raw_market.raw_index_value
                     WHERE (index_id = %s OR raw_index_value_id::text = %s)
                       AND value_ts BETWEEN %s AND %s
                     ORDER BY value_ts
                    """,
                    (
                        index_id,
                        index_id,
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        return tuple(
            RawIndexValue(
                raw_index_value_id=str(row[0]),
                index_id=row[1] or "",
                value_ts=_iso(row[2]),
                value=_optional_float(row[3]),
                provider=row[4] or "",
            )
            for row in rows
        )

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
