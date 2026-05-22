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
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            raw_text_item_id=str(payload.get("raw_text_item_id") or payload.get("id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or source_payload.get("instrument_ids") or ())),
            source=str(payload.get("source") or source_payload.get("source") or ""),
            source_url=_optional_text(payload.get("source_url") or source_payload.get("source_url")),
            title=_optional_text(payload.get("title") or source_payload.get("title")),
            body=_optional_text(payload.get("body") or source_payload.get("body") or source_payload.get("text")),
            language=_optional_text(payload.get("language") or source_payload.get("language")),
            published_at=_optional_text(payload.get("published_at") or source_payload.get("published_at")),
            fetched_at=_optional_text(payload.get("fetched_at") or source_payload.get("fetched_at")),
            source_payload=source_payload,
        )


@dataclass(frozen=True)
class StructuredEvent:
    event_id: str
    instrument_ids: tuple[str, ...]
    event_type: str
    event_subtype: str
    event_ts: str
    detected_at: str
    source_refs: tuple[str, ...]
    relevance_score: float
    materiality_score: float
    novelty_score: float
    surprise_score: float
    sentiment_score: float
    confidence_score: float
    evidence: tuple[str, ...]
    reason_codes: tuple[str, ...]
    model_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StructuredEvent":
        event_payload = payload.get("payload") or {}
        if not isinstance(event_payload, Mapping):
            event_payload = {}
        event_ts = str(payload.get("event_ts") or event_payload.get("event_ts") or "")
        return cls(
            event_id=str(payload.get("event_id") or payload.get("id") or ""),
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or event_payload.get("instrument_ids") or ())),
            event_type=str(payload.get("event_type") or event_payload.get("event_type") or ""),
            event_subtype=str(payload.get("event_subtype") or event_payload.get("event_subtype") or ""),
            event_ts=event_ts,
            detected_at=str(payload.get("detected_at") or event_payload.get("detected_at") or event_ts),
            source_refs=tuple(str(item) for item in (payload.get("source_refs") or event_payload.get("source_refs") or ())),
            relevance_score=float(payload.get("relevance_score") or event_payload.get("relevance_score") or 0.0),
            materiality_score=float(payload.get("materiality_score") or event_payload.get("materiality_score") or 0.0),
            novelty_score=float(payload.get("novelty_score") or event_payload.get("novelty_score") or 0.0),
            surprise_score=float(payload.get("surprise_score") or event_payload.get("surprise_score") or 0.0),
            sentiment_score=float(payload.get("sentiment_score") or event_payload.get("sentiment_score") or 0.0),
            confidence_score=float(payload.get("confidence_score") or event_payload.get("confidence_score") or 1.0),
            evidence=tuple(str(item) for item in (payload.get("evidence") or event_payload.get("evidence") or ())),
            reason_codes=tuple(str(item) for item in (payload.get("reason_codes") or event_payload.get("reason_codes") or ())),
            model_version=str(payload.get("model_version") or event_payload.get("model_version") or "structured_event"),
            payload=event_payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "instrument_ids": list(self.instrument_ids),
            "event_type": self.event_type,
            "event_subtype": self.event_subtype,
            "event_ts": self.event_ts,
            "detected_at": self.detected_at,
            "source_refs": list(self.source_refs),
            "relevance_score": self.relevance_score,
            "materiality_score": self.materiality_score,
            "novelty_score": self.novelty_score,
            "surprise_score": self.surprise_score,
            "sentiment_score": self.sentiment_score,
            "confidence_score": self.confidence_score,
            "evidence": list(self.evidence),
            "reason_codes": list(self.reason_codes),
            "model_version": self.model_version,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class RawCandle:
    raw_candle_id: str
    instrument_id: str
    universe_id: str | None
    timeframe: str
    open_ts: str
    close_ts: str | None
    open_price: float | None
    close_price: float | None
    adjusted_open_price: float | None = None
    adjusted_close_price: float | None = None
    source_refs: tuple[str, ...] = ()
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawCandle":
        source_payload = payload.get("source_payload") or payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        raw_candle_id = str(payload.get("raw_candle_id") or payload.get("id") or source_payload.get("raw_candle_id") or "")
        return cls(
            raw_candle_id=raw_candle_id,
            instrument_id=str(payload.get("instrument_id") or source_payload.get("instrument_id") or ""),
            universe_id=_optional_text(payload.get("universe_id") or source_payload.get("universe_id")),
            timeframe=str(payload.get("timeframe") or source_payload.get("timeframe") or ""),
            open_ts=str(payload.get("open_ts") or source_payload.get("open_ts") or ""),
            close_ts=_optional_text(payload.get("close_ts") or source_payload.get("close_ts")),
            open_price=_first_float(payload, source_payload, "open_price", "open"),
            close_price=_first_float(payload, source_payload, "close_price", "close", "price", "last_price"),
            adjusted_open_price=_first_float(payload, source_payload, "adjusted_open_price", "adj_open", "open_adjusted"),
            adjusted_close_price=_first_float(payload, source_payload, "adjusted_close_price", "adj_close", "close_adjusted"),
            source_refs=tuple(
                str(item)
                for item in (
                    payload.get("source_refs")
                    or ((f"raw_market.raw_candle:{raw_candle_id}",) if raw_candle_id else ())
                )
            ),
            source_payload=source_payload,
            received_at=_optional_text(payload.get("received_at") or source_payload.get("received_at")),
        )


