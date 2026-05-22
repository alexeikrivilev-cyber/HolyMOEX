from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class RawTextItem:
    raw_text_item_id: str
    universe_id: str | None
    instrument_ids: tuple[str, ...]
    source: str
    source_url: str | None = None
    title: str | None = None
    body: str | None = None
    language: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    source_payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawTextItem":
        source_payload = payload.get("source_payload") or payload.get("payload") or {}
        return cls(
            raw_text_item_id=str(payload.get("raw_text_item_id") or payload.get("id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or ())),
            source=str(payload.get("source") or ""),
            source_url=_optional_text(payload.get("source_url")),
            title=_optional_text(payload.get("title")),
            body=_optional_text(payload.get("body")),
            language=_optional_text(payload.get("language")),
            published_at=_optional_text(payload.get("published_at")),
            fetched_at=_optional_text(payload.get("fetched_at")),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
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
    confidence_score: float | None = None
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
            confidence_score=_optional_float(payload.get("confidence_score")),
            payload=event_payload if isinstance(event_payload, Mapping) else {},
        )


@dataclass(frozen=True)
class RawCandle:
    raw_candle_id: str
    instrument_id: str
    universe_id: str
    timeframe: str
    open_ts: str
    close_ts: str | None
    close_price: float | None
    turnover: float | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawCandle":
        source_payload = payload.get("source_payload") or payload.get("payload") or {}
        return cls(
            raw_candle_id=str(payload.get("raw_candle_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            timeframe=str(payload.get("timeframe") or ""),
            open_ts=str(payload.get("open_ts") or ""),
            close_ts=_optional_text(payload.get("close_ts")),
            close_price=_optional_float(payload.get("close_price") or payload.get("price")),
            turnover=_optional_float(payload.get("turnover")),
            provider=str(payload.get("provider") or ""),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
        )


@dataclass(frozen=True)
class FinancialStatement:
    statement_id: str
    instrument_id: str
    period: str
    reporting_standard: str
    source_refs: tuple[str, ...]
    fields: Mapping[str, Any]
    published_at: str | None = None
    confidence_score: float = 1.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FinancialStatement":
        fields = _statement_fields(payload)
        statement_id = str(
            payload.get("statement_id")
            or payload.get("financial_statement_id")
            or payload.get("raw_text_item_id")
            or payload.get("event_id")
            or payload.get("id")
            or stable_record_id("statement", fields)
        )
        source_refs = tuple(str(item) for item in (payload.get("source_refs") or fields.get("source_refs") or ()))
        return cls(
            statement_id=statement_id,
            instrument_id=str(payload.get("instrument_id") or fields.get("instrument_id") or ""),
            period=str(payload.get("period") or fields.get("period") or ""),
            reporting_standard=str(
                payload.get("reporting_standard")
                or payload.get("accounting_standard")
                or fields.get("reporting_standard")
                or fields.get("accounting_standard")
                or "unknown"
            ).lower(),
            source_refs=source_refs,
            fields=fields,
            published_at=_optional_text(payload.get("published_at") or payload.get("event_ts") or payload.get("fetched_at")),
            confidence_score=float(payload.get("confidence_score") or fields.get("confidence_score") or 1.0),
        )


@dataclass(frozen=True)
class MarketDataRecord:
    market_data_id: str
    instrument_id: str
    as_of_ts: str
    market_cap: float | None
    enterprise_value: float | None = None
    shares_outstanding: float | None = None
    price: float | None = None
    pe_current: float | None = None
    pb_current: float | None = None
    ps_current: float | None = None
    ev_ebitda_current: float | None = None
    source_refs: tuple[str, ...] = ()
    source_payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MarketDataRecord":
        source_payload = payload.get("source_payload") or payload.get("payload") or payload
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        market_data_id = str(payload.get("market_data_id") or payload.get("raw_candle_id") or payload.get("id") or "")
        market_cap = _first_float(source_payload, "market_cap", "market_capitalization", "mcap")
        shares = _first_float(source_payload, "shares_outstanding", "shares", "ordinary_shares")
        price = _first_float(source_payload, "price", "close_price", "last_price")
        if market_cap is None and shares is not None and price is not None:
            market_cap = shares * price
        return cls(
            market_data_id=market_data_id,
            instrument_id=str(payload.get("instrument_id") or source_payload.get("instrument_id") or ""),
            as_of_ts=str(
                payload.get("as_of_ts")
                or payload.get("market_cap_ts")
                or payload.get("close_ts")
                or payload.get("value_ts")
                or payload.get("received_at")
                or ""
            ),
            market_cap=market_cap,
            enterprise_value=_first_float(source_payload, "enterprise_value", "ev"),
            shares_outstanding=shares,
            price=price,
            pe_current=_first_float(source_payload, "pe_current", "pe", "p_e"),
            pb_current=_first_float(source_payload, "pb_current", "pb", "p_b"),
            ps_current=_first_float(source_payload, "ps_current", "ps", "p_s"),
            ev_ebitda_current=_first_float(source_payload, "ev_ebitda_current", "ev_ebitda"),
            source_refs=tuple(str(item) for item in (payload.get("source_refs") or ())),
            source_payload=source_payload,
        )


@dataclass(frozen=True)
class PeerGroupRecord:
    peer_group_id: str
    instrument_ids: tuple[str, ...]
    as_of_ts: str | None
    source_refs: tuple[str, ...]
    values_by_metric: Mapping[str, tuple[float, ...]]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PeerGroupRecord":
        peer_payload = payload.get("payload") or payload.get("source_payload") or payload
        if not isinstance(peer_payload, Mapping):
            peer_payload = {}
        peer_group_id = str(payload.get("peer_group_id") or payload.get("peer_group_ref") or payload.get("id") or "")
        values = _peer_metric_values(peer_payload)
        return cls(
            peer_group_id=peer_group_id,
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or peer_payload.get("instrument_ids") or ())),
            as_of_ts=_optional_text(payload.get("as_of_ts") or payload.get("close_ts") or payload.get("received_at")),
            source_refs=tuple(str(item) for item in (payload.get("source_refs") or ())),
            values_by_metric=values,
            payload=peer_payload,
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
class FundamentalSnapshot:
    fundamental_snapshot_id: str
    instrument_id: str
    period: str
    reporting_standard: str
    source_refs: tuple[str, ...]
    features_ref: str
    confidence_score: float
    calculation_version: str
    as_of_ts: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fundamental_snapshot_id": self.fundamental_snapshot_id,
            "instrument_id": self.instrument_id,
            "period": self.period,
            "reporting_standard": self.reporting_standard,
            "source_refs": list(self.source_refs),
            "features_ref": self.features_ref,
            "confidence_score": self.confidence_score,
            "calculation_version": self.calculation_version,
            "as_of_ts": self.as_of_ts,
            "quality_flags": list(self.quality_flags),
            "payload": dict(self.payload),
        }


class FundamentalValuationRepository(Protocol):
    def list_financial_statements(
        self,
        statement_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FinancialStatement, ...]:
        ...

    def list_market_data(
        self,
        market_cap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketDataRecord, ...]:
        ...

    def list_peer_groups(
        self,
        peer_group_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[PeerGroupRecord, ...]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_fundamental_snapshot(self, record: FundamentalSnapshot) -> str:
        ...


class InMemoryFundamentalValuationRepository:
    def __init__(
        self,
        financial_statements: tuple[Mapping[str, Any] | FinancialStatement, ...] = (),
        market_data: tuple[Mapping[str, Any] | MarketDataRecord, ...] = (),
        market_cap_data: tuple[Mapping[str, Any] | MarketDataRecord, ...] = (),
        market_cap_records: tuple[Mapping[str, Any] | MarketDataRecord, ...] = (),
        peer_groups: tuple[Mapping[str, Any] | PeerGroupRecord, ...] = (),
        peer_group_data: tuple[Mapping[str, Any] | PeerGroupRecord, ...] = (),
        raw_text_items: tuple[Mapping[str, Any] | RawTextItem, ...] = (),
        structured_events: tuple[Mapping[str, Any] | StructuredEvent, ...] = (),
        raw_candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
    ) -> None:
        self.financial_statements = tuple(
            item if isinstance(item, FinancialStatement) else FinancialStatement.from_mapping(item)
            for item in financial_statements
        )
        market_items = (*market_data, *market_cap_data, *market_cap_records)
        self.market_data = tuple(
            item if isinstance(item, MarketDataRecord) else MarketDataRecord.from_mapping(item)
            for item in market_items
        )
        self.peer_groups = tuple(
            item if isinstance(item, PeerGroupRecord) else PeerGroupRecord.from_mapping(item)
            for item in (*peer_groups, *peer_group_data)
        )
        self.raw_text_items = tuple(
            item if isinstance(item, RawTextItem) else RawTextItem.from_mapping(item)
            for item in raw_text_items
        )
        self.structured_events = tuple(
            item if isinstance(item, StructuredEvent) else StructuredEvent.from_mapping(item)
            for item in structured_events
        )
        self.raw_candles = tuple(
            item if isinstance(item, RawCandle) else RawCandle.from_mapping(item)
            for item in raw_candles
        )
        self.feature_records: list[FeatureRecord] = []
        self.fundamental_snapshots: list[FundamentalSnapshot] = []

    def list_financial_statements(
        self,
        statement_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FinancialStatement, ...]:
        del universe_id
        requested = set(instrument_ids)
        ref_filter = {_ref_tail(ref) for ref in statement_refs}
        statements = list(self.financial_statements)
        statements.extend(_statement_from_raw_text(item) for item in self.raw_text_items)
        statements.extend(_statement_from_event(event) for event in self.structured_events)
        filtered = [
            item
            for item in statements
            if item.instrument_id in requested
            and (not ref_filter or _ref_tail(item.statement_id) in ref_filter or bool(ref_filter & {_ref_tail(ref) for ref in item.source_refs}))
            and _timestamp_in_range_or_missing(item.published_at, from_ts, to_ts)
        ]
        return tuple(sorted(filtered, key=lambda item: (item.instrument_id, item.period, item.published_at or "")))

    def list_market_data(
        self,
        market_cap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketDataRecord, ...]:
        requested = set(instrument_ids)
        ref_tail = _ref_tail(market_cap_ref) if market_cap_ref else ""
        market_records = list(self.market_data)
        market_records.extend(_market_data_from_candle(candle) for candle in self.raw_candles)
        filtered = [
            item
            for item in market_records
            if item.instrument_id in requested
            and (not ref_tail or _ref_tail(item.market_data_id) == ref_tail or ref_tail in {_ref_tail(ref) for ref in item.source_refs})
            and _timestamp_in_range_or_missing(item.as_of_ts, from_ts, to_ts)
        ]
        del universe_id
        return tuple(sorted(filtered, key=lambda item: (item.instrument_id, item.as_of_ts or "")))

    def list_peer_groups(
        self,
        peer_group_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[PeerGroupRecord, ...]:
        del universe_id
        requested = set(instrument_ids)
        ref_tail = _ref_tail(peer_group_ref) if peer_group_ref else ""
        peer_groups = list(self.peer_groups)
        peer_groups.extend(_peer_group_from_candle(candle) for candle in self.raw_candles if _peer_metric_values(candle.source_payload))
        filtered = [
            item
            for item in peer_groups
            if (not item.instrument_ids or bool(requested & set(item.instrument_ids)))
            and (not ref_tail or _ref_tail(item.peer_group_id) == ref_tail or ref_tail in {_ref_tail(ref) for ref in item.source_refs})
            and _timestamp_in_range_or_missing(item.as_of_ts, from_ts, to_ts)
        ]
        return tuple(sorted(filtered, key=lambda item: item.as_of_ts or ""))

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_fundamental_snapshot(self, record: FundamentalSnapshot) -> str:
        self.fundamental_snapshots.append(record)
        return f"features.fundamental_snapshot:{record.fundamental_snapshot_id}"


class PostgresFundamentalValuationRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_financial_statements(
        self,
        statement_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FinancialStatement, ...]:
        ref_ids = [_ref_tail(ref) for ref in statement_refs]
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ref_ids:
                    cur.execute(
                        """
                        SELECT raw_text_item_id, universe_id, instrument_ids, source,
                               source_url, title, body, language, published_at,
                               fetched_at, source_payload
                          FROM raw_text.raw_text_item
                         WHERE raw_text_item_id::text = ANY(%s)
                            OR source_payload ->> 'statement_id' = ANY(%s)
                        """,
                        (ref_ids, ref_ids),
                    )
                else:
                    cur.execute(
                        """
                        SELECT raw_text_item_id, universe_id, instrument_ids, source,
                               source_url, title, body, language, published_at,
                               fetched_at, source_payload
                          FROM raw_text.raw_text_item
                         WHERE universe_id = %s
                           AND (cardinality(instrument_ids) = 0 OR instrument_ids && %s)
                           AND COALESCE(published_at, fetched_at) BETWEEN %s AND %s
                        """,
                        (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                    )
                text_rows = cur.fetchall()

                if ref_ids:
                    cur.execute(
                        """
                        SELECT event_id, instrument_ids, event_type, event_subtype,
                               event_ts, detected_at, source_refs, confidence_score,
                               payload
                          FROM events.structured_event
                         WHERE event_id = ANY(%s)
                        """,
                        (ref_ids,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT event_id, instrument_ids, event_type, event_subtype,
                               event_ts, detected_at, source_refs, confidence_score,
                               payload
                          FROM events.structured_event
                         WHERE event_type = 'earnings'
                           AND event_ts BETWEEN %s AND %s
                           AND (cardinality(instrument_ids) = 0 OR instrument_ids && %s)
                        """,
                        (parse_utc_iso(from_ts), parse_utc_iso(to_ts), list(instrument_ids)),
                    )
                event_rows = cur.fetchall()

        raw_items = tuple(
            RawTextItem(
                raw_text_item_id=str(row[0]),
                universe_id=row[1],
                instrument_ids=tuple(row[2] or ()),
                source=row[3] or "",
                source_url=row[4],
                title=row[5],
                body=row[6],
                language=row[7],
                published_at=_iso(row[8]) if row[8] else None,
                fetched_at=_iso(row[9]) if row[9] else None,
                source_payload=row[10] or {},
            )
            for row in text_rows
        )
        events = tuple(
            StructuredEvent(
                event_id=str(row[0]),
                instrument_ids=tuple(row[1] or ()),
                event_type=row[2] or "",
                event_subtype=row[3],
                event_ts=_iso(row[4]),
                detected_at=_iso(row[5]) if row[5] else None,
                source_refs=tuple(row[6] or ()),
                confidence_score=_optional_float(row[7]),
                payload=row[8] or {},
            )
            for row in event_rows
        )
        statements = tuple(_statement_from_raw_text(item) for item in raw_items) + tuple(
            _statement_from_event(event) for event in events
        )
        requested = set(instrument_ids)
        return tuple(item for item in statements if item.instrument_id in requested)

    def list_market_data(
        self,
        market_cap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketDataRecord, ...]:
        ref_id = _ref_tail(market_cap_ref) if market_cap_ref else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ref_id:
                    cur.execute(
                        """
                        SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                               open_ts, close_ts, close_price, turnover, provider,
                               source_payload, received_at
                          FROM raw_market.raw_candle
                         WHERE raw_candle_id::text = %s
                            OR source_payload ->> 'market_cap_ref' = %s
                         ORDER BY close_ts
                        """,
                        (ref_id, ref_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                               open_ts, close_ts, close_price, turnover, provider,
                               source_payload, received_at
                          FROM raw_market.raw_candle
                         WHERE universe_id = %s
                           AND instrument_id = ANY(%s)
                           AND close_ts BETWEEN %s AND %s
                         ORDER BY instrument_id, close_ts
                        """,
                        (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                    )
                rows = cur.fetchall()
        candles = tuple(
            RawCandle(
                raw_candle_id=str(row[0]),
                instrument_id=row[1] or "",
                universe_id=row[2] or "",
                timeframe=row[3] or "",
                open_ts=_iso(row[4]),
                close_ts=_iso(row[5]) if row[5] else None,
                close_price=_optional_float(row[6]),
                turnover=_optional_float(row[7]),
                provider=row[8] or "",
                source_payload=row[9] or {},
                received_at=_iso(row[10]) if row[10] else None,
            )
            for row in rows
        )
        return tuple(_market_data_from_candle(candle) for candle in candles if candle.instrument_id in set(instrument_ids))

    def list_peer_groups(
        self,
        peer_group_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[PeerGroupRecord, ...]:
        market_data = self.list_market_data(peer_group_ref, universe_id, instrument_ids, from_ts, to_ts)
        groups = [
            PeerGroupRecord.from_mapping(
                {
                    "peer_group_id": peer_group_ref or item.market_data_id,
                    "instrument_ids": [item.instrument_id],
                    "as_of_ts": item.as_of_ts,
                    "source_refs": item.source_refs,
                    "payload": item.source_payload,
                }
            )
            for item in market_data
            if _peer_metric_values(item.source_payload)
        ]
        return tuple(groups)

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

    def save_fundamental_snapshot(self, record: FundamentalSnapshot) -> str:
        from psycopg.types.json import Jsonb

        payload = {
            **dict(record.payload),
            "fundamental_snapshot_id": record.fundamental_snapshot_id,
            "period": record.period,
            "reporting_standard": record.reporting_standard,
            "features_ref": record.features_ref,
            "confidence_score": record.confidence_score,
            "quality_flags": list(record.quality_flags),
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO features.fundamental_snapshot (
                        fundamental_snapshot_id, instrument_id, as_of_date,
                        source_refs, payload, source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (fundamental_snapshot_id) DO UPDATE SET
                        source_refs = EXCLUDED.source_refs,
                        payload = EXCLUDED.payload,
                        source_module = EXCLUDED.source_module,
                        calculation_version = EXCLUDED.calculation_version
                    """,
                    (
                        uuid.UUID(record.fundamental_snapshot_id),
                        record.instrument_id,
                        parse_utc_iso(record.as_of_ts).date(),
                        list(record.source_refs),
                        Jsonb(payload),
                        "Fundamental & Valuation Module",
                        record.calculation_version,
                    ),
                )
        return f"features.fundamental_snapshot:{record.fundamental_snapshot_id}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def stable_uuid_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))


def _statement_from_raw_text(item: RawTextItem) -> FinancialStatement:
    payload = dict(item.source_payload)
    fields = _statement_fields(payload)
    instrument_id = str(payload.get("instrument_id") or fields.get("instrument_id") or (item.instrument_ids[0] if item.instrument_ids else ""))
    source_ref = f"raw_text.raw_text_item:{item.raw_text_item_id}"
    return FinancialStatement(
        statement_id=str(payload.get("statement_id") or item.raw_text_item_id),
        instrument_id=instrument_id,
        period=str(payload.get("period") or fields.get("period") or ""),
        reporting_standard=str(payload.get("reporting_standard") or payload.get("accounting_standard") or fields.get("reporting_standard") or "unknown").lower(),
        source_refs=tuple(dict.fromkeys((source_ref, *tuple(str(ref) for ref in payload.get("source_refs", ()))))),
        fields=fields,
        published_at=item.published_at or item.fetched_at,
        confidence_score=float(payload.get("confidence_score") or 1.0),
    )


def _statement_from_event(event: StructuredEvent) -> FinancialStatement:
    payload = dict(event.payload)
    fields = _statement_fields(payload)
    instrument_id = str(payload.get("instrument_id") or fields.get("instrument_id") or (event.instrument_ids[0] if event.instrument_ids else ""))
    source_ref = f"events.structured_event:{event.event_id}"
    return FinancialStatement(
        statement_id=str(payload.get("statement_id") or event.event_id),
        instrument_id=instrument_id,
        period=str(payload.get("period") or fields.get("period") or ""),
        reporting_standard=str(payload.get("reporting_standard") or payload.get("accounting_standard") or fields.get("reporting_standard") or "unknown").lower(),
        source_refs=tuple(dict.fromkeys((source_ref, *event.source_refs, *tuple(str(ref) for ref in payload.get("source_refs", ()))))),
        fields=fields,
        published_at=event.event_ts,
        confidence_score=float(event.confidence_score or payload.get("confidence_score") or 1.0),
    )


def _market_data_from_candle(candle: RawCandle) -> MarketDataRecord:
    payload = {
        **dict(candle.source_payload),
        "raw_candle_id": candle.raw_candle_id,
        "instrument_id": candle.instrument_id,
        "close_ts": candle.close_ts,
        "close_price": candle.close_price,
        "received_at": candle.received_at,
    }
    record = MarketDataRecord.from_mapping(payload)
    source_ref = f"raw_market.raw_candle:{candle.raw_candle_id}"
    return MarketDataRecord(
        market_data_id=record.market_data_id or candle.raw_candle_id,
        instrument_id=record.instrument_id or candle.instrument_id,
        as_of_ts=record.as_of_ts or candle.close_ts or candle.received_at or "",
        market_cap=record.market_cap,
        enterprise_value=record.enterprise_value,
        shares_outstanding=record.shares_outstanding,
        price=record.price,
        pe_current=record.pe_current,
        pb_current=record.pb_current,
        ps_current=record.ps_current,
        ev_ebitda_current=record.ev_ebitda_current,
        source_refs=tuple(dict.fromkeys((source_ref, *record.source_refs))),
        source_payload=record.source_payload,
    )


def _peer_group_from_candle(candle: RawCandle) -> PeerGroupRecord:
    source_ref = f"raw_market.raw_candle:{candle.raw_candle_id}"
    return PeerGroupRecord.from_mapping(
        {
            "peer_group_id": candle.source_payload.get("peer_group_ref") or candle.raw_candle_id,
            "instrument_ids": [candle.instrument_id],
            "as_of_ts": candle.close_ts,
            "source_refs": [source_ref],
            "payload": candle.source_payload,
        }
    )


def _statement_fields(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    candidates: list[Mapping[str, Any]] = [payload]
    for key in ("financial_statement", "financial_fields", "statement", "fields", "metrics"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            candidates.insert(0, nested)
    fields: dict[str, Any] = {}
    for candidate in candidates:
        fields.update(candidate)
    return fields


def _peer_metric_values(payload: Mapping[str, Any]) -> Mapping[str, tuple[float, ...]]:
    values: dict[str, tuple[float, ...]] = {}
    containers: list[Mapping[str, Any]] = [payload]
    for key in ("peer_values", "sector_values", "sector_multiples", "peer_group", "metrics"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            containers.append(nested)
    for container in containers:
        for source_key, metric_name in (
            ("ev_ebitda_sector", "ev_ebitda"),
            ("ev_ebitda", "ev_ebitda"),
            ("pe_sector", "pe"),
            ("pe", "pe"),
            ("pb_sector", "pb"),
            ("pb", "pb"),
            ("ps_sector", "ps"),
            ("ps", "ps"),
            ("earnings_yield_sector", "earnings_yield"),
            ("earnings_yield", "earnings_yield"),
            ("fcf_yield_sector", "fcf_yield"),
            ("fcf_yield", "fcf_yield"),
            ("roe_sector", "roe"),
            ("roe", "roe"),
            ("roic_sector", "roic"),
            ("roic", "roic"),
            ("net_debt_ebitda_sector", "net_debt_ebitda"),
            ("net_debt_ebitda", "net_debt_ebitda"),
            ("interest_coverage_sector", "interest_coverage"),
            ("interest_coverage", "interest_coverage"),
            ("margin_change_sector", "margin_change"),
            ("margin_change", "margin_change"),
        ):
            if source_key in container:
                parsed = _float_sequence(container.get(source_key))
                if parsed:
                    values[metric_name] = parsed
    peers = payload.get("peers")
    if isinstance(peers, list):
        for metric_name in (
            "ev_ebitda",
            "pe",
            "pb",
            "ps",
            "earnings_yield",
            "fcf_yield",
            "roe",
            "roic",
            "net_debt_ebitda",
            "interest_coverage",
            "margin_change",
        ):
            parsed = _float_sequence(peer.get(metric_name) for peer in peers if isinstance(peer, Mapping))
            if parsed:
                values[metric_name] = parsed
    return values


def _timestamp_in_range_or_missing(timestamp: str | None, from_ts: str, to_ts: str) -> bool:
    if not timestamp:
        return True
    parsed = parse_utc_iso(timestamp)
    return parse_utc_iso(from_ts) <= parsed <= parse_utc_iso(to_ts)


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _first_float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(payload.get(key))
        if value is not None:
            return value
    return None


def _float_sequence(value: Any) -> tuple[float, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, Mapping):
        value = value.values()
    if isinstance(value, (str, bytes)):
        return (_optional_float(value),) if _optional_float(value) is not None else ()
    try:
        iterator = iter(value)
    except TypeError:
        parsed = _optional_float(value)
        return () if parsed is None else (parsed,)
    parsed_values = tuple(_optional_float(item) for item in iterator)
    return tuple(item for item in parsed_values if item is not None)


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


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
