from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class RawMacroPoint:
    raw_macro_point_id: str
    series_id: str
    point_ts: str
    value: float | None
    unit: str | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawMacroPoint":
        source_payload = payload.get("source_payload") or {}
        return cls(
            raw_macro_point_id=str(payload.get("raw_macro_point_id") or payload.get("id") or ""),
            series_id=str(payload.get("series_id") or ""),
            point_ts=str(payload.get("point_ts") or payload.get("value_ts") or ""),
            value=_optional_float(payload.get("value")),
            unit=_optional_text(payload.get("unit")),
            provider=str(payload.get("provider") or ""),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
        )


@dataclass(frozen=True)
class RawIndexValue:
    raw_index_value_id: str
    index_id: str
    value_ts: str
    value: float | None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawIndexValue":
        source_payload = payload.get("source_payload") or {}
        return cls(
            raw_index_value_id=str(payload.get("raw_index_value_id") or payload.get("id") or ""),
            index_id=str(payload.get("index_id") or ""),
            value_ts=str(payload.get("value_ts") or ""),
            value=_optional_float(payload.get("value")),
            provider=str(payload.get("provider") or ""),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
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
        source_payload = payload.get("source_payload") or {}
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
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
        )


@dataclass(frozen=True)
class StructuredEvent:
    event_id: str
    instrument_ids: tuple[str, ...]
    event_type: str
    event_subtype: str | None
    event_ts: str
    detected_at: str | None = None
    source_refs: tuple[str, ...] = ()
    relevance_score: float | None = None
    materiality_score: float | None = None
    novelty_score: float | None = None
    surprise_score: float | None = None
    sentiment_score: float | None = None
    confidence_score: float | None = None
    evidence: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    model_version: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StructuredEvent":
        event_payload = payload.get("payload") or {}
        return cls(
            event_id=str(payload.get("event_id") or payload.get("id") or ""),
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or ())),
            event_type=str(payload.get("event_type") or ""),
            event_subtype=_optional_text(payload.get("event_subtype")),
            event_ts=str(payload.get("event_ts") or ""),
            detected_at=_optional_text(payload.get("detected_at")),
            source_refs=tuple(str(item) for item in (payload.get("source_refs") or ())),
            relevance_score=_optional_float(payload.get("relevance_score")),
            materiality_score=_optional_float(payload.get("materiality_score")),
            novelty_score=_optional_float(payload.get("novelty_score")),
            surprise_score=_optional_float(payload.get("surprise_score")),
            sentiment_score=_optional_float(payload.get("sentiment_score")),
            confidence_score=_optional_float(payload.get("confidence_score")),
            evidence=tuple(str(item) for item in (payload.get("evidence") or ())),
            reason_codes=tuple(str(item) for item in (payload.get("reason_codes") or ())),
            model_version=_optional_text(payload.get("model_version")),
            payload=event_payload if isinstance(event_payload, Mapping) else {},
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
class MarketStateRecord:
    market_state_id: str
    universe_id: str
    as_of_ts: str
    market_regime: str
    volatility_regime: str
    liquidity_regime: str
    correlation_regime: str
    risk_on_risk_off_score: float | None
    confidence_score: float
    source_refs: tuple[str, ...]
    market_session_status: str = "unknown"
    source_module: str = "Market Context Module"
    calculation_version: str = "market_context_v1"
    ttl_seconds: int = 0
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_state_id": self.market_state_id,
            "universe_id": self.universe_id,
            "as_of_ts": self.as_of_ts,
            "market_regime": self.market_regime,
            "volatility_regime": self.volatility_regime,
            "liquidity_regime": self.liquidity_regime,
            "correlation_regime": self.correlation_regime,
            "risk_on_risk_off_score": self.risk_on_risk_off_score,
            "confidence_score": self.confidence_score,
            "source_refs": list(self.source_refs),
            "market_session_status": self.market_session_status,
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "ttl_seconds": self.ttl_seconds,
            "timestamp": self.as_of_ts,
            "quality_flags": list(self.quality_flags),
            "payload": dict(self.payload),
        }