@dataclass(frozen=True)
class FinancialExpectation:
    expectation_id: str
    instrument_id: str
    period: str
    estimate_source: str
    model_version: str
    fields: Mapping[str, Any]
    source_refs: tuple[str, ...]
    as_of_ts: str | None = None
    confidence_score: float = 1.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FinancialExpectation":
        fields = _nested_fields(payload, "expectations", "financial_expectations", "expected", "fields")
        expectation_id = str(
            payload.get("expectation_id")
            or payload.get("financial_expectation_id")
            or payload.get("id")
            or stable_record_id("expectation", fields)
        )
        return cls(
            expectation_id=expectation_id,
            instrument_id=str(payload.get("instrument_id") or fields.get("instrument_id") or ""),
            period=str(payload.get("period") or fields.get("period") or ""),
            estimate_source=str(payload.get("estimate_source") or fields.get("estimate_source") or ""),
            model_version=str(payload.get("model_version") or fields.get("model_version") or ""),
            fields=fields,
            source_refs=tuple(str(item) for item in (payload.get("source_refs") or fields.get("source_refs") or ())),
            as_of_ts=_optional_text(payload.get("as_of_ts") or payload.get("published_at") or fields.get("as_of_ts")),
            confidence_score=float(payload.get("confidence_score") or fields.get("confidence_score") or 1.0),
        )


