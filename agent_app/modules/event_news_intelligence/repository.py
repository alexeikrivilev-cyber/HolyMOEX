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
    source_type: str | None = None
    source_url: str | None = None
    title: str | None = None
    body: str | None = None
    language: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    content_hash: str | None = None
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    discovery_run_id: str | None = None
    discovery_item_id: str | None = None
    discovery_mode: str | None = None
    external_request_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawTextItem":
        source_payload = payload.get("source_payload") or payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        source = str(payload.get("source") or payload.get("provider") or source_payload.get("source") or "")
        return cls(
            raw_text_item_id=str(payload.get("raw_text_item_id") or payload.get("id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or source_payload.get("instrument_ids") or ())),
            source=source,
            source_type=_optional_text(payload.get("source_type") or source_payload.get("source_type") or source),
            source_url=_optional_text(payload.get("source_url")),
            title=_optional_text(payload.get("title")),
            body=_optional_text(payload.get("body")),
            language=_optional_text(payload.get("language")),
            published_at=_optional_text(payload.get("published_at")),
            fetched_at=_optional_text(payload.get("fetched_at")),
            content_hash=_optional_text(payload.get("content_hash")),
            source_payload=source_payload,
            discovery_run_id=_optional_text(payload.get("discovery_run_id")),
            discovery_item_id=_optional_text(payload.get("discovery_item_id")),
            discovery_mode=_optional_text(payload.get("discovery_mode")),
            external_request_id=_optional_text(payload.get("external_request_id")),
        )


@dataclass(frozen=True)
class EventRoutingMessage:
    routing_message_id: str
    raw_text_item_id: str | None
    target_module: str
    universe_id: str | None
    instrument_ids: tuple[str, ...]
    routing_reason: str | None = None
    routing_ttl_seconds: int | None = None
    status: str = "pending"
    created_at: str | None = None
    discovery_mode: str | None = None
    discovery_run_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "EventRoutingMessage":
        raw_ref = payload.get("raw_text_ref") or payload.get("raw_text_item_ref")
        raw_text_item_id = payload.get("raw_text_item_id") or (_ref_tail(str(raw_ref)) if raw_ref else None)
        return cls(
            routing_message_id=str(payload.get("routing_message_id") or payload.get("id") or ""),
            raw_text_item_id=_optional_text(raw_text_item_id),
            target_module=str(payload.get("target_module") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            instrument_ids=tuple(str(item) for item in (payload.get("instrument_ids") or ())),
            routing_reason=_optional_text(payload.get("routing_reason")),
            routing_ttl_seconds=_optional_int(payload.get("routing_ttl_seconds")),
            status=str(payload.get("status") or "pending"),
            created_at=_optional_text(payload.get("created_at")),
            discovery_mode=_optional_text(payload.get("discovery_mode")),
            discovery_run_id=_optional_text(payload.get("discovery_run_id")),
        )


@dataclass(frozen=True)
class InstrumentProfile:
    instrument_id: str
    universe_id: str
    ticker: str
    board_id: str | None = None
    sector: str | None = None
    issuer_name: str | None = None
    aliases: tuple[str, ...] = ()
    related_entities: tuple[str, ...] = ()
    is_active: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentProfile":
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            ticker=str(payload.get("ticker") or payload.get("secid") or ""),
            board_id=_optional_text(payload.get("board_id")),
            sector=_optional_text(payload.get("sector")),
            issuer_name=_optional_text(payload.get("issuer_name")),
            aliases=tuple(str(item) for item in (payload.get("aliases") or ())),
            related_entities=tuple(str(item) for item in (payload.get("related_entities") or ())),
            is_active=bool(payload.get("is_active", True)),
            metadata=metadata,
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
class EventReaction:
    event_id: str
    instrument_id: str
    horizon: str
    reaction_return: float | None
    market_adjusted_return: float | None
    abnormal_volume: float | None
    calculated_at: str
    calculation_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "instrument_id": self.instrument_id,
            "horizon": self.horizon,
            "reaction_return": self.reaction_return,
            "market_adjusted_return": self.market_adjusted_return,
            "abnormal_volume": self.abnormal_volume,
            "calculated_at": self.calculated_at,
            "calculation_version": self.calculation_version,
            "payload": dict(self.payload),
        }


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


class EventNewsIntelligenceRepository(Protocol):
    def list_raw_text_items(
        self,
        raw_text_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawTextItem, ...]:
        ...

    def list_routing_messages(
        self,
        routing_message_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[EventRoutingMessage, ...]:
        ...

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        ...

    def save_structured_event(self, event: StructuredEvent) -> str:
        ...

    def save_event_reaction(self, reaction: EventReaction) -> str:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryEventNewsIntelligenceRepository:
    def __init__(
        self,
        raw_text_items: tuple[Mapping[str, Any] | RawTextItem, ...] = (),
        routing_messages: tuple[Mapping[str, Any] | EventRoutingMessage, ...] = (),
        instrument_profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
        profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
    ) -> None:
        self.raw_text_items = tuple(
            item if isinstance(item, RawTextItem) else RawTextItem.from_mapping(item)
            for item in raw_text_items
        )
        self.routing_messages = tuple(
            item if isinstance(item, EventRoutingMessage) else EventRoutingMessage.from_mapping(item)
            for item in routing_messages
        )
        self.instrument_profiles = tuple(
            item if isinstance(item, InstrumentProfile) else InstrumentProfile.from_mapping(item)
            for item in (*instrument_profiles, *profiles)
        )
        self.structured_events: list[StructuredEvent] = []
        self.event_reactions: list[EventReaction] = []
        self.feature_records: list[FeatureRecord] = []
        self.audit_records: list[AuditRecord] = []

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

    def list_routing_messages(
        self,
        routing_message_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[EventRoutingMessage, ...]:
        requested_refs = {_ref_tail(ref) for ref in routing_message_refs}
        requested_instruments = set(instrument_ids)
        messages = []
        for message in self.routing_messages:
            if requested_refs and message.routing_message_id not in requested_refs:
                continue
            if not requested_refs:
                if message.target_module and message.target_module != "Event & News Intelligence Module":
                    continue
                if message.universe_id and message.universe_id != universe_id:
                    continue
                if requested_instruments and message.instrument_ids and not (requested_instruments & set(message.instrument_ids)):
                    continue
                if message.created_at and not _timestamp_in_range(message.created_at, from_ts, to_ts):
                    continue
            messages.append(message)
        return tuple(sorted(messages, key=lambda item: item.created_at or item.routing_message_id))

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        requested = set(instrument_ids)
        return tuple(
            sorted(
                (
                    profile
                    for profile in self.instrument_profiles
                    if profile.universe_id == universe_id
                    and profile.is_active
                    and (not requested or profile.instrument_id in requested)
                ),
                key=lambda item: item.instrument_id,
            )
        )

    def save_structured_event(self, event: StructuredEvent) -> str:
        self.structured_events = [item for item in self.structured_events if item.event_id != event.event_id]
        self.structured_events.append(event)
        return f"events.structured_event:{event.event_id}"

    def save_event_reaction(self, reaction: EventReaction) -> str:
        self.event_reactions.append(reaction)
        return f"events.event_reaction:{stable_record_id('event_reaction', reaction.to_dict())}"

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records = [item for item in self.feature_records if item.feature_id != record.feature_id]
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit.audit_record:memory:{len(self.audit_records)}"


class PostgresEventNewsIntelligenceRepository:
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
                               COALESCE(source_type, source) AS source_type, source_url,
                               title, body, language, published_at, fetched_at,
                               content_hash, source_payload, discovery_run_id,
                               discovery_item_id, discovery_mode, external_request_id
                          FROM raw_text.raw_text_item
                         WHERE raw_text_item_id::text = ANY(%s)
                         ORDER BY COALESCE(published_at, fetched_at)
                        """,
                        (ref_ids,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT raw_text_item_id, universe_id, instrument_ids, source,
                               COALESCE(source_type, source) AS source_type, source_url,
                               title, body, language, published_at, fetched_at,
                               content_hash, source_payload, discovery_run_id,
                               discovery_item_id, discovery_mode, external_request_id
                          FROM raw_text.raw_text_item
                         WHERE universe_id = %s
                           AND (cardinality(instrument_ids) = 0 OR instrument_ids && %s)
                           AND COALESCE(published_at, fetched_at) BETWEEN %s AND %s
                         ORDER BY COALESCE(published_at, fetched_at)
                        """,
                        (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                    )
                rows = cur.fetchall()
        return tuple(_raw_text_item_from_row(row) for row in rows)

    def list_routing_messages(
        self,
        routing_message_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[EventRoutingMessage, ...]:
        ref_ids = [_ref_tail(ref) for ref in routing_message_refs]
        with self._connect() as conn:
            with conn.cursor() as cur:
                if ref_ids:
                    cur.execute(
                        """
                        SELECT routing_message_id, raw_text_item_id, target_module,
                               universe_id, instrument_ids, routing_reason,
                               routing_ttl_seconds, status, created_at,
                               discovery_mode, discovery_run_id
                          FROM raw_text.event_routing_message
                         WHERE routing_message_id::text = ANY(%s)
                         ORDER BY created_at
                        """,
                        (ref_ids,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT routing_message_id, raw_text_item_id, target_module,
                               universe_id, instrument_ids, routing_reason,
                               routing_ttl_seconds, status, created_at,
                               discovery_mode, discovery_run_id
                          FROM raw_text.event_routing_message
                         WHERE target_module = 'Event & News Intelligence Module'
                           AND universe_id = %s
                           AND (cardinality(instrument_ids) = 0 OR instrument_ids && %s)
                           AND created_at BETWEEN %s AND %s
                         ORDER BY created_at
                        """,
                        (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                    )
                rows = cur.fetchall()
        return tuple(_routing_message_from_row(row) for row in rows)

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        if not instrument_ids:
            return ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_id, universe_id, ticker, board_id, sector, issuer_name,
                           aliases, related_entities, is_active, metadata
                      FROM registry.instrument_profile
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND is_active = true
                     ORDER BY instrument_id
                    """,
                    (universe_id, list(instrument_ids)),
                )
                rows = cur.fetchall()
        return tuple(
            InstrumentProfile(
                instrument_id=row[0],
                universe_id=row[1],
                ticker=row[2] or "",
                board_id=row[3],
                sector=row[4],
                issuer_name=row[5],
                aliases=tuple(row[6] or ()),
                related_entities=tuple(row[7] or ()),
                is_active=bool(row[8]),
                metadata=row[9] or {},
            )
            for row in rows
        )

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

    def save_event_reaction(self, reaction: EventReaction) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events.event_reaction (
                        event_id, instrument_id, horizon, reaction_return,
                        market_adjusted_return, abnormal_volume,
                        calculated_at, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING event_reaction_id
                    """,
                    (
                        reaction.event_id,
                        reaction.instrument_id,
                        reaction.horizon,
                        reaction.reaction_return,
                        reaction.market_adjusted_return,
                        reaction.abnormal_volume,
                        parse_utc_iso(reaction.calculated_at),
                        reaction.calculation_version,
                    ),
                )
                row = cur.fetchone()
        return f"events.event_reaction:{row[0]}"

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


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def stable_uuid_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))


def _raw_text_item_from_row(row: tuple[Any, ...]) -> RawTextItem:
    return RawTextItem(
        raw_text_item_id=str(row[0]),
        universe_id=row[1],
        instrument_ids=tuple(row[2] or ()),
        source=row[3] or "",
        source_type=row[4],
        source_url=row[5],
        title=row[6],
        body=row[7],
        language=row[8],
        published_at=_iso(row[9]) if row[9] else None,
        fetched_at=_iso(row[10]) if row[10] else None,
        content_hash=row[11],
        source_payload=row[12] or {},
        discovery_run_id=row[13],
        discovery_item_id=str(row[14]) if row[14] else None,
        discovery_mode=row[15],
        external_request_id=row[16],
    )


def _routing_message_from_row(row: tuple[Any, ...]) -> EventRoutingMessage:
    return EventRoutingMessage(
        routing_message_id=str(row[0]),
        raw_text_item_id=str(row[1]) if row[1] else None,
        target_module=row[2] or "",
        universe_id=row[3],
        instrument_ids=tuple(row[4] or ()),
        routing_reason=row[5],
        routing_ttl_seconds=_optional_int(row[6]),
        status=row[7] or "pending",
        created_at=_iso(row[8]) if row[8] else None,
        discovery_mode=row[9],
        discovery_run_id=row[10],
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


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
