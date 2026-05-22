from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


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
        event_payload = payload.get("payload") or payload.get("source_payload") or {}
        if not isinstance(event_payload, Mapping):
            event_payload = {}
        event_ts = str(payload.get("event_ts") or event_payload.get("event_ts") or "")
        return cls(
            event_id=str(payload.get("event_id") or payload.get("id") or event_payload.get("event_id") or ""),
            instrument_ids=_string_tuple(payload.get("instrument_ids") or event_payload.get("instrument_ids")),
            event_type=str(payload.get("event_type") or event_payload.get("event_type") or ""),
            event_subtype=str(payload.get("event_subtype") or event_payload.get("event_subtype") or ""),
            event_ts=event_ts,
            detected_at=str(payload.get("detected_at") or event_payload.get("detected_at") or event_ts),
            source_refs=_string_tuple(payload.get("source_refs") or event_payload.get("source_refs")),
            relevance_score=float(payload.get("relevance_score") or event_payload.get("relevance_score") or 0.0),
            materiality_score=float(payload.get("materiality_score") or event_payload.get("materiality_score") or 0.0),
            novelty_score=float(payload.get("novelty_score") or event_payload.get("novelty_score") or 0.0),
            surprise_score=float(payload.get("surprise_score") or event_payload.get("surprise_score") or 0.0),
            sentiment_score=float(payload.get("sentiment_score") or event_payload.get("sentiment_score") or 0.0),
            confidence_score=float(payload.get("confidence_score") or event_payload.get("confidence_score") or 1.0),
            evidence=_string_tuple(payload.get("evidence") or event_payload.get("evidence")),
            reason_codes=_string_tuple(payload.get("reason_codes") or event_payload.get("reason_codes")),
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
class InstrumentProfile:
    instrument_id: str
    universe_id: str
    ticker: str
    figi: str | None = None
    isin: str | None = None
    class_code: str | None = None
    board_id: str | None = None
    sector: str | None = None
    issuer_name: str | None = None
    aliases: tuple[str, ...] = ()
    is_active: bool = True
    tradable: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentProfile":
        metadata = payload.get("metadata") or payload.get("payload") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            ticker=str(payload.get("ticker") or payload.get("secid") or ""),
            figi=_optional_text(payload.get("figi")),
            isin=_optional_text(payload.get("isin")),
            class_code=_optional_text(payload.get("class_code")),
            board_id=_optional_text(payload.get("board_id")),
            sector=_optional_text(payload.get("sector")),
            issuer_name=_optional_text(payload.get("issuer_name")),
            aliases=_string_tuple(payload.get("aliases")),
            is_active=bool(payload.get("is_active", True)),
            tradable=bool(payload.get("tradable", True)),
            metadata=metadata,
        )

    def float_field(self, *keys: str) -> float | None:
        for key in keys:
            value = _optional_float(self.metadata.get(key))
            if value is not None:
                return value
        return None


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
        source_payload = payload.get("source_payload") or payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            raw_candle_id=str(payload.get("raw_candle_id") or payload.get("id") or source_payload.get("raw_candle_id") or ""),
            instrument_id=str(payload.get("instrument_id") or source_payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or source_payload.get("universe_id") or ""),
            timeframe=str(payload.get("timeframe") or source_payload.get("timeframe") or ""),
            open_ts=str(payload.get("open_ts") or source_payload.get("open_ts") or ""),
            close_ts=_optional_text(payload.get("close_ts") or source_payload.get("close_ts")),
            open_price=_first_float(payload, source_payload, "open_price", "open"),
            high_price=_first_float(payload, source_payload, "high_price", "high"),
            low_price=_first_float(payload, source_payload, "low_price", "low"),
            close_price=_first_float(payload, source_payload, "close_price", "close", "price"),
            volume=_first_float(payload, source_payload, "volume"),
            turnover=_first_float(payload, source_payload, "turnover"),
            provider=str(payload.get("provider") or source_payload.get("provider") or ""),
            source_payload=source_payload,
            received_at=_optional_text(payload.get("received_at") or source_payload.get("received_at")),
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

    def with_adjustment(
        self,
        *,
        adjustment_factor: float,
        corporate_action_id: str,
        effective_date: str,
        calculation_version: str,
        adjustment_policy: str,
    ) -> "RawCandle":
        def adjusted(value: float | None) -> float | None:
            return None if value is None else float(value) * float(adjustment_factor)

        payload = {
            **dict(self.source_payload),
            "corporate_action_adjusted": True,
            "corporate_action_id": corporate_action_id,
            "corporate_action_effective_date": effective_date,
            "adjustment_factor": adjustment_factor,
            "adjustment_policy": adjustment_policy,
            "calculation_version": calculation_version,
            "raw_open_price": self.open_price,
            "raw_high_price": self.high_price,
            "raw_low_price": self.low_price,
            "raw_close_price": self.close_price,
            "corporate_action_adjusted_open": adjusted(self.open_price),
            "corporate_action_adjusted_high": adjusted(self.high_price),
            "corporate_action_adjusted_low": adjusted(self.low_price),
            "corporate_action_adjusted_close": adjusted(self.close_price),
        }
        return RawCandle(
            raw_candle_id=self.raw_candle_id,
            instrument_id=self.instrument_id,
            universe_id=self.universe_id,
            timeframe=self.timeframe,
            open_ts=self.open_ts,
            close_ts=self.close_ts,
            open_price=self.open_price,
            high_price=self.high_price,
            low_price=self.low_price,
            close_price=self.close_price,
            volume=self.volume,
            turnover=self.turnover,
            provider=self.provider,
            source_payload=payload,
            received_at=self.received_at,
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
class CorporateActionRecord:
    corporate_action_id: str
    instrument_id: str
    action_type: str
    effective_date: str
    adjustment_factor: float | None
    source_refs: tuple[str, ...]
    confidence_score: float
    requires_recompute: bool
    event_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    source_module: str = "Corporate Actions Adjustment Module"
    calculation_version: str = "corporate_actions_adjustment_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "corporate_action_id": self.corporate_action_id,
            "instrument_id": self.instrument_id,
            "action_type": self.action_type,
            "effective_date": self.effective_date,
            "adjustment_factor": self.adjustment_factor,
            "source_refs": list(self.source_refs),
            "confidence_score": self.confidence_score,
            "requires_recompute": self.requires_recompute,
            "event_id": self.event_id,
            "payload": dict(self.payload),
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
        }