class MarketContextRepository(Protocol):
    def list_macro_points(
        self,
        macro_refs: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawMacroPoint, ...]:
        ...

    def list_index_values(
        self,
        index_refs: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawIndexValue, ...]:
        ...

    def list_candles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        ...

    def list_structured_events(
        self,
        event_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[StructuredEvent, ...]:
        ...

    def list_sector_mappings(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> Mapping[str, str]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_market_state_record(self, record: MarketStateRecord) -> str:
        ...


class InMemoryMarketContextRepository:
    def __init__(
        self,
        macro_points: Mapping[str, tuple[Mapping[str, Any] | RawMacroPoint, ...]]
        | tuple[Mapping[str, Any] | RawMacroPoint, ...] = (),
        index_values: Mapping[str, tuple[Mapping[str, Any] | RawIndexValue, ...]]
        | tuple[Mapping[str, Any] | RawIndexValue, ...] = (),
        candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
        structured_events: Mapping[str, tuple[Mapping[str, Any] | StructuredEvent, ...]]
        | tuple[Mapping[str, Any] | StructuredEvent, ...] = (),
        sector_mappings: Mapping[str, str] | None = None,
    ) -> None:
        self.macro_points_by_ref, self.macro_points = _coerce_ref_map(macro_points, RawMacroPoint.from_mapping)
        self.index_values_by_ref, self.index_values = _coerce_ref_map(index_values, RawIndexValue.from_mapping)
        self.candles = tuple(
            candle if isinstance(candle, RawCandle) else RawCandle.from_mapping(candle)
            for candle in candles
        )
        self.events_by_ref, self.structured_events = _coerce_ref_map(structured_events, StructuredEvent.from_mapping)
        self.sector_mappings = dict(sector_mappings or {})
        self.feature_records: list[FeatureRecord] = []
        self.market_state_records: list[MarketStateRecord] = []

    def list_macro_points(
        self,
        macro_refs: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawMacroPoint, ...]:
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        values = _values_for_refs(self.macro_points_by_ref, self.macro_points, macro_refs, "series_id")
        filtered = [
            item
            for item in values
            if item.value is not None
            and item.point_ts
            and from_dt <= parse_utc_iso(item.point_ts) <= to_dt
        ]
        return tuple(sorted(filtered, key=lambda item: (item.series_id, item.point_ts)))

    def list_index_values(
        self,
        index_refs: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawIndexValue, ...]:
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        values = _values_for_refs(self.index_values_by_ref, self.index_values, index_refs, "index_id")
        filtered = [
            item
            for item in values
            if item.value is not None
            and item.value_ts
            and from_dt <= parse_utc_iso(item.value_ts) <= to_dt
        ]
        return tuple(sorted(filtered, key=lambda item: (item.index_id, item.value_ts)))

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
            and (not requested or candle.instrument_id in requested)
            and candle.close_ts
            and from_dt <= parse_utc_iso(candle.close_ts) <= to_dt
        ]
        return tuple(sorted(candles, key=lambda item: (item.instrument_id, item.timeframe, item.close_ts or "")))

    def list_structured_events(
        self,
        event_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[StructuredEvent, ...]:
        del universe_id
        requested_ids = set(instrument_ids)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        values = _values_for_refs(self.events_by_ref, self.structured_events, event_refs, "event_id")
        filtered = [
            event
            for event in values
            if event.event_ts
            and from_dt <= parse_utc_iso(event.event_ts) <= to_dt
            and (not requested_ids or not event.instrument_ids or bool(requested_ids & set(event.instrument_ids)))
        ]
        return tuple(sorted(filtered, key=lambda item: item.event_ts))

    def list_sector_mappings(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> Mapping[str, str]:
        del universe_id
        requested = set(instrument_ids)
        mappings = {
            instrument_id: sector
            for instrument_id, sector in self.sector_mappings.items()
            if not requested or instrument_id in requested
        }
        for candle in sorted(self.candles, key=lambda item: item.close_ts or ""):
            if requested and candle.instrument_id not in requested:
                continue
            sector = _optional_text(candle.source_payload.get("sector"))
            if sector:
                mappings[candle.instrument_id] = sector
        return mappings

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_market_state_record(self, record: MarketStateRecord) -> str:
        self.market_state_records.append(record)
        return f"features.market_state_record:{record.market_state_id}"


class PostgresMarketContextRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_macro_points(
        self,
        macro_refs: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawMacroPoint, ...]:
        series_ids = [_ref_tail(ref) for ref in macro_refs]
        if not series_ids:
            return ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_macro_point_id, series_id, point_ts, value, unit,
                           provider, source_payload, received_at
                      FROM raw_macro.raw_macro_point
                     WHERE series_id = ANY(%s)
                       AND point_ts BETWEEN %s AND %s
                     ORDER BY series_id, point_ts
                    """,
                    (series_ids, parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(
            RawMacroPoint(
                raw_macro_point_id=str(row[0]),
                series_id=row[1] or "",
                point_ts=_iso(row[2]),
                value=_optional_float(row[3]),
                unit=_optional_text(row[4]),
                provider=row[5] or "",
                source_payload=row[6] or {},
                received_at=_iso(row[7]) if row[7] else None,
            )
            for row in rows
        )

    def list_index_values(
        self,
        index_refs: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawIndexValue, ...]:
        index_ids = [_ref_tail(ref) for ref in index_refs]
        if not index_ids:
            return ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_index_value_id, index_id, value_ts, value,
                           provider, source_payload, received_at
                      FROM raw_market.raw_index_value
                     WHERE (index_id = ANY(%s) OR raw_index_value_id::text = ANY(%s))
                       AND value_ts BETWEEN %s AND %s
                     ORDER BY index_id, value_ts
                    """,
                    (index_ids, index_ids, parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(
            RawIndexValue(
                raw_index_value_id=str(row[0]),
                index_id=row[1] or "",
                value_ts=_iso(row[2]),
                value=_optional_float(row[3]),
                provider=row[4] or "",
                source_payload=row[5] or {},
                received_at=_iso(row[6]) if row[6] else None,
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
        if not instrument_ids:
            return ()
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
                       AND close_ts IS NOT NULL
                       AND close_ts BETWEEN %s AND %s
                     ORDER BY instrument_id, timeframe, close_ts
                    """,
                    (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
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

    def list_structured_events(
        self,
        event_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[StructuredEvent, ...]:
        del universe_id
        event_ids = [_ref_tail(ref) for ref in event_refs]
        with self._connect() as conn:
            with conn.cursor() as cur:
                if event_ids:
                    cur.execute(
                        """
                        SELECT event_id, instrument_ids, event_type, event_subtype,
                               event_ts, detected_at, source_refs, relevance_score,
                               materiality_score, novelty_score, surprise_score,
                               sentiment_score, confidence_score, evidence,
                               reason_codes, model_version, payload
                          FROM events.structured_event
                         WHERE event_id = ANY(%s)
                           AND event_ts BETWEEN %s AND %s
                           AND (
                                cardinality(instrument_ids) = 0
                                OR instrument_ids && %s
                           )
                         ORDER BY event_ts
                        """,
                        (event_ids, parse_utc_iso(from_ts), parse_utc_iso(to_ts), list(instrument_ids)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT event_id, instrument_ids, event_type, event_subtype,
                               event_ts, detected_at, source_refs, relevance_score,
                               materiality_score, novelty_score, surprise_score,
                               sentiment_score, confidence_score, evidence,
                               reason_codes, model_version, payload
                          FROM events.structured_event
                         WHERE event_type IN ('macro', 'regulation', 'sector', 'market_structure')
                           AND event_ts BETWEEN %s AND %s
                           AND (
                                cardinality(instrument_ids) = 0
                                OR instrument_ids && %s
                           )
                         ORDER BY event_ts
                        """,
                        (parse_utc_iso(from_ts), parse_utc_iso(to_ts), list(instrument_ids)),
                    )
                rows = cur.fetchall()
        return tuple(_structured_event_from_row(row) for row in rows)

    def list_sector_mappings(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> Mapping[str, str]:
        if not instrument_ids:
            return {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           instrument_id, source_payload ->> 'sector'
                      FROM raw_market.raw_candle
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND source_payload ? 'sector'
                     ORDER BY instrument_id, close_ts DESC
                    """,
                    (universe_id, list(instrument_ids)),
                )
                rows = cur.fetchall()
        return {row[0]: row[1] for row in rows if row[0] and row[1]}

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

    def save_market_state_record(self, record: MarketStateRecord) -> str:
        from psycopg.types.json import Jsonb

        payload = {
            **dict(record.payload),
            "market_state_id": record.market_state_id,
            "market_regime": record.market_regime,
            "volatility_regime": record.volatility_regime,
            "liquidity_regime": record.liquidity_regime,
            "correlation_regime": record.correlation_regime,
            "risk_on_risk_off_score": record.risk_on_risk_off_score,
            "confidence_score": record.confidence_score,
            "source_refs": list(record.source_refs),
            "ttl_seconds": record.ttl_seconds,
            "quality_flags": list(record.quality_flags),
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO features.market_state_record (
                        universe_id, as_of_ts, market_session_status,
                        market_regime, payload, source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING market_state_record_id
                    """,
                    (
                        record.universe_id,
                        parse_utc_iso(record.as_of_ts),
                        record.market_session_status,
                        record.market_regime,
                        Jsonb(payload),
                        record.source_module,
                        record.calculation_version,
                    ),
                )
                row = cur.fetchone()
        return f"features.market_state_record:{row[0]}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _coerce_ref_map(
    values: Mapping[str, tuple[Any, ...]] | tuple[Any, ...],
    factory: Any,
) -> tuple[dict[str, tuple[Any, ...]], tuple[Any, ...]]:
    if isinstance(values, Mapping):
        by_ref = {
            str(ref): tuple(item if not isinstance(item, Mapping) else factory(item) for item in items)
            for ref, items in values.items()
        }
        flat: list[Any] = []
        for items in by_ref.values():
            flat.extend(items)
        return by_ref, tuple(flat)
    flat_values = tuple(item if not isinstance(item, Mapping) else factory(item) for item in values)
    return {}, flat_values


def _values_for_refs(
    by_ref: Mapping[str, tuple[Any, ...]],
    flat_values: tuple[Any, ...],
    refs: tuple[str, ...],
    id_attr: str,
) -> tuple[Any, ...]:
    if not refs:
        return flat_values
    requested = {_ref_tail(ref) for ref in refs}
    values: list[Any] = []
    for ref in refs:
        values.extend(by_ref.get(ref, ()))
        values.extend(by_ref.get(_ref_tail(ref), ()))
    if values:
        return tuple(values)
    return tuple(item for item in flat_values if getattr(item, id_attr, "") in requested)


def _structured_event_from_row(row: tuple[Any, ...]) -> StructuredEvent:
    return StructuredEvent(
        event_id=str(row[0]),
        instrument_ids=tuple(row[1] or ()),
        event_type=row[2] or "",
        event_subtype=_optional_text(row[3]),
        event_ts=_iso(row[4]),
        detected_at=_iso(row[5]) if row[5] else None,
        source_refs=tuple(row[6] or ()),
        relevance_score=_optional_float(row[7]),
        materiality_score=_optional_float(row[8]),
        novelty_score=_optional_float(row[9]),
        surprise_score=_optional_float(row[10]),
        sentiment_score=_optional_float(row[11]),
        confidence_score=_optional_float(row[12]),
        evidence=tuple(row[13] or ()),
        reason_codes=tuple(row[14] or ()),
        model_version=_optional_text(row[15]),
        payload=row[16] or {},
    )


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


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