@dataclass(frozen=True)
class HistoricalGapRecord:
    historical_gap_id: str
    instrument_id: str
    as_of_ts: str | None
    gap_returns: tuple[float, ...]
    closed_within_20d: tuple[bool, ...]
    days_to_close: tuple[float, ...]
    volatility_adjustment: float | None
    adjusted_prices: bool
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "HistoricalGapRecord":
        source_payload = payload.get("payload") or payload.get("source_payload") or payload
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        gap_events = source_payload.get("gap_events")
        if isinstance(gap_events, list):
            gap_returns = tuple(
                value
                for value in (_optional_float(item.get("gap_return")) if isinstance(item, Mapping) else None for item in gap_events)
                if value is not None
            )
            closed = tuple(
                bool(item.get("closed_within_20d"))
                for item in gap_events
                if isinstance(item, Mapping) and item.get("closed_within_20d") is not None
            )
            days = tuple(
                value
                for value in (_optional_float(item.get("days_to_close")) if isinstance(item, Mapping) else None for item in gap_events)
                if value is not None
            )
        else:
            gap_returns = _float_tuple(source_payload.get("gap_returns") or source_payload.get("historical_gap_returns"))
            closed = tuple(bool(item) for item in (source_payload.get("closed_within_20d") or ()))
            days = _float_tuple(source_payload.get("days_to_close") or source_payload.get("gap_close_days"))
        adjusted = bool(
            payload.get("adjusted_prices")
            or source_payload.get("adjusted_prices")
            or source_payload.get("uses_adjusted_prices")
            or source_payload.get("historical_gap_uses_adjusted_prices")
        )
        gap_id = str(payload.get("historical_gap_id") or payload.get("id") or stable_record_id("historical_gap", source_payload))
        return cls(
            historical_gap_id=gap_id,
            instrument_id=str(payload.get("instrument_id") or source_payload.get("instrument_id") or ""),
            as_of_ts=_optional_text(payload.get("as_of_ts") or source_payload.get("as_of_ts")),
            gap_returns=gap_returns,
            closed_within_20d=closed,
            days_to_close=days,
            volatility_adjustment=_optional_float(source_payload.get("volatility_adjustment")),
            adjusted_prices=adjusted,
            source_refs=tuple(str(item) for item in (payload.get("source_refs") or source_payload.get("source_refs") or ())),
            payload=source_payload,
        )


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    instrument_id: str
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float | None
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
class EarningsDividendRecord:
    earnings_dividend_record_id: str
    instrument_id: str
    event_id: str | None
    record_type: str
    as_of_ts: str
    payload: Mapping[str, Any]
    source_module: str
    calculation_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "earnings_dividend_record_id": self.earnings_dividend_record_id,
            "instrument_id": self.instrument_id,
            "event_id": self.event_id,
            "record_type": self.record_type,
            "as_of_ts": self.as_of_ts,
            "payload": dict(self.payload),
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
        }


