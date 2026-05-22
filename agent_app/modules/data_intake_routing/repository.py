from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects import ExternalResponse
from agent_app.contracts.unified_objects.module_job import parse_utc_iso


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


DEFAULT_TEXT_SOURCE_CONFIGS: tuple[Mapping[str, Any], ...] = (
    {
        "text_source_config_id": "source:news_api:text_search",
        "source_type": "news_api",
        "provider": "news_api",
        "request_type": "text_search",
        "enabled": True,
        "discovery_frequency": "15m-60m",
        "max_age_seconds": 3600,
        "query_template": {
            "query_fields": ["ticker", "issuer_name", "aliases", "related_entities"],
            "response_target": "Raw Text Store",
        },
        "source_policy": {"requires_active_instrument": True, "gateway_only": True},
    },
    {
        "text_source_config_id": "source:issuer_disclosure:text_search",
        "source_type": "issuer_disclosure",
        "provider": "issuer_disclosure",
        "request_type": "text_search",
        "enabled": True,
        "discovery_frequency": "15m-60m",
        "max_age_seconds": 3600,
        "query_template": {
            "query_fields": ["ticker", "issuer_name", "aliases"],
            "response_target": "Raw Text Store",
        },
        "source_policy": {"requires_active_instrument": True, "gateway_only": True},
    },
    {
        "text_source_config_id": "source:regulatory_text:text_search",
        "source_type": "regulatory_text",
        "provider": "issuer_disclosure",
        "request_type": "text_search",
        "enabled": True,
        "discovery_frequency": "15m-60m",
        "max_age_seconds": 3600,
        "query_template": {
            "query_fields": ["ticker", "issuer_name", "related_entities"],
            "response_target": "Raw Text Store",
        },
        "source_policy": {"requires_active_instrument": True, "gateway_only": True},
    },
    {
        "text_source_config_id": "source:macro_text:text_search",
        "source_type": "macro_text",
        "provider": "macro_api",
        "request_type": "text_search",
        "enabled": True,
        "discovery_frequency": "4h-8h",
        "max_age_seconds": 14400,
        "query_template": {
            "query_fields": ["sector", "related_entities"],
            "response_target": "Raw Text Store",
        },
        "source_policy": {
            "requires_active_instrument": False,
            "gateway_only": True,
            "market_wide_allowed": True,
        },
    },
    {
        "text_source_config_id": "source:corporate_site:text_search",
        "source_type": "corporate_site",
        "provider": "issuer_disclosure",
        "request_type": "text_search",
        "enabled": True,
        "discovery_frequency": "15m-60m",
        "max_age_seconds": 3600,
        "query_template": {
            "query_fields": ["issuer_name", "aliases", "related_entities"],
            "response_target": "Raw Text Store",
        },
        "source_policy": {"requires_active_instrument": True, "gateway_only": True},
    },
)


