from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects import ExternalRequest, ExternalResponse


SENSITIVE_PAYLOAD_TOKENS = ("authorization", "token", "api_key", "secret", "password")


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url_env: str | None = None
    auth_header: str | None = None
    auth_value_source: str | None = None
    config_payload: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    default_base_url: str | None = None


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    provider: str
    request_type: str
    data_ref: str
    payload: Mapping[str, Any]
    expires_at: datetime | None
    created_at: datetime

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at >= now


def utc_now() -> datetime:
    return datetime.now(UTC)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in SENSITIVE_PAYLOAD_TOKENS):
                sanitized[key_text] = "[redacted]"
            else:
                sanitized[key_text] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    return value


class ExternalRequestGatewayRepository(Protocol):
    def load_provider_config(self, provider: str) -> ProviderConfig | None:
        ...

    def get_cached_response(self, cache_key: str, now: datetime) -> CacheEntry | None:
        ...

    def save_cache_entry(
        self,
        request: ExternalRequest,
        response: ExternalResponse,
        payload: Mapping[str, Any],
        now: datetime,
    ) -> None:
        ...

    def get_response_by_idempotency_key(
        self,
        provider: str,
        request_type: str,
        idempotency_key: str,
    ) -> ExternalResponse | None:
        ...

    def save_external_request(self, request: ExternalRequest) -> None:
        ...

    def save_external_response(self, request: ExternalRequest, response: ExternalResponse) -> None:
        ...

    def write_raw_data(self, request: ExternalRequest, payload: Mapping[str, Any], received_at: str) -> str:
        ...

    def write_request_log(self, request: ExternalRequest, response: ExternalResponse) -> str:
        ...

    def record_rate_limit_event(self, provider: str, request_type: str, now: datetime) -> bool:
        ...


class InMemoryExternalRequestGatewayRepository:
    def __init__(
        self,
        provider_configs: Mapping[str, ProviderConfig] | None = None,
        rate_limit_per_minute: Mapping[str, int] | None = None,
    ) -> None:
        self.provider_configs = dict(provider_configs or default_provider_configs())
        self.rate_limit_per_minute = dict(rate_limit_per_minute or {})
        self.requests: dict[str, ExternalRequest] = {}
        self.responses: dict[str, ExternalResponse] = {}
        self.responses_by_idempotency: dict[tuple[str, str, str], ExternalResponse] = {}
        self.request_logs: list[dict[str, Any]] = []
        self.cache_entries: dict[str, CacheEntry] = {}
        self.raw_market_data: dict[str, Mapping[str, Any]] = {}
        self.raw_text_items: dict[str, Mapping[str, Any]] = {}
        self.raw_macro_data: dict[str, Mapping[str, Any]] = {}
        self.broker_responses: dict[str, Mapping[str, Any]] = {}
        self.rate_limit_events: list[tuple[str, str, datetime]] = []

    def load_provider_config(self, provider: str) -> ProviderConfig | None:
        return self.provider_configs.get(provider)

    def get_cached_response(self, cache_key: str, now: datetime) -> CacheEntry | None:
        entry = self.cache_entries.get(cache_key)
        if entry is None or not entry.is_fresh(now):
            return None
        return entry

    def save_cache_entry(
        self,
        request: ExternalRequest,
        response: ExternalResponse,
        payload: Mapping[str, Any],
        now: datetime,
    ) -> None:
        if request.cache_policy.max_age_seconds <= 0:
            expires_at = None
        else:
            expires_at = now + timedelta(seconds=request.cache_policy.max_age_seconds)
        self.cache_entries[request.cache_key] = CacheEntry(
            cache_key=request.cache_key,
            provider=request.provider,
            request_type=request.request_type,
            data_ref=response.data_ref,
            payload=dict(payload),
            expires_at=expires_at,
            created_at=now,
        )

    def get_response_by_idempotency_key(
        self,
        provider: str,
        request_type: str,
        idempotency_key: str,
    ) -> ExternalResponse | None:
        return self.responses_by_idempotency.get((provider, request_type, idempotency_key))

    def save_external_request(self, request: ExternalRequest) -> None:
        self.requests[request.request_id] = request

    def save_external_response(self, request: ExternalRequest, response: ExternalResponse) -> None:
        self.responses[response.request_id] = response
        self.responses_by_idempotency[(request.provider, request.request_type, request.idempotency_key)] = response

    def write_raw_data(self, request: ExternalRequest, payload: Mapping[str, Any], received_at: str) -> str:
        ref = f"{request.provider}:{request.request_type}:{request.request_id}"
        record = {
            "request_id": request.request_id,
            "provider": request.provider,
            "request_type": request.request_type,
            "universe_id": request.universe_id,
            "instrument_ids": list(request.instrument_ids),
            "received_at": received_at,
            "payload": sanitize_payload(payload),
        }
        if request.request_type in {"market_data", "orderbook", "trades", "instruments"}:
            self.raw_market_data[ref] = record
            return f"raw_market:{ref}"
        if request.request_type in {"text_search", "text_fetch", "llm_completion"}:
            self.raw_text_items[ref] = record
            return f"raw_text:{ref}"
        if request.request_type == "macro_series":
            self.raw_macro_data[ref] = record
            return f"raw_macro:{ref}"
        self.broker_responses[ref] = record
        return f"request_logs.external_response:{request.request_id}"

    def write_request_log(self, request: ExternalRequest, response: ExternalResponse) -> str:
        payload = {
            "external_request": {
                **request.to_dict(),
                "payload": sanitize_payload(request.payload),
            },
            "external_response": {
                **response.to_dict(),
                "data": sanitize_payload(response.data),
            },
        }
        record = {
            "request_id": request.request_id,
            "caller_module": request.caller_module,
            "provider": request.provider,
            "request_type": request.request_type,
            "status": response.status,
            "latency_ms": response.latency_ms,
            "cost_units": response.cost_units,
            "cache_hit": response.cache_hit,
            "payload": payload,
        }
        self.request_logs.append(record)
        return f"request_log:memory:{len(self.request_logs)}"

    def record_rate_limit_event(self, provider: str, request_type: str, now: datetime) -> bool:
        limit = self.rate_limit_per_minute.get(provider)
        if not limit:
            return True
        window_start = now - timedelta(minutes=1)
        recent = [
            event
            for event in self.rate_limit_events
            if event[0] == provider and event[2] >= window_start
        ]
        if len(recent) >= limit:
            return False
        self.rate_limit_events.append((provider, request_type, now))
        return True


class PostgresExternalRequestGatewayRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def load_provider_config(self, provider: str) -> ProviderConfig | None:
        query = """
            SELECT provider, base_url_env, auth_header, auth_value_source, config_payload, enabled
              FROM request_logs.provider_config
             WHERE provider = %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (provider,))
                row = cur.fetchone()
        if row is None:
            return default_provider_configs().get(provider)
        defaults = default_provider_configs().get(provider)
        return ProviderConfig(
            provider=row[0],
            base_url_env=row[1],
            auth_header=row[2],
            auth_value_source=row[3],
            config_payload=row[4] or {},
            enabled=bool(row[5]),
            default_base_url=(row[4] or {}).get("default_base_url") or (defaults.default_base_url if defaults else None),
        )

    def get_cached_response(self, cache_key: str, now: datetime) -> CacheEntry | None:
        query = """
            SELECT cache_key, provider, request_type, data_ref, payload, expires_at, created_at
              FROM request_logs.request_cache
             WHERE cache_key = %s
               AND (expires_at IS NULL OR expires_at >= %s)
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (cache_key, now))
                row = cur.fetchone()
        if row is None:
            return None
        return CacheEntry(
            cache_key=row[0],
            provider=row[1],
            request_type=row[2],
            data_ref=row[3],
            payload=row[4] or {},
            expires_at=row[5],
            created_at=row[6],
        )

    def save_cache_entry(
        self,
        request: ExternalRequest,
        response: ExternalResponse,
        payload: Mapping[str, Any],
        now: datetime,
    ) -> None:
        from psycopg.types.json import Jsonb

        expires_at = None
        if request.cache_policy.max_age_seconds > 0:
            expires_at = now + timedelta(seconds=request.cache_policy.max_age_seconds)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO request_logs.request_cache (
                        cache_key, provider, request_type, data_ref, payload, expires_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        data_ref = EXCLUDED.data_ref,
                        payload = EXCLUDED.payload,
                        expires_at = EXCLUDED.expires_at,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        request.cache_key,
                        request.provider,
                        request.request_type,
                        response.data_ref,
                        Jsonb(sanitize_payload(payload)),
                        expires_at,
                        now,
                    ),
                )

    def get_response_by_idempotency_key(
        self,
        provider: str,
        request_type: str,
        idempotency_key: str,
    ) -> ExternalResponse | None:
        query = """
            SELECT er.request_id, er.provider, er.status, er.data_ref, er.received_at,
                   er.latency_ms, er.cost_units, er.cache_hit, er.warnings, er.errors,
                   er.provider_tracking_id, er.payload
              FROM request_logs.external_request req
              JOIN request_logs.external_response er ON er.request_id = req.request_id
             WHERE req.provider = %s
               AND req.request_type = %s
               AND req.idempotency_key = %s
             ORDER BY er.received_at DESC
             LIMIT 1
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (provider, request_type, idempotency_key))
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

    def save_external_request(self, request: ExternalRequest) -> None:
        from psycopg.types.json import Jsonb

        payload = request.to_dict()
        payload["payload"] = sanitize_payload(payload["payload"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO request_logs.external_request (
                        request_id, caller_module, provider, request_type, universe_id,
                        instrument_ids, payload, cache_policy, timeout_ms, retry_policy,
                        idempotency_key
                    ) VALUES (
                        %(request_id)s, %(caller_module)s, %(provider)s, %(request_type)s, %(universe_id)s,
                        %(instrument_ids)s, %(payload)s, %(cache_policy)s, %(timeout_ms)s, %(retry_policy)s,
                        %(idempotency_key)s
                    )
                    ON CONFLICT (request_id) DO NOTHING
                    """,
                    {
                        **payload,
                        "payload": Jsonb(payload["payload"]),
                        "cache_policy": Jsonb(payload["cache_policy"]),
                        "retry_policy": Jsonb(payload["retry_policy"]),
                    },
                )

    def save_external_response(self, request: ExternalRequest, response: ExternalResponse) -> None:
        from psycopg.types.json import Jsonb

        payload = {"data": sanitize_payload(response.data)}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO request_logs.external_response (
                        request_id, provider, status, data_ref, received_at, latency_ms,
                        cost_units, cache_hit, warnings, errors, provider_tracking_id, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        status = EXCLUDED.status,
                        data_ref = EXCLUDED.data_ref,
                        received_at = EXCLUDED.received_at,
                        latency_ms = EXCLUDED.latency_ms,
                        cost_units = EXCLUDED.cost_units,
                        cache_hit = EXCLUDED.cache_hit,
                        warnings = EXCLUDED.warnings,
                        errors = EXCLUDED.errors,
                        provider_tracking_id = EXCLUDED.provider_tracking_id,
                        payload = EXCLUDED.payload
                    """,
                    (
                        response.request_id,
                        response.provider,
                        response.status,
                        response.data_ref,
                        response.received_at,
                        response.latency_ms,
                        response.cost_units,
                        response.cache_hit,
                        list(response.warnings),
                        list(response.errors),
                        response.provider_tracking_id,
                        Jsonb(payload),
                    ),
                )

    def write_raw_data(self, request: ExternalRequest, payload: Mapping[str, Any], received_at: str) -> str:
        from psycopg.types.json import Jsonb

        sanitized = _mapping_payload(sanitize_payload(payload))
        refs: list[str] = []
        if request.request_type == "market_data":
            refs = self._write_market_data(request, sanitized, received_at, Jsonb)
        elif request.request_type == "trades":
            refs = self._write_trades(request, sanitized, received_at, Jsonb)
        elif request.request_type == "orderbook":
            refs = self._write_orderbook(request, sanitized, received_at, Jsonb)
        elif request.request_type in {"text_search", "text_fetch", "llm_completion"}:
            refs = self._write_text_items(request, sanitized, received_at, Jsonb)
        elif request.request_type == "macro_series":
            refs = self._write_macro_points(request, sanitized, received_at, Jsonb)
        return ",".join(refs) if refs else f"request_logs.external_response:{request.request_id}"

    def _write_market_data(
        self,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> list[str]:
        refs: list[str] = []
        items = _raw_items(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                for item in items:
                    index_ref = self._insert_index_value(cur, request, payload, item, received_at, jsonb_type)
                    if index_ref:
                        refs.append(index_ref)
                        continue
                    candle_ref = self._insert_candle(cur, request, payload, item, received_at, jsonb_type)
                    if candle_ref:
                        refs.append(candle_ref)
        return refs

    def _insert_candle(
        self,
        cur: Any,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        item: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> str:
        open_price = _numeric(_lookup(item, "open_price", "openprice", "open", "o"))
        high_price = _numeric(_lookup(item, "high_price", "highprice", "high", "h"))
        low_price = _numeric(_lookup(item, "low_price", "lowprice", "low", "l"))
        close_price = _numeric(_lookup(item, "close_price", "closeprice", "close", "c", "last", "lastprice"))
        if all(value is None for value in (open_price, high_price, low_price, close_price)):
            return ""
        open_ts = _timestamp_or(
            _lookup(item, "open_ts", "begin", "start", "timestamp", "ts", "date", "tradedate"),
            received_at,
        )
        close_ts = _timestamp_text(_lookup(item, "close_ts", "end", "finish"))
        timeframe = _text(
            _lookup(item, "timeframe", "interval")
            or _first(request.payload.get("timeframes"))
            or request.payload.get("timeframe")
            or "unknown"
        )
        cur.execute(
            """
            INSERT INTO raw_market.raw_candle (
                instrument_id, universe_id, board_id, timeframe, open_ts, close_ts,
                open_price, high_price, low_price, close_price, volume, turnover,
                provider, source_payload, received_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING raw_candle_id
            """,
            (
                _instrument_id(request, item),
                request.universe_id,
                _text(_lookup(item, "board_id", "boardid", "board") or request.payload.get("board") or request.payload.get("board_id")),
                timeframe,
                open_ts,
                close_ts,
                open_price,
                high_price,
                low_price,
                close_price,
                _numeric(_lookup(item, "volume", "vol", "quantity")),
                _numeric(_lookup(item, "turnover", "value", "trade_value")),
                request.provider,
                jsonb_type(_source_payload(payload, item)),
                received_at,
            ),
        )
        row = cur.fetchone()
        return f"raw_market.raw_candle:{row[0]}" if row else ""

    def _insert_index_value(
        self,
        cur: Any,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        item: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> str:
        index_id = _text(
            _lookup(item, "index_id", "indexid", "index", "index_code")
            or request.payload.get("index_id")
            or request.payload.get("index")
        )
        if not index_id:
            return ""
        value = _numeric(_lookup(item, "value", "index_value", "close", "last", "price"))
        if value is None:
            return ""
        value_ts = _timestamp_or(_lookup(item, "value_ts", "timestamp", "ts", "date", "tradedate", "time"), received_at)
        cur.execute(
            """
            INSERT INTO raw_market.raw_index_value (
                index_id, value_ts, value, provider, source_payload, received_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING raw_index_value_id
            """,
            (
                index_id,
                value_ts,
                value,
                request.provider,
                jsonb_type(_source_payload(payload, item)),
                received_at,
            ),
        )
        row = cur.fetchone()
        return f"raw_market.raw_index_value:{row[0]}" if row else ""

    def _write_trades(
        self,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> list[str]:
        refs: list[str] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                for item in _raw_items(payload):
                    price = _numeric(_lookup(item, "price", "last", "lastprice"))
                    quantity = _numeric(_lookup(item, "quantity", "qty", "volume", "vol"))
                    if price is None and quantity is None:
                        continue
                    cur.execute(
                        """
                        INSERT INTO raw_market.raw_trade (
                            instrument_id, universe_id, trade_ts, price, quantity, side,
                            trade_value, provider, provider_trade_id, source_payload, received_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING raw_trade_id
                        """,
                        (
                            _instrument_id(request, item),
                            request.universe_id,
                            _timestamp_or(_lookup(item, "trade_ts", "tradetime", "timestamp", "ts", "date", "tradedate"), received_at),
                            price,
                            quantity,
                            _text(_lookup(item, "side", "buysell", "direction")),
                            _numeric(_lookup(item, "trade_value", "value", "turnover")),
                            request.provider,
                            _text(_lookup(item, "provider_trade_id", "tradeno", "trade_id", "id")),
                            jsonb_type(_source_payload(payload, item)),
                            received_at,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        refs.append(f"raw_market.raw_trade:{row[0]}")
        return refs

    def _write_orderbook(
        self,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> list[str]:
        items = _raw_items(payload)
        bids, asks = _orderbook_sides(payload, items)
        if not bids and not asks:
            return []
        first_item = items[0] if items else {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_market.raw_orderbook (
                        instrument_id, universe_id, snapshot_ts, bids, asks,
                        provider, source_payload, received_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING raw_orderbook_id
                    """,
                    (
                        _instrument_id(request, first_item),
                        request.universe_id,
                        _timestamp_or(
                            _lookup(first_item, "snapshot_ts", "timestamp", "ts", "systime", "date", "tradedate")
                            or _lookup(payload, "snapshot_ts", "timestamp", "ts"),
                            received_at,
                        ),
                        jsonb_type(bids),
                        jsonb_type(asks),
                        request.provider,
                        jsonb_type(payload),
                        received_at,
                    ),
                )
                row = cur.fetchone()
        return [f"raw_market.raw_orderbook:{row[0]}"] if row else []

    def _write_text_items(
        self,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> list[str]:
        refs: list[str] = []
        items = [payload] if request.request_type == "llm_completion" else _raw_items(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                for item in items:
                    source_url = _text(_lookup(item, "source_url", "url", "link", "href"))
                    title = _text(_lookup(item, "title", "headline", "name"))
                    body = _text(_lookup(item, "body", "text", "content", "description", "summary", "message"))
                    if not body and request.request_type == "llm_completion":
                        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    if not (source_url or title or body):
                        continue
                    source_payload = _source_payload(payload, item)
                    content_hash = _text(_lookup(item, "content_hash", "hash")) or _content_hash(
                        {
                            "source": request.provider,
                            "source_url": source_url,
                            "title": title,
                            "body": body,
                            "payload": source_payload,
                        }
                    )
                    cur.execute(
                        """
                        INSERT INTO raw_text.raw_text_item (
                            universe_id, instrument_ids, source, source_url, title, body,
                            language, published_at, fetched_at, content_hash, source_payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (content_hash) DO UPDATE SET
                            fetched_at = EXCLUDED.fetched_at,
                            source_payload = EXCLUDED.source_payload
                        RETURNING raw_text_item_id
                        """,
                        (
                            request.universe_id,
                            list(request.instrument_ids),
                            request.provider,
                            source_url or None,
                            title or None,
                            body or None,
                            _text(_lookup(item, "language", "lang")) or None,
                            _timestamp_text(_lookup(item, "published_at", "published", "date", "created_at")),
                            received_at,
                            content_hash,
                            jsonb_type(source_payload),
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        refs.append(f"raw_text.raw_text_item:{row[0]}")
        return refs

    def _write_macro_points(
        self,
        request: ExternalRequest,
        payload: Mapping[str, Any],
        received_at: str,
        jsonb_type: Any,
    ) -> list[str]:
        refs: list[str] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                for item in _raw_items(payload):
                    series_id = _text(
                        _lookup(item, "series_id", "series", "id", "code")
                        or request.payload.get("series_id")
                        or _first(request.payload.get("series_ids"))
                    )
                    point_ts = _timestamp_text(_lookup(item, "point_ts", "timestamp", "ts", "date", "period", "time"))
                    value = _numeric(_lookup(item, "value", "close", "last", "rate"))
                    if not series_id or point_ts is None or value is None:
                        continue
                    cur.execute(
                        """
                        INSERT INTO raw_macro.raw_macro_point (
                            series_id, point_ts, value, unit, provider, source_payload, received_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (series_id, point_ts, provider) DO UPDATE SET
                            value = EXCLUDED.value,
                            unit = EXCLUDED.unit,
                            source_payload = EXCLUDED.source_payload,
                            received_at = EXCLUDED.received_at
                        RETURNING raw_macro_point_id
                        """,
                        (
                            series_id,
                            point_ts,
                            value,
                            _text(_lookup(item, "unit", "units")),
                            request.provider,
                            jsonb_type(_source_payload(payload, item)),
                            received_at,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        refs.append(f"raw_macro.raw_macro_point:{row[0]}")
        return refs

    def write_request_log(self, request: ExternalRequest, response: ExternalResponse) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO request_logs.external_request_log (
                        request_id, caller_module, provider, request_type, status,
                        latency_ms, cost_units, cache_hit, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING external_request_log_id
                    """,
                    (
                        request.request_id,
                        request.caller_module,
                        request.provider,
                        request.request_type,
                        response.status,
                        response.latency_ms,
                        response.cost_units,
                        response.cache_hit,
                        Jsonb(
                            {
                                "external_request": {
                                    **request.to_dict(),
                                    "payload": sanitize_payload(request.payload),
                                },
                                "external_response": response.to_payload_dict(),
                            }
                        ),
                    ),
                )
                row = cur.fetchone()
        return str(row[0])

    def record_rate_limit_event(self, provider: str, request_type: str, now: datetime) -> bool:
        return True


def default_provider_configs() -> dict[str, ProviderConfig]:
    return {
        "moex_iss": ProviderConfig(
            provider="moex_iss",
            base_url_env="MOEX_ISS_BASE_URL",
            config_payload={
                "default_base_url": "https://iss.moex.com/iss",
                "allowed_request_types": ["market_data", "orderbook", "trades", "instruments"],
                "gateway_only": True,
                "cache_defaults": {"market_data": 60, "instruments": 86400},
            },
            default_base_url="https://iss.moex.com/iss",
        ),
        "moex_fast": ProviderConfig(
            provider="moex_fast",
            base_url_env="MOEX_FAST_BASE_URL",
            config_payload={
                "allowed_request_types": ["market_data", "orderbook", "trades"],
                "gateway_only": True,
                "requires_direct_market_data_contract": True,
            },
            enabled=False,
        ),
        "arena_go": ProviderConfig(
            provider="arena_go",
            base_url_env="ARENA_GO_BASE_URL",
            auth_header="Authorization",
            auth_value_source="ARENA_GO_TOKEN",
            config_payload={
                "default_base_url": "https://arenago.ru/api",
                "portfolio_env": "ARENA_GO_PORTFOLIO",
                "bot_name_env": "ARENA_GO_BOT_NAME",
                "allowed_request_types": ["submit_order", "get_trades", "get_positions", "get_bots"],
            },
            default_base_url="https://arenago.ru/api",
        ),
        "polza_ai": ProviderConfig(
            provider="polza_ai",
            base_url_env="POLZA_BASE_URL",
            auth_header="Authorization",
            auth_value_source="POLZA_API_KEY",
            config_payload={
                "default_base_url": "https://polza.ai/api/v1",
                "auth_scheme": "Bearer",
                "default_model_env": "POLZA_LLM_MODEL",
                "response_format": "json_object",
                "temperature": 0,
                "allowed_request_types": ["llm_completion"],
            },
            default_base_url="https://polza.ai/api/v1",
        ),
        "news_api": ProviderConfig(
            provider="news_api",
            base_url_env="NEWS_API_BASE_URL",
            auth_header="Authorization",
            auth_value_source="NEWS_API_KEY",
            config_payload={
                "allowed_request_types": ["text_search", "text_fetch"],
                "gateway_only": True,
                "raw_store": "raw_text.raw_text_item",
            },
            enabled=False,
        ),
        "issuer_disclosure": ProviderConfig(
            provider="issuer_disclosure",
            base_url_env="ISSUER_DISCLOSURE_BASE_URL",
            config_payload={
                "allowed_request_types": ["text_search", "text_fetch"],
                "gateway_only": True,
                "raw_store": "raw_text.raw_text_item",
            },
            enabled=False,
        ),
        "macro_api": ProviderConfig(
            provider="macro_api",
            base_url_env="MACRO_API_BASE_URL",
            auth_header="Authorization",
            auth_value_source="MACRO_API_KEY",
            config_payload={
                "allowed_request_types": ["macro_series", "text_search", "text_fetch"],
                "gateway_only": True,
                "raw_store": "raw_macro.raw_macro_point",
            },
            enabled=False,
        ),
        "broker_api": ProviderConfig(
            provider="broker_api",
            base_url_env="BROKER_API_BASE_URL",
            auth_header="Authorization",
            auth_value_source="BROKER_API_TOKEN",
            config_payload={
                "allowed_request_types": ["orders", "portfolio"],
                "gateway_only": True,
                "preferred_provider_for_execution": "arena_go",
            },
            enabled=False,
        ),
        "internal_cache": ProviderConfig(
            provider="internal_cache",
            config_payload={
                "allowed_request_types": [
                    "market_data",
                    "orderbook",
                    "trades",
                    "instruments",
                    "text_search",
                    "text_fetch",
                    "macro_series",
                ],
                "gateway_only": True,
            },
        ),
    }


def _mapping_payload(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return {"items": value}
    return {"value": value}


def _raw_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = payload.get("items")
    if isinstance(items, list):
        return [_coerce_item(item) for item in items]
    if isinstance(items, Mapping):
        return [dict(items)]
    raw = payload.get("raw")
    if isinstance(raw, Mapping):
        for key in (
            "candles",
            "trades",
            "orderbook",
            "orderbooks",
            "marketdepth",
            "marketdata",
            "securities",
            "points",
            "series",
            "data",
            "results",
            "documents",
            "news",
            "document",
            "item",
        ):
            records = _records_from_provider_value(raw.get(key))
            if records:
                return records
    records = _records_from_provider_value(payload)
    return records if records else [dict(payload)] if payload else []


def _records_from_provider_value(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [_coerce_item(item) for item in value]
    if isinstance(value, Mapping):
        columns = value.get("columns")
        rows = value.get("data")
        if isinstance(columns, list) and isinstance(rows, list):
            records: list[Mapping[str, Any]] = []
            for row in rows:
                if isinstance(row, list):
                    records.append({str(column).lower(): item for column, item in zip(columns, row)})
            return records
        for key in ("items", "data", "results", "points", "documents"):
            records = _records_from_provider_value(value.get(key))
            if records:
                return records
        return [dict(value)] if value else []
    return []


def _coerce_item(item: Any) -> Mapping[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    return {"value": item}


def _lookup(payload: Mapping[str, Any], *names: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        key = str(name).lower()
        if key in lowered:
            return lowered[key]
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _timestamp_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def _timestamp_or(value: Any, fallback: str) -> str:
    return _timestamp_text(value) or fallback


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def _instrument_id(request: ExternalRequest, item: Mapping[str, Any]) -> str:
    candidate = _text(_lookup(item, "instrument_id", "instrument", "secid", "security", "ticker", "symbol"))
    if request.instrument_ids:
        if not candidate:
            return request.instrument_ids[0]
        stripped_candidate = _strip_moex_prefix(candidate)
        for instrument_id in request.instrument_ids:
            if _strip_moex_prefix(instrument_id) == stripped_candidate:
                return instrument_id
    if request.provider in {"moex_iss", "moex_fast"} and candidate and not candidate.startswith("moex:"):
        return f"moex:{candidate}"
    return candidate


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text


def _source_payload(payload: Mapping[str, Any], item: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"item": dict(item), "provider_response": dict(payload)}


def _content_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _orderbook_sides(
    payload: Mapping[str, Any],
    items: list[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    bids = _side_list(payload.get("bids"))
    asks = _side_list(payload.get("asks"))
    raw = payload.get("raw")
    if isinstance(raw, Mapping):
        bids.extend(_side_list(raw.get("bids")))
        asks.extend(_side_list(raw.get("asks")))
    for item in items:
        side = _text(_lookup(item, "side", "buysell", "direction")).lower()
        price = _numeric(_lookup(item, "price", "bid", "ask"))
        quantity = _numeric(_lookup(item, "quantity", "qty", "volume", "vol"))
        if price is None or quantity is None:
            continue
        level = {"price": price, "quantity": quantity}
        if side in {"b", "buy", "bid", "1"}:
            bids.append(level)
        elif side in {"s", "sell", "ask", "offer", "2"}:
            asks.append(level)
    return bids, asks


def _side_list(value: Any) -> list[Mapping[str, Any]]:
    rows = _records_from_provider_value(value)
    levels: list[Mapping[str, Any]] = []
    for row in rows:
        price = _numeric(_lookup(row, "price", "p"))
        quantity = _numeric(_lookup(row, "quantity", "qty", "volume", "vol", "q"))
        if price is not None and quantity is not None:
            levels.append({"price": price, "quantity": quantity})
    return levels