class EarningsDividendIntelligenceRepository(Protocol):
    def list_raw_text_items(
        self,
        raw_text_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTextItem, ...]:
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

    def list_financial_expectations(
        self,
        expectation_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FinancialExpectation, ...]:
        ...

    def list_market_candles(
        self,
        historical_gap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        ...

    def list_historical_gap_records(
        self,
        historical_gap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[HistoricalGapRecord, ...]:
        ...

    def save_structured_event(self, event: StructuredEvent) -> str:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_earnings_dividend_record(self, record: EarningsDividendRecord) -> str:
        ...


class InMemoryEarningsDividendIntelligenceRepository:
    def __init__(
        self,
        raw_text_items: tuple[Mapping[str, Any] | RawTextItem, ...] = (),
        structured_events: tuple[Mapping[str, Any] | StructuredEvent, ...] = (),
        financial_expectations: tuple[Mapping[str, Any] | FinancialExpectation, ...] = (),
        expectations: tuple[Mapping[str, Any] | FinancialExpectation, ...] = (),
        raw_candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
        market_candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
        historical_gap_records: tuple[Mapping[str, Any] | HistoricalGapRecord, ...] = (),
    ) -> None:
        self.raw_text_items = tuple(
            item if isinstance(item, RawTextItem) else RawTextItem.from_mapping(item)
            for item in raw_text_items
        )
        self.structured_events = tuple(
            item if isinstance(item, StructuredEvent) else StructuredEvent.from_mapping(item)
            for item in structured_events
        )
        self.financial_expectations = tuple(
            item if isinstance(item, FinancialExpectation) else FinancialExpectation.from_mapping(item)
            for item in (*financial_expectations, *expectations)
        )
        self.raw_candles = tuple(
            item if isinstance(item, RawCandle) else RawCandle.from_mapping(item)
            for item in (*raw_candles, *market_candles)
        )
        self.historical_gap_records = tuple(
            item if isinstance(item, HistoricalGapRecord) else HistoricalGapRecord.from_mapping(item)
            for item in historical_gap_records
        )
        self.saved_structured_events: list[StructuredEvent] = []
        self.feature_records: list[FeatureRecord] = []
        self.earnings_dividend_records: list[EarningsDividendRecord] = []

    def list_raw_text_items(
        self,
        raw_text_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTextItem, ...]:
        requested_refs = {_ref_tail(ref) for ref in raw_text_refs}
        requested_instruments = set(instrument_ids)
        items = []
        for item in self.raw_text_items:
            if requested_refs and item.raw_text_item_id not in requested_refs:
                continue
            if not requested_refs:
                if item.universe_id and item.universe_id != universe_id:
                    continue
                if requested_instruments and item.instrument_ids and not (requested_instruments & set(item.instrument_ids)):
                    continue
                timestamp = item.published_at or item.fetched_at
                if timestamp and not _timestamp_in_range(timestamp, from_ts, to_ts):
                    continue
            items.append(item)
        return tuple(sorted(items, key=lambda item: item.published_at or item.fetched_at or item.raw_text_item_id))

    def list_structured_events(
        self,
        event_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[StructuredEvent, ...]:
        del universe_id
        requested_refs = {_ref_tail(ref) for ref in event_refs}
        requested_instruments = set(instrument_ids)
        events = []
        for event in self.structured_events:
            if requested_refs and event.event_id not in requested_refs:
                continue
            if not requested_refs:
                if event.event_type not in {"earnings", "dividend"}:
                    continue
                if requested_instruments and event.instrument_ids and not (requested_instruments & set(event.instrument_ids)):
                    continue
                if event.event_ts and not _timestamp_in_range(event.event_ts, from_ts, to_ts):
                    continue
            events.append(event)
        return tuple(sorted(events, key=lambda item: item.event_ts or item.event_id))

    def list_financial_expectations(
        self,
        expectation_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FinancialExpectation, ...]:
        del universe_id
        requested = set(instrument_ids)
        ref_tail = _ref_tail(expectation_ref) if expectation_ref else ""
        expectations = list(self.financial_expectations)
        expectations.extend(_expectation_from_raw_text(item) for item in self.raw_text_items if _has_expectation_payload(item.source_payload))
        expectations.extend(_expectation_from_event(event) for event in self.structured_events if _has_expectation_payload(event.payload))
        filtered = [
            item
            for item in expectations
            if item.instrument_id in requested
            and (not ref_tail or item.expectation_id == ref_tail or ref_tail in {_ref_tail(ref) for ref in item.source_refs})
            and _timestamp_in_range_or_missing(item.as_of_ts, from_ts, to_ts)
        ]
        return tuple(sorted(filtered, key=lambda item: item.as_of_ts or ""))

    def list_market_candles(
        self,
        historical_gap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        requested = set(instrument_ids)
        ref_tail = _ref_tail(historical_gap_ref) if historical_gap_ref else ""
        filtered = [
            candle
            for candle in self.raw_candles
            if candle.instrument_id in requested
            and (not candle.universe_id or candle.universe_id == universe_id)
            and (not ref_tail or candle.raw_candle_id == ref_tail or ref_tail in {_ref_tail(ref) for ref in candle.source_refs})
            and _timestamp_in_range_or_missing(candle.close_ts or candle.open_ts, from_ts, to_ts)
        ]
        return tuple(sorted(filtered, key=lambda item: item.close_ts or item.open_ts))

    def list_historical_gap_records(
        self,
        historical_gap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[HistoricalGapRecord, ...]:
        del universe_id
        requested = set(instrument_ids)
        ref_tail = _ref_tail(historical_gap_ref) if historical_gap_ref else ""
        records = list(self.historical_gap_records)
        for candle in self.raw_candles:
            payload = candle.source_payload
            if any(key in payload for key in ("gap_events", "gap_returns", "historical_gap_returns")):
                records.append(
                    HistoricalGapRecord.from_mapping(
                        {
                            "historical_gap_id": candle.raw_candle_id,
                            "instrument_id": candle.instrument_id,
                            "as_of_ts": candle.close_ts or candle.received_at,
                            "source_refs": list(candle.source_refs),
                            "payload": payload,
                        }
                    )
                )
        filtered = [
            item
            for item in records
            if item.instrument_id in requested
            and (not ref_tail or item.historical_gap_id == ref_tail or ref_tail in {_ref_tail(ref) for ref in item.source_refs})
            and _timestamp_in_range_or_missing(item.as_of_ts, from_ts, to_ts)
        ]
        return tuple(sorted(filtered, key=lambda item: item.as_of_ts or ""))

    def save_structured_event(self, event: StructuredEvent) -> str:
        self.saved_structured_events = [item for item in self.saved_structured_events if item.event_id != event.event_id]
        self.saved_structured_events.append(event)
        return f"events.structured_event:{event.event_id}"

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records = [item for item in self.feature_records if item.feature_id != record.feature_id]
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_earnings_dividend_record(self, record: EarningsDividendRecord) -> str:
        self.earnings_dividend_records = [
            item
            for item in self.earnings_dividend_records
            if item.earnings_dividend_record_id != record.earnings_dividend_record_id
        ]
        self.earnings_dividend_records.append(record)
        return f"events.earnings_dividend_record:{record.earnings_dividend_record_id}"


class PostgresEarningsDividendIntelligenceRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_raw_text_items(
        self,
        raw_text_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTextItem, ...]:
        ref_ids = [_ref_tail(ref) for ref in raw_text_refs]
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
                            OR source_payload ->> 'report_id' = ANY(%s)
                         ORDER BY COALESCE(published_at, fetched_at)
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
                         ORDER BY COALESCE(published_at, fetched_at)
                        """,
                        (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                    )
                rows = cur.fetchall()
        return tuple(_raw_text_from_row(row) for row in rows)

    def list_structured_events(
        self,
        event_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[StructuredEvent, ...]:
        del universe_id
        ref_ids = [_ref_tail(ref) for ref in event_refs]
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ref_ids:
                    cur.execute(
                        """
                        SELECT event_id, instrument_ids, event_type, event_subtype,
                               event_ts, detected_at, source_refs, relevance_score,
                               materiality_score, novelty_score, surprise_score,
                               sentiment_score, confidence_score, evidence,
                               reason_codes, model_version, payload
                          FROM events.structured_event
                         WHERE event_id = ANY(%s)
                         ORDER BY event_ts
                        """,
                        (ref_ids,),
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
                         WHERE event_type IN ('earnings', 'dividend')
                           AND event_ts BETWEEN %s AND %s
                           AND (cardinality(instrument_ids) = 0 OR instrument_ids && %s)
                         ORDER BY event_ts
                        """,
                        (parse_utc_iso(from_ts), parse_utc_iso(to_ts), list(instrument_ids)),
                    )
                rows = cur.fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def list_financial_expectations(
        self,
        expectation_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FinancialExpectation, ...]:
        raw_items = self.list_raw_text_items((expectation_ref,) if expectation_ref else (), universe_id, instrument_ids, from_ts, to_ts)
        events = self.list_structured_events((expectation_ref,) if expectation_ref else (), universe_id, instrument_ids, from_ts, to_ts)
        expectations = [
            _expectation_from_raw_text(item)
            for item in raw_items
            if _has_expectation_payload(item.source_payload)
        ]
        expectations.extend(
            _expectation_from_event(event)
            for event in events
            if _has_expectation_payload(event.payload)
        )
        requested = set(instrument_ids)
        return tuple(item for item in expectations if item.instrument_id in requested)

    def list_market_candles(
        self,
        historical_gap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        ref_id = _ref_tail(historical_gap_ref) if historical_gap_ref else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ref_id:
                    cur.execute(
                        """
                        SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                               open_ts, close_ts, open_price, close_price,
                               source_payload, received_at
                          FROM raw_market.raw_candle
                         WHERE raw_candle_id::text = %s
                            OR source_payload ->> 'historical_gap_ref' = %s
                         ORDER BY instrument_id, close_ts
                        """,
                        (ref_id, ref_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                               open_ts, close_ts, open_price, close_price,
                               source_payload, received_at
                          FROM raw_market.raw_candle
                         WHERE universe_id = %s
                           AND instrument_id = ANY(%s)
                           AND COALESCE(close_ts, open_ts) BETWEEN %s AND %s
                         ORDER BY instrument_id, COALESCE(close_ts, open_ts)
                        """,
                        (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                    )
                rows = cur.fetchall()
        return tuple(_candle_from_row(row) for row in rows)

    def list_historical_gap_records(
        self,
        historical_gap_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[HistoricalGapRecord, ...]:
        records = []
        for candle in self.list_market_candles(historical_gap_ref, universe_id, instrument_ids, from_ts, to_ts):
            if any(key in candle.source_payload for key in ("gap_events", "gap_returns", "historical_gap_returns")):
                records.append(
                    HistoricalGapRecord.from_mapping(
                        {
                            "historical_gap_id": candle.raw_candle_id,
                            "instrument_id": candle.instrument_id,
                            "as_of_ts": candle.close_ts or candle.received_at,
                            "source_refs": list(candle.source_refs),
                            "payload": candle.source_payload,
                        }
                    )
                )
        return tuple(records)

    def save_structured_event(self, event: StructuredEvent) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events.structured_event (
                        event_id, instrument_ids, event_type, event_subtype,
                        event_ts, detected_at, source_refs, relevance_score,
                        materiality_score, novelty_score, surprise_score,
                        sentiment_score, confidence_score, evidence,
                        reason_codes, model_version, payload
                    ) VALUES (
                        %(event_id)s, %(instrument_ids)s, %(event_type)s,
                        %(event_subtype)s, %(event_ts)s, %(detected_at)s,
                        %(source_refs)s, %(relevance_score)s,
                        %(materiality_score)s, %(novelty_score)s,
                        %(surprise_score)s, %(sentiment_score)s,
                        %(confidence_score)s, %(evidence)s,
                        %(reason_codes)s, %(model_version)s, %(payload)s
                    )
                    ON CONFLICT (event_id) DO UPDATE SET
                        instrument_ids = EXCLUDED.instrument_ids,
                        event_type = EXCLUDED.event_type,
                        event_subtype = EXCLUDED.event_subtype,
                        event_ts = EXCLUDED.event_ts,
                        detected_at = EXCLUDED.detected_at,
                        source_refs = EXCLUDED.source_refs,
                        relevance_score = EXCLUDED.relevance_score,
                        materiality_score = EXCLUDED.materiality_score,
                        novelty_score = EXCLUDED.novelty_score,
                        surprise_score = EXCLUDED.surprise_score,
                        sentiment_score = EXCLUDED.sentiment_score,
                        confidence_score = EXCLUDED.confidence_score,
                        evidence = EXCLUDED.evidence,
                        reason_codes = EXCLUDED.reason_codes,
                        model_version = EXCLUDED.model_version,
                        payload = EXCLUDED.payload
                    """,
                    {
                        **event.to_dict(),
                        "event_ts": parse_utc_iso(event.event_ts),
                        "detected_at": parse_utc_iso(event.detected_at),
                        "payload": Jsonb(dict(event.payload)),
                    },
                )
        return f"events.structured_event:{event.event_id}"

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

    def save_earnings_dividend_record(self, record: EarningsDividendRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events.earnings_dividend_record (
                        earnings_dividend_record_id, instrument_id, event_id,
                        record_type, as_of_ts, payload, source_module,
                        calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (earnings_dividend_record_id) DO UPDATE SET
                        event_id = EXCLUDED.event_id,
                        record_type = EXCLUDED.record_type,
                        as_of_ts = EXCLUDED.as_of_ts,
                        payload = EXCLUDED.payload,
                        source_module = EXCLUDED.source_module,
                        calculation_version = EXCLUDED.calculation_version
                    """,
                    (
                        uuid.UUID(record.earnings_dividend_record_id),
                        record.instrument_id,
                        record.event_id,
                        record.record_type,
                        parse_utc_iso(record.as_of_ts),
                        Jsonb(dict(record.payload)),
                        record.source_module,
                        record.calculation_version,
                    ),
                )
        return f"events.earnings_dividend_record:{record.earnings_dividend_record_id}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def stable_uuid_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))


def _raw_text_from_row(row: tuple[Any, ...]) -> RawTextItem:
    return RawTextItem(
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


def _event_from_row(row: tuple[Any, ...]) -> StructuredEvent:
    return StructuredEvent(
        event_id=str(row[0]),
        instrument_ids=tuple(row[1] or ()),
        event_type=row[2] or "",
        event_subtype=row[3] or "",
        event_ts=_iso(row[4]),
        detected_at=_iso(row[5]) if row[5] else _iso(row[4]),
        source_refs=tuple(row[6] or ()),
        relevance_score=float(row[7] or 0.0),
        materiality_score=float(row[8] or 0.0),
        novelty_score=float(row[9] or 0.0),
        surprise_score=float(row[10] or 0.0),
        sentiment_score=float(row[11] or 0.0),
        confidence_score=float(row[12] or 1.0),
        evidence=tuple(row[13] or ()),
        reason_codes=tuple(row[14] or ()),
        model_version=row[15] or "structured_event",
        payload=row[16] or {},
    )


def _candle_from_row(row: tuple[Any, ...]) -> RawCandle:
    payload = row[8] or {}
    return RawCandle.from_mapping(
        {
            "raw_candle_id": str(row[0]),
            "instrument_id": row[1] or "",
            "universe_id": row[2],
            "timeframe": row[3] or "",
            "open_ts": _iso(row[4]),
            "close_ts": _iso(row[5]) if row[5] else None,
            "open_price": row[6],
            "close_price": row[7],
            "source_payload": payload,
            "received_at": _iso(row[9]) if row[9] else None,
        }
    )


def _expectation_from_raw_text(item: RawTextItem) -> FinancialExpectation:
    payload = dict(item.source_payload)
    payload.setdefault("instrument_id", item.instrument_ids[0] if item.instrument_ids else "")
    payload.setdefault("as_of_ts", item.published_at or item.fetched_at)
    payload.setdefault("source_refs", [f"raw_text.raw_text_item:{item.raw_text_item_id}"])
    payload.setdefault("id", item.raw_text_item_id)
    return FinancialExpectation.from_mapping(payload)


def _expectation_from_event(event: StructuredEvent) -> FinancialExpectation:
    payload = dict(event.payload)
    payload.setdefault("instrument_id", event.instrument_ids[0] if event.instrument_ids else "")
    payload.setdefault("as_of_ts", event.event_ts)
    payload.setdefault("source_refs", [f"events.structured_event:{event.event_id}", *event.source_refs])
    payload.setdefault("id", event.event_id)
    return FinancialExpectation.from_mapping(payload)


def _has_expectation_payload(payload: Mapping[str, Any]) -> bool:
    if any(key in payload for key in ("expectations", "financial_expectations", "expected")):
        return True
    return any(str(key).endswith("_expected") or str(key).startswith("expected_") for key in payload)


def _nested_fields(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    fields: dict[str, Any] = {}
    for key in keys:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            fields.update(nested)
    fields.update(payload)
    return fields


def _timestamp_in_range(timestamp: str, from_ts: str, to_ts: str) -> bool:
    parsed = parse_utc_iso(timestamp)
    return parse_utc_iso(from_ts) <= parsed <= parse_utc_iso(to_ts)


def _timestamp_in_range_or_missing(timestamp: str | None, from_ts: str, to_ts: str) -> bool:
    if not timestamp:
        return True
    return _timestamp_in_range(timestamp, from_ts, to_ts)


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
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(primary: Mapping[str, Any], secondary: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(primary.get(key))
        if value is not None:
            return value
        value = _optional_float(secondary.get(key))
        if value is not None:
            return value
    return None


def _float_tuple(value: Any) -> tuple[float, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, Mapping):
        value = value.values()
    if isinstance(value, (str, bytes)):
        parsed = _optional_float(value)
        return () if parsed is None else (parsed,)
    try:
        iterator = iter(value)
    except TypeError:
        parsed = _optional_float(value)
        return () if parsed is None else (parsed,)
    parsed_values = tuple(_optional_float(item) for item in iterator)
    return tuple(item for item in parsed_values if item is not None)


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