class DataIntakeRoutingRepository(Protocol):
    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[Any, ...]:
        ...

    def list_text_source_configs(self, source_types: tuple[str, ...]) -> tuple[Any, ...]:
        ...

    def save_discovery_run(self, run: Any) -> str:
        ...

    def save_discovery_item(self, item: Any) -> str:
        ...

    def save_external_text_search_request(self, record: Any) -> str:
        ...

    def find_raw_text_item_by_hash(self, content_hash: str) -> Any | None:
        ...

    def list_recent_raw_text_items(self, universe_id: str, limit: int = 500) -> tuple[Any, ...]:
        ...

    def save_raw_text_item(self, item: Any) -> str:
        ...

    def load_raw_text_item(self, ref: str) -> Any | None:
        ...

    def save_routing_message(self, message: Any) -> str:
        ...

    def save_text_dedup_record(self, record: Any) -> str:
        ...

    def save_source_credibility_record(self, record: Any) -> str:
        ...

    def load_external_response(self, ref: str) -> ExternalResponse | None:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryDataIntakeRoutingRepository:
    def __init__(
        self,
        profiles: tuple[Any, ...] = (),
        external_responses: Mapping[str, ExternalResponse] | None = None,
        text_source_configs: tuple[Any, ...] | None = None,
    ) -> None:
        self.profiles: dict[str, Any] = {
            str(_value(profile, "instrument_id")): profile for profile in profiles
        }
        source_configs = text_source_configs or DEFAULT_TEXT_SOURCE_CONFIGS
        self.text_source_configs: dict[str, Any] = {
            str(_value(config, "source_type")): config for config in source_configs
        }
        self.discovery_runs: dict[str, Any] = {}
        self.discovery_items: dict[str, Any] = {}
        self.external_text_search_requests: dict[str, Any] = {}
        self.raw_text_items_by_id: dict[str, Any] = {}
        self.raw_text_items_by_hash: dict[str, Any] = {}
        self.raw_text_refs: dict[str, Any] = {}
        self.routing_messages: list[Any] = []
        self.routing_refs: dict[str, Any] = {}
        self.text_dedup_records: list[Any] = []
        self.source_credibility_records: list[Any] = []
        self.external_responses: dict[str, ExternalResponse] = dict(external_responses or {})
        self.audit_records: list[AuditRecord] = []

    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[Any, ...]:
        requested = set(instrument_ids)
        profiles = []
        for profile in self.profiles.values():
            if str(_value(profile, "universe_id")) != universe_id:
                continue
            if requested and str(_value(profile, "instrument_id")) not in requested:
                continue
            if not bool(_value(profile, "is_active", True)):
                continue
            profiles.append(profile)
        return tuple(sorted(profiles, key=lambda profile: str(_value(profile, "instrument_id"))))

    def list_text_source_configs(self, source_types: tuple[str, ...]) -> tuple[Any, ...]:
        requested = set(source_types)
        return tuple(
            config
            for source_type, config in self.text_source_configs.items()
            if not requested or source_type in requested
        )

    def save_discovery_run(self, run: Any) -> str:
        discovery_run_id = str(_value(run, "discovery_run_id"))
        self.discovery_runs[discovery_run_id] = run
        return f"raw_text.scheduled_external_news_discovery_run:{discovery_run_id}"

    def save_discovery_item(self, item: Any) -> str:
        item_id = str(_value(item, "discovery_item_id", "") or _stable_id(_to_mapping(item)))
        ref = f"raw_text.scheduled_external_news_discovery_item:{item_id}"
        self.discovery_items[ref] = item
        return ref

    def save_external_text_search_request(self, record: Any) -> str:
        request_id = str(_value(record, "request_id"))
        ref = f"raw_text.external_text_search_request:{request_id}"
        self.external_text_search_requests[ref] = record
        return ref

    def find_raw_text_item_by_hash(self, content_hash: str) -> Any | None:
        return self.raw_text_items_by_hash.get(content_hash)

    def list_recent_raw_text_items(self, universe_id: str, limit: int = 500) -> tuple[Any, ...]:
        items = [
            item
            for item in self.raw_text_items_by_id.values()
            if str(_value(item, "universe_id", "")) == universe_id
        ]
        return tuple(items[-limit:])

    def save_raw_text_item(self, item: Any) -> str:
        existing = self.raw_text_items_by_hash.get(str(_value(item, "content_hash")))
        if existing is not None:
            return self._raw_text_ref(existing)
        item_id = str(_value(item, "raw_text_item_id"))
        self.raw_text_items_by_id[item_id] = item
        self.raw_text_items_by_hash[str(_value(item, "content_hash"))] = item
        ref = self._raw_text_ref(item)
        self.raw_text_refs[ref] = item
        return ref

    def load_raw_text_item(self, ref: str) -> Any | None:
        if ref in self.raw_text_refs:
            return self.raw_text_refs[ref]
        item_id = ref.rsplit(":", 1)[-1]
        return self.raw_text_items_by_id.get(item_id)

    def save_routing_message(self, message: Any) -> str:
        self.routing_messages.append(message)
        ref = f"raw_text.event_routing_message:{_value(message, 'routing_message_id')}"
        self.routing_refs[ref] = message
        return ref

    def save_text_dedup_record(self, record: Any) -> str:
        self.text_dedup_records.append(record)
        return f"raw_text.text_dedup_record:{len(self.text_dedup_records)}"

    def save_source_credibility_record(self, record: Any) -> str:
        self.source_credibility_records.append(record)
        return f"raw_text.source_credibility_record:{len(self.source_credibility_records)}"

    def load_external_response(self, ref: str) -> ExternalResponse | None:
        return self.external_responses.get(ref) or self.external_responses.get(ref.rsplit(":", 1)[-1])

    def save_external_response(self, ref: str, response: ExternalResponse) -> None:
        self.external_responses[ref] = response
        self.external_responses[response.request_id] = response

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit:memory:{len(self.audit_records)}"

    def _raw_text_ref(self, item: Any) -> str:
        return f"raw_text.raw_text_item:{_value(item, 'raw_text_item_id')}"


class PostgresDataIntakeRoutingRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_active_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[Any, ...]:
        from agent_app.modules.selected_instruments_registry.service import InstrumentProfile

        requested = list(instrument_ids)
        query = """
            SELECT instrument_id, universe_id, ticker, figi, isin, class_code,
                   board_id, lot_size, min_price_increment, currency, sector,
                   issuer_name, aliases, related_entities, is_active, tradable,
                   allowed_horizons, arena_go_secid, arena_go_quantity_mode,
                   metadata, created_at, updated_at
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
        profiles = []
        for row in rows:
            metadata = dict(row[19] or {})
            profiles.append(
                InstrumentProfile(
                    instrument_id=row[0],
                    universe_id=row[1],
                    ticker=row[2],
                    figi=row[3],
                    isin=row[4],
                    class_code=row[5],
                    board_id=row[6],
                    lot_size=row[7],
                    min_price_increment=float(row[8]) if row[8] is not None else None,
                    currency=row[9],
                    sector=row[10],
                    issuer_name=row[11],
                    aliases=tuple(row[12] or ()),
                    related_entities=tuple(row[13] or ()),
                    is_active=bool(row[14]),
                    tradable=bool(row[15]),
                    allowed_horizons=tuple(row[16] or ()),
                    arena_go_secid=row[17],
                    arena_go_quantity_mode=row[18],
                    max_trade_quantity=metadata.get("max_trade_quantity"),
                    execution_enabled=bool(metadata.get("execution_enabled", True)),
                    metadata=metadata,
                    created_at=row[20].isoformat().replace("+00:00", "Z") if row[20] else None,
                    updated_at=row[21].isoformat().replace("+00:00", "Z") if row[21] else None,
                )
            )
        return tuple(profiles)

    def list_text_source_configs(self, source_types: tuple[str, ...]) -> tuple[Any, ...]:
        requested = list(source_types)
        query = """
            SELECT text_source_config_id, source_type, provider, request_type,
                   enabled, discovery_frequency, max_age_seconds, query_template,
                   source_policy
              FROM raw_text.text_source_config
        """
        params: list[Any] = []
        if requested:
            query += " WHERE source_type = ANY(%s)"
            params.append(requested)
        query += " ORDER BY source_type, provider, request_type"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return tuple(
            {
                "text_source_config_id": row[0],
                "source_type": row[1],
                "provider": row[2],
                "request_type": row[3],
                "enabled": bool(row[4]),
                "discovery_frequency": row[5],
                "max_age_seconds": row[6],
                "query_template": row[7] or {},
                "source_policy": row[8] or {},
            }
            for row in rows
        )

    def save_discovery_run(self, run: Any) -> str:
        from psycopg.types.json import Jsonb

        payload = _to_mapping(run)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_text.scheduled_external_news_discovery_run (
                        discovery_run_id, module_job_id, universe_id, discovery_mode,
                        per_instrument_discovery, source_types, time_range,
                        active_instruments_total, active_instruments_with_discovery_request,
                        scheduled_discovery_coverage_ratio, status, started_at,
                        finished_at, source_module, calculation_version, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (discovery_run_id) DO UPDATE SET
                        active_instruments_total = EXCLUDED.active_instruments_total,
                        active_instruments_with_discovery_request = EXCLUDED.active_instruments_with_discovery_request,
                        scheduled_discovery_coverage_ratio = EXCLUDED.scheduled_discovery_coverage_ratio,
                        status = EXCLUDED.status,
                        finished_at = EXCLUDED.finished_at,
                        payload = EXCLUDED.payload
                    """,
                    (
                        payload["discovery_run_id"],
                        payload.get("module_job_id"),
                        payload["universe_id"],
                        payload["discovery_mode"],
                        bool(payload.get("per_instrument_discovery", True)),
                        list(payload.get("source_types") or ()),
                        Jsonb(dict(payload.get("time_range") or {})),
                        int(payload.get("active_instruments_total") or 0),
                        int(payload.get("active_instruments_with_discovery_request") or 0),
                        float(payload.get("scheduled_discovery_coverage_ratio") or 0.0),
                        payload.get("status") or "planned",
                        _optional_timestamp(payload.get("started_at")),
                        _optional_timestamp(payload.get("finished_at")),
                        payload.get("source_module") or "Data Intake & Routing Module",
                        payload.get("calculation_version") or "data_intake_routing_v1",
                        Jsonb(dict(payload.get("payload") or {})),
                    ),
                )
        return f"raw_text.scheduled_external_news_discovery_run:{payload['discovery_run_id']}"

    def save_discovery_item(self, item: Any) -> str:
        from psycopg.types.json import Jsonb

        payload = _to_mapping(item)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_text.scheduled_external_news_discovery_item (
                        discovery_run_id, instrument_id, source_type, text_source_config_id,
                        query_terms, query_payload, status, skip_reason_code, external_request_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (discovery_run_id, instrument_id, source_type) DO UPDATE SET
                        text_source_config_id = EXCLUDED.text_source_config_id,
                        query_terms = EXCLUDED.query_terms,
                        query_payload = EXCLUDED.query_payload,
                        status = EXCLUDED.status,
                        skip_reason_code = EXCLUDED.skip_reason_code,
                        external_request_id = EXCLUDED.external_request_id,
                        updated_at = now()
                    RETURNING discovery_item_id
                    """,
                    (
                        payload["discovery_run_id"],
                        payload.get("instrument_id") or "",
                        payload["source_type"],
                        payload.get("text_source_config_id"),
                        list(payload.get("query_terms") or ()),
                        Jsonb(dict(payload.get("query_payload") or {})),
                        payload.get("status") or "planned",
                        payload.get("skip_reason_code"),
                        payload.get("external_request_id"),
                    ),
                )
                row = cur.fetchone()
        return f"raw_text.scheduled_external_news_discovery_item:{row[0]}"

    def save_external_text_search_request(self, record: Any) -> str:
        from psycopg.types.json import Jsonb

        payload = _to_mapping(record)
        discovery_item_id = _optional_uuid_text(_ref_id(payload.get("discovery_item_ref")))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_text.external_text_search_request (
                        discovery_item_id, request_id, instrument_id, source_type,
                        provider, request_type, query_terms, query_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id) DO UPDATE SET
                        discovery_item_id = EXCLUDED.discovery_item_id,
                        instrument_id = EXCLUDED.instrument_id,
                        source_type = EXCLUDED.source_type,
                        provider = EXCLUDED.provider,
                        request_type = EXCLUDED.request_type,
                        query_terms = EXCLUDED.query_terms,
                        query_payload = EXCLUDED.query_payload
                    RETURNING external_text_search_request_id
                    """,
                    (
                        discovery_item_id,
                        payload["request_id"],
                        payload.get("instrument_id") or "",
                        payload["source_type"],
                        payload["provider"],
                        payload.get("request_type") or "text_search",
                        list(payload.get("query_terms") or ()),
                        Jsonb(dict(payload.get("query_payload") or {})),
                    ),
                )
                row = cur.fetchone()
        return f"raw_text.external_text_search_request:{row[0]}"

    def find_raw_text_item_by_hash(self, content_hash: str) -> Any | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_text_item_id, universe_id, instrument_ids,
                           COALESCE(source_type, source) AS source_type,
                           source_url, title, body, language, published_at,
                           fetched_at, content_hash, source_payload,
                           discovery_run_id, discovery_item_id, discovery_mode,
                           external_request_id
                      FROM raw_text.raw_text_item
                     WHERE content_hash = %s
                    """,
                    (content_hash,),
                )
                row = cur.fetchone()
        return _raw_text_item_from_row(row)

    def list_recent_raw_text_items(self, universe_id: str, limit: int = 500) -> tuple[Any, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_text_item_id, universe_id, instrument_ids,
                           COALESCE(source_type, source) AS source_type,
                           source_url, title, body, language, published_at,
                           fetched_at, content_hash, source_payload,
                           discovery_run_id, discovery_item_id, discovery_mode,
                           external_request_id
                      FROM raw_text.raw_text_item
                     WHERE universe_id = %s
                     ORDER BY fetched_at DESC
                     LIMIT %s
                    """,
                    (universe_id, limit),
                )
                rows = cur.fetchall()
        return tuple(item for item in (_raw_text_item_from_row(row) for row in rows) if item is not None)

    def save_raw_text_item(self, item: Any) -> str:
        from psycopg.types.json import Jsonb

        existing = self.find_raw_text_item_by_hash(str(_value(item, "content_hash")))
        if existing is not None:
            return f"raw_text.raw_text_item:{_value(existing, 'raw_text_item_id')}"
        published_at = _optional_timestamp(_value(item, "published_at"))
        fetched_at = _optional_timestamp(_value(item, "fetched_at"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_text.raw_text_item (
                        universe_id, instrument_ids, source, source_type, source_url, title, body,
                        language, published_at, fetched_at, content_hash, source_payload,
                        discovery_run_id, discovery_item_id, discovery_mode, external_request_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s, %s, %s, %s, %s)
                    RETURNING raw_text_item_id
                    """,
                    (
                        _value(item, "universe_id"),
                        list(_value(item, "instrument_ids", ())),
                        _value(item, "source_type"),
                        _value(item, "source_type"),
                        _value(item, "source_url"),
                        _value(item, "title"),
                        _value(item, "body"),
                        _value(item, "language"),
                        published_at,
                        fetched_at,
                        _value(item, "content_hash"),
                        Jsonb(dict(_to_mapping(item))),
                        _value(item, "discovery_run_id"),
                        _optional_uuid_text(_value(item, "discovery_item_id")),
                        _value(item, "discovery_mode"),
                        _value(item, "external_request_id"),
                    ),
                )
                row = cur.fetchone()
        return f"raw_text.raw_text_item:{row[0]}"

    def load_raw_text_item(self, ref: str) -> Any | None:
        raw_text_item_id = ref.rsplit(":", 1)[-1]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_text_item_id, universe_id, instrument_ids,
                           COALESCE(source_type, source) AS source_type,
                           source_url, title, body, language, published_at,
                           fetched_at, content_hash, source_payload,
                           discovery_run_id, discovery_item_id, discovery_mode,
                           external_request_id
                      FROM raw_text.raw_text_item
                     WHERE raw_text_item_id = %s
                    """,
                    (raw_text_item_id,),
                )
                row = cur.fetchone()
        return _raw_text_item_from_row(row)

    def save_routing_message(self, message: Any) -> str:
        raw_text_item_id = str(_value(message, "raw_text_ref")).rsplit(":", 1)[-1]
        refs = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                for target_module in _value(message, "target_modules", ()):
                    cur.execute(
                        """
                        INSERT INTO raw_text.event_routing_message (
                            raw_text_item_id, target_module, universe_id, instrument_ids,
                            routing_reason, routing_ttl_seconds, status,
                            discovery_mode, discovery_run_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING routing_message_id
                        """,
                        (
                            raw_text_item_id,
                            target_module,
                            _value(message, "universe_id"),
                            list(_value(message, "instrument_ids", ())),
                            _value(message, "routing_reason"),
                            _value(message, "routing_ttl_seconds"),
                            _value(message, "status", "pending"),
                            _value(message, "discovery_mode"),
                            _value(message, "discovery_run_id"),
                        ),
                    )
                    row = cur.fetchone()
                    refs.append(f"raw_text.event_routing_message:{row[0]}")
        return ",".join(refs)

    def save_text_dedup_record(self, record: Any) -> str:
        return f"raw_text.text_dedup_record:{_value(record, 'content_hash')}"

    def save_source_credibility_record(self, record: Any) -> str:
        return f"raw_text.source_credibility_record:{_value(record, 'source_ref')}"

    def load_external_response(self, ref: str) -> ExternalResponse | None:
        request_id = ref.rsplit(":", 1)[-1]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT request_id, provider, status, data_ref, received_at,
                           latency_ms, cost_units, cache_hit, warnings, errors,
                           provider_tracking_id, payload
                      FROM request_logs.external_response
                     WHERE request_id = %s
                    """,
                    (request_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        payload = row[11] or {}
        return ExternalResponse(
            request_id=row[0],
            provider=row[1],
            status=row[2],
            data_ref=row[3] or "",
            received_at=row[4].isoformat().replace("+00:00", "Z"),
            latency_ms=int(row[5] or 0),
            cost_units=float(row[6] or 0.0),
            cache_hit=bool(row[7]),
            warnings=tuple(row[8] or ()),
            errors=tuple(row[9] or ()),
            provider_tracking_id=row[10] or "",
            data=payload.get("data") or {},
        )

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


def _raw_text_item_from_row(row: Any | None) -> Any | None:
    if row is None:
        return None
    from .service import RawTextItem

    source_payload = row[11] or {}
    source_type = row[3] or source_payload.get("source_type") or "unknown"
    if source_type == "macro_api":
        source_type = "macro_text"
    return RawTextItem(
        raw_text_item_id=str(row[0]),
        universe_id=row[1] or "",
        instrument_ids=tuple(row[2] or ()),
        source_type=source_type,
        source_ref=str(source_payload.get("source_ref") or row[4] or row[0]),
        source_url=row[4],
        title=row[5] or "",
        body=row[6] or "",
        language=row[7] or "unknown",
        published_at=row[8].isoformat().replace("+00:00", "Z") if row[8] else None,
        fetched_at=row[9].isoformat().replace("+00:00", "Z") if row[9] else None,
        content_hash=row[10] or "",
        source_payload=source_payload,
        discovery_run_id=row[12],
        discovery_item_id=str(row[13]) if row[13] else None,
        discovery_mode=row[14],
        external_request_id=row[15],
    )


def _optional_timestamp(value: Any) -> Any | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return parse_utc_iso(value)
    return value


def _optional_uuid_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _ref_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).rsplit(":", 1)[-1]


def _stable_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _to_mapping(record: Any) -> Mapping[str, Any]:
    if hasattr(record, "to_dict"):
        return record.to_dict()
    if isinstance(record, Mapping):
        return record
    return dict(getattr(record, "__dict__", {}))


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)