@dataclass(frozen=True)
class InstrumentMappingUpdate:
    mapping_update_id: str
    instrument_id: str
    provider: str
    provider_symbol: str
    previous_provider_symbol: str | None
    effective_date: str
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_update_id": self.mapping_update_id,
            "instrument_id": self.instrument_id,
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "previous_provider_symbol": self.previous_provider_symbol,
            "effective_date": self.effective_date,
            "source_refs": list(self.source_refs),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class InstrumentStatusUpdate:
    status_update_id: str
    instrument_id: str
    old_tradable: bool | None
    new_tradable: bool
    effective_date: str
    source_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_update_id": self.status_update_id,
            "instrument_id": self.instrument_id,
            "old_tradable": self.old_tradable,
            "new_tradable": self.new_tradable,
            "effective_date": self.effective_date,
            "source_refs": list(self.source_refs),
            "reason_codes": list(self.reason_codes),
            "payload": dict(self.payload),
        }


class CorporateActionsAdjustmentRepository(Protocol):
    def list_structured_events(
        self,
        event_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[StructuredEvent, ...]:
        ...

    def list_instrument_profiles(
        self,
        profile_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        ...

    def list_candles(
        self,
        price_series_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        ...

    def save_corporate_action_record(self, record: CorporateActionRecord) -> str:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_adjusted_candles(self, candles: tuple[RawCandle, ...], adjusted_price_series_ref: str) -> str:
        ...

    def save_instrument_mapping_update(self, update: InstrumentMappingUpdate) -> str:
        ...

    def save_instrument_status_update(self, update: InstrumentStatusUpdate) -> str:
        ...


class InMemoryCorporateActionsAdjustmentRepository:
    def __init__(
        self,
        structured_events: tuple[Mapping[str, Any] | StructuredEvent, ...] = (),
        events: tuple[Mapping[str, Any] | StructuredEvent, ...] = (),
        profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
        candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
    ) -> None:
        self.structured_events = tuple(
            event if isinstance(event, StructuredEvent) else StructuredEvent.from_mapping(event)
            for event in (*structured_events, *events)
        )
        self.profiles = tuple(
            profile if isinstance(profile, InstrumentProfile) else InstrumentProfile.from_mapping(profile)
            for profile in profiles
        )
        self.candles = tuple(
            candle if isinstance(candle, RawCandle) else RawCandle.from_mapping(candle)
            for candle in candles
        )
        self.corporate_action_records: list[CorporateActionRecord] = []
        self.feature_records: list[FeatureRecord] = []
        self.adjusted_candle_sets: dict[str, tuple[RawCandle, ...]] = {}
        self.instrument_mapping_updates: list[InstrumentMappingUpdate] = []
        self.instrument_status_updates: list[InstrumentStatusUpdate] = []

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
                if event.event_type not in {"corporate_action", "dividend"}:
                    continue
                if requested_instruments and event.instrument_ids and not (requested_instruments & set(event.instrument_ids)):
                    continue
                if event.event_ts and not _timestamp_in_range(event.event_ts, from_ts, to_ts):
                    continue
            events.append(event)
        return tuple(sorted(events, key=lambda item: (item.event_ts, item.event_id)))

    def list_instrument_profiles(
        self,
        profile_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        del profile_ref
        requested = set(instrument_ids)
        profiles = [
            profile
            for profile in self.profiles
            if profile.universe_id == universe_id and (not requested or profile.instrument_id in requested)
        ]
        return tuple(sorted(profiles, key=lambda profile: profile.instrument_id))

    def list_candles(
        self,
        price_series_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        del price_series_ref
        requested = set(instrument_ids)
        candles = [
            candle
            for candle in self.candles
            if candle.universe_id == universe_id
            and candle.instrument_id in requested
            and (candle.close_ts or candle.open_ts)
            and _timestamp_in_range(candle.close_ts or candle.open_ts, from_ts, to_ts)
        ]
        return tuple(sorted(candles, key=lambda candle: (candle.instrument_id, candle.timeframe, candle.close_ts or candle.open_ts)))

    def save_corporate_action_record(self, record: CorporateActionRecord) -> str:
        self.corporate_action_records.append(record)
        return f"events.corporate_action_record:{record.corporate_action_id}"

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_adjusted_candles(self, candles: tuple[RawCandle, ...], adjusted_price_series_ref: str) -> str:
        self.adjusted_candle_sets[adjusted_price_series_ref] = candles
        return adjusted_price_series_ref

    def save_instrument_mapping_update(self, update: InstrumentMappingUpdate) -> str:
        self.instrument_mapping_updates.append(update)
        return f"registry.instrument_mapping:{update.mapping_update_id}"

    def save_instrument_status_update(self, update: InstrumentStatusUpdate) -> str:
        self.instrument_status_updates.append(update)
        return f"registry.instrument_profile:{update.status_update_id}"


class PostgresCorporateActionsAdjustmentRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

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
                         ORDER BY event_ts, event_id
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
                         WHERE event_type IN ('corporate_action', 'dividend')
                           AND event_ts BETWEEN %s AND %s
                           AND (cardinality(instrument_ids) = 0 OR instrument_ids && %s)
                         ORDER BY event_ts, event_id
                        """,
                        (parse_utc_iso(from_ts), parse_utc_iso(to_ts), list(instrument_ids)),
                    )
                rows = cur.fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def list_instrument_profiles(
        self,
        profile_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        del profile_ref
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_id, universe_id, ticker, figi, isin, class_code,
                           board_id, sector, issuer_name, aliases, is_active,
                           tradable, metadata
                      FROM registry.instrument_profile
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                     ORDER BY instrument_id
                    """,
                    (universe_id, list(instrument_ids)),
                )
                rows = cur.fetchall()
        return tuple(
            InstrumentProfile(
                instrument_id=row[0] or "",
                universe_id=row[1] or "",
                ticker=row[2] or "",
                figi=row[3],
                isin=row[4],
                class_code=row[5],
                board_id=row[6],
                sector=row[7],
                issuer_name=row[8],
                aliases=tuple(row[9] or ()),
                is_active=bool(row[10]),
                tradable=bool(row[11]),
                metadata=row[12] or {},
            )
            for row in rows
        )

    def list_candles(
        self,
        price_series_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        del price_series_ref
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
                       AND COALESCE(close_ts, open_ts) BETWEEN %s AND %s
                     ORDER BY instrument_id, timeframe, COALESCE(close_ts, open_ts)
                    """,
                    (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_candle_from_row(row) for row in rows)

    def save_corporate_action_record(self, record: CorporateActionRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events.corporate_action_record (
                        corporate_action_record_id, instrument_id, event_id,
                        action_type, effective_date, adjustment_factor, payload,
                        source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (corporate_action_record_id) DO UPDATE SET
                        instrument_id = EXCLUDED.instrument_id,
                        event_id = EXCLUDED.event_id,
                        action_type = EXCLUDED.action_type,
                        effective_date = EXCLUDED.effective_date,
                        adjustment_factor = EXCLUDED.adjustment_factor,
                        payload = EXCLUDED.payload,
                        source_module = EXCLUDED.source_module,
                        calculation_version = EXCLUDED.calculation_version
                    """,
                    (
                        uuid.UUID(record.corporate_action_id),
                        record.instrument_id,
                        record.event_id,
                        record.action_type,
                        date.fromisoformat(record.effective_date),
                        record.adjustment_factor,
                        Jsonb(
                            {
                                **dict(record.payload),
                                "source_refs": list(record.source_refs),
                                "confidence_score": record.confidence_score,
                                "requires_recompute": record.requires_recompute,
                            }
                        ),
                        record.source_module,
                        record.calculation_version,
                    ),
                )
        return f"events.corporate_action_record:{record.corporate_action_id}"

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

    def save_adjusted_candles(self, candles: tuple[RawCandle, ...], adjusted_price_series_ref: str) -> str:
        if not candles:
            return adjusted_price_series_ref

        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                for candle in candles:
                    cur.execute(
                        """
                        UPDATE raw_market.raw_candle
                           SET source_payload = source_payload || %s
                         WHERE raw_candle_id = %s
                        """,
                        (Jsonb(dict(candle.source_payload)), uuid.UUID(candle.raw_candle_id)),
                    )
        return adjusted_price_series_ref

    def save_instrument_mapping_update(self, update: InstrumentMappingUpdate) -> str:
        from psycopg.types.json import Jsonb

        payload = {
            **dict(update.payload),
            "effective_date": update.effective_date,
            "source_refs": list(update.source_refs),
            "mapping_version": update.mapping_update_id,
            "previous_provider_symbol": update.previous_provider_symbol,
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registry.instrument_mapping (
                        instrument_id, provider, provider_symbol, provider_payload
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (instrument_id, provider) DO UPDATE SET
                        provider_symbol = EXCLUDED.provider_symbol,
                        provider_payload = registry.instrument_mapping.provider_payload || EXCLUDED.provider_payload
                    """,
                    (update.instrument_id, update.provider, update.provider_symbol, Jsonb(payload)),
                )
        return f"registry.instrument_mapping:{update.mapping_update_id}"

    def save_instrument_status_update(self, update: InstrumentStatusUpdate) -> str:
        from psycopg.types.json import Jsonb

        audit_payload = {
            "corporate_action_status_audit": update.to_dict(),
            **dict(update.payload),
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE registry.instrument_profile
                       SET tradable = %s,
                           metadata = metadata || %s,
                           updated_at = now()
                     WHERE instrument_id = %s
                    """,
                    (update.new_tradable, Jsonb(audit_payload), update.instrument_id),
                )
        return f"registry.instrument_profile:{update.status_update_id}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def stable_uuid_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))


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
    return RawCandle(
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


def _timestamp_in_range(timestamp: str, from_ts: str, to_ts: str) -> bool:
    parsed = parse_utc_iso(timestamp)
    return parse_utc_iso(from_ts) <= parsed <= parse_utc_iso(to_ts)


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
