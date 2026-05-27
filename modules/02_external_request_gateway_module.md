# External Request Gateway Module — Шлюз внешних запросов

## 1. Назначение

Единая точка всех внешних запросов: MOEX ISS, low-latency market data adapters, broker API, LLM API, news API, issuer disclosure sources, macro providers. Скрывает детали провайдеров от остальных модулей.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `External Request Gateway Module` |
| `module_type` | `service` |
| `primary_contour` | `service_contour` |
| `secondary_contours` | `all_contours` |
| `execution_mode` | `sync + async + stream_adapter` |
| `llm_usage` | `gateway_only_for_llm_provider` |

## 3. Запуск

`External Request Gateway Module` является сервисным интерфейсом для внешних запросов и не требует входного `module_job` для штатной обработки. Любой авторизованный модуль может передать в gateway `external_request`; gateway выполняет provider routing, auth, cache, retries, logging и нормализацию ответа.

Служебные health-check, replay, cache maintenance и provider diagnostics могут запускаться через `module_job`, но обычный внешний запрос не должен ждать отдельного orchestration job.

### Required service request fields

- `request_id`
- `caller_module`
- `provider`
- `request_type`
- `payload`
- `idempotency_key`

### Trigger policy

Работает как dependency service. Вызывается модулями через `external_request`. Может работать как unary request, batch request или stream subscription manager. Не разрешает модулям выполнять прямой HTTP-вызов к provider.

## 4. Input classification

- `external_request`
- `provider_credentials_ref`
- `rate_limit_config`
- `cache_policy`
- `retry_policy`

## 5. Input contract

```json
{
  "external_request": {
    "request_id": "string",
    "caller_module": "string",
    "provider": "moex_iss | moex_fast | arena_go | polza_ai | broker_api | news_api | issuer_disclosure | macro_api | internal_cache",
    "request_type": "market_data | orderbook | trades | instruments | orders | portfolio | text_search | text_fetch | llm_completion | models | macro_series | submit_order | get_trades | get_positions | get_bots",
    "universe_id": "string",
    "instrument_ids": [
      "string"
    ],
    "payload": {},
    "cache_policy": {
      "use_cache": "boolean",
      "max_age_seconds": "integer",
      "write_cache": "boolean"
    },
    "timeout_ms": "integer",
    "retry_policy": {
      "max_retries": "integer",
      "backoff_ms": "integer"
    },
    "idempotency_key": "string"
  }
}
```

## 6. External requests

Это единственный модуль, которому разрешены внешние запросы. Все credentials берутся по `provider_credentials_ref`. Токены не передаются в аналитические модули.

## 7. Processing rules

- `validate_provider_access`
- `apply_rate_limit`
- `check_cache`
- `normalize_provider_request`
- `execute_request`
- `normalize_provider_response`
- `normalize_raw_store_payload`
- `write_raw_data`
- `write_request_log`
- `return_external_response`
- `attach_provider_tracking_id`

## 8. Output classification

- `external_response`
- `raw_market_data`
- `raw_candle`
- `raw_trade`
- `raw_orderbook`
- `raw_index_value`
- `raw_text_item`
- `raw_macro_data`
- `raw_macro_point`
- `broker_response`
- `request_log_record`

## 9. Output contract

```json
{
  "external_response": {
    "request_id": "string",
    "provider": "string",
    "status": "success | partial_success | failed | timeout | rate_limited",
    "data_ref": "string",
    "received_at": "string",
    "latency_ms": "integer",
    "cost_units": "number",
    "cache_hit": "boolean",
    "warnings": [
      "string"
    ],
    "errors": [
      "string"
    ],
    "provider_tracking_id": "string"
  }
}
```

## 10. Metrics / Records

- `provider_latency_ms`
- `provider_error_rate`
- `cache_hit_rate`
- `rate_limit_events`
- `request_cost_units`
- `timeout_count`
- `retry_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Provider Config Store` |
| `read/write` | `Request Cache Store` |
| `write` | `Request Log Store` |
| `write` | `Raw Market Data Store` |
| `write` | `Raw Text Store` |
| `write` | `Raw Macro Data Store` |

## 12. TTL and freshness

TTL зависит от `request_type`: market snapshots — секунды/минуты; text items — часы/дни; macro series — часы; instrument metadata — дни.

## 13. Failure policy

Возвращает `external_response.status` без выброса необработанного исключения. При `rate_limited` или `timeout` применяет retry policy и пишет request log.

## 14. Acceptance criteria

- `no_external_api_calls_outside_gateway`
- `all_requests_logged`
- `provider_responses_normalized`
- `cache_policy_respected`
- `secrets_not_exposed_to_callers`
- `tracking_id_preserved_when_available`
- `gateway_accepts_external_request_without_module_job`
- `market_text_macro_responses_written_to_documented_raw_tables`
- `unsupported_provider_request_type_rejected_before_transport`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `provider_latency_ms` | `response_received_at_ms - request_sent_at_ms` |
| `provider_error_rate` | `failed_external_requests / total_external_requests` by provider |
| `cache_hit_rate` | `cache_hit_requests / cache_eligible_requests` |
| `rate_limit_events` | count of provider responses mapped to `rate_limited` |
| `request_cost_units` | provider-reported cost if available, else internal estimate by request type |
| `timeout_count` | count of requests where elapsed time > `timeout_ms` |
| `retry_count` | count of retries for `request_id` |

## 16. Provider-specific rules

### Raw store persistence

Gateway writes provider responses only to documented raw stores and request logs:

| `request_type` | Raw table |
|---|---|
| `market_data` | `raw_market.raw_candle` or `raw_market.raw_index_value` when normalized OHLC/index fields are present |
| `trades` | `raw_market.raw_trade` |
| `orderbook` | `raw_market.raw_orderbook` |
| `text_search` / `text_fetch` | `raw_text.raw_text_item` |
| `llm_completion` | `raw_text.raw_text_item` only as raw provider output; analytical modules still must validate strict JSON before writing events/features |
| `macro_series` | `raw_macro.raw_macro_point` |
| `submit_order` / `get_trades` / `get_positions` / `get_bots` | `request_logs.external_response` and `request_logs.external_request_log`; portfolio/order modules own portfolio/order stores |

If provider payload cannot be mapped to the target raw table without inventing values, Gateway must preserve it in request logs and return `external_response.data_ref` pointing to `request_logs.external_response`.

### `moex_iss` / `moex_fast`

- `instruments`: normalized HTTP request to provider instrument metadata endpoint.
- `market_data`: normalized HTTP request to candle/security market data endpoint.
- `trades`: normalized HTTP request to trades endpoint.
- `orderbook`: normalized HTTP request to orderbook/depth endpoint.
- Gateway may use provider default paths from `Provider Config Store`; caller-provided `payload.path` is allowed only as transport routing metadata.
- `market_data`, `trades` and `orderbook` requests must be per instrument/per board. Gateway rejects MOEX market/trade/orderbook requests with zero or more than one `instrument_id`.
- Callers must pass explicit `payload.secid`, `payload.board_id`; `market_data` must also pass `payload.timeframe`, and `market_data`/`trades` must pass `time_range.from_ts/to_ts`; Gateway maps `1m/5m/10m/15m/1h/1d` to MOEX ISS `interval`.
- Raw market persistence must not fallback to the first request instrument. If provider `secid` conflicts with the single requested instrument, the row is skipped.
- Raw market persistence is idempotent by natural keys: candles use `provider + instrument_id + universe_id + board_id + timeframe + open_ts`; trades use `provider + instrument_id + universe_id + trade_ts + price + quantity + side`; orderbook uses `provider + instrument_id + universe_id + snapshot_ts`; index values use `provider + index_id + value_ts`.

### `news_api` / `issuer_disclosure`

- `text_search`: normalized HTTP search request; results are written to `raw_text.raw_text_item` when title/body/url fields are available.
- `text_fetch`: normalized HTTP fetch request; fetched document text is written to `raw_text.raw_text_item`.
- Concrete public sources are configured through `raw_text.text_source_config.query_template.endpoint`; provider names remain generic (`news_api`, `issuer_disclosure`) to preserve the gateway contract.
- RSS/Atom responses from public news feeds are normalized into `items` with `title`, `url`, `body`, `published_at` and `source_ref` before raw-store persistence.
- HTML public pages are normalized into title/body snippets when a source has no JSON/RSS response.
- Raw text persistence keeps source-specific metadata (`source`, `source_type`, `trust_level`, `confidence_score`, `instrument_candidates`, `issuer_candidates`, `quality_flags`) alongside provider payload.
- Ordinary news providers may create event candidates only; official disclosure/issuer/MOEX/CBR sources are required for confirmed corporate events.

### `macro_api`

- `macro_series`: normalized HTTP series request; points are written to `raw_macro.raw_macro_point` when `series_id`, timestamp and value are available.
- Public macro/news endpoints for CBR, MOEX ISS and optional public macro sources are configured in `Provider Config Store` and `Text Source Config`; analytical modules must still access them only through Gateway.
- Source-specific public parsers are supported for CBR XML dynamic FX rates, CBR public HTML tables, FRED/EIA CSV daily oil series and MOEX ISS JSON tables.
- Every persisted `macro_series` point must carry `provider`, `series_name`, `point_ts`, `value`, `unit`, `source_url`, `confidence_score` and `quality_flags`.
- CBR official rows are high-trust macro inputs; FRED/EIA public commodity rows are external public macro inputs; generic HTML table extraction must be flagged through `quality_flags` and never silently upgraded to an unflagged source.

### `arena_go`

- `submit_order`: `POST ${ARENA_GO_BASE_URL}/submit_order`.
- `get_trades`: `GET ${ARENA_GO_BASE_URL}/trades/{portfolio}`.
- `get_positions`: `GET ${ARENA_GO_BASE_URL}/positions/{portfolio}`.
- `get_bots`: `GET ${ARENA_GO_BASE_URL}/bots`.
- `Authorization` header value: `${SANDBOX_API_KEY}` first; `${ARENA_GO_TOKEN}` is local/dev fallback only.
- Token values must never be written to request/audit logs; only masked token source metadata is allowed.
- Portfolio path value must be the exact URL-encoded `bots[].name` returned by `get_bots`. `ARENA_GO_PORTFOLIO` and `ARENA_GO_BOT_NAME` may be empty at startup and are resolved from `/api/bots` when possible.
- Empty `positions`/`trades` responses are valid initial state when the bot exists and `cash_balance` is present.
- `submit_order.quantity` is shares, not lots; registry must keep `arena_go_quantity_mode=shares`.

### `polza_ai`

- `llm_completion`: `POST ${POLZA_BASE_URL}/chat/completions`.
- `models`: `GET ${POLZA_BASE_URL}/models`; if unavailable, healthcheck falls back to strict JSON `llm_completion`.
- `Authorization` header value: bearer-token auth using `${POLZA_API_KEY}`.
- task-specific `model`: `${POLZA_FAST_MODEL}` for light news/event classification and `${POLZA_REASONING_MODEL}` for reports/macro/reasoning; `${POLZA_DEFAULT_MODEL}` / `${POLZA_LLM_MODEL}` are fallbacks.
- LLM throttle env: `LLM_ENABLED`, `LLM_MAX_CALLS_PER_MINUTE`, `LLM_MAX_CALLS_PER_HOUR`, `LLM_MAX_CALLS_PER_DAY`, `LLM_MAX_ITEMS_PER_RUN`, `LLM_MIN_SECONDS_BETWEEN_CALLS`.
- required `response_format`: `{"type":"json_object"}` unless explicitly overridden by schema-based task.

## 17. Forbidden actions

- Запрещено интерпретировать рыночные данные как торговые сигналы.
- Запрещено менять payload семантически: gateway только нормализует transport/provider response.
- Запрещено скрывать provider errors.
- Запрещено хранить API keys в `Request Log Store`.
- Запрещено выполнять повторный `submit_order` без idempotency validation.
- Запрещено требовать отдельный `module_job` для штатной обработки `external_request`.

## 18. Updated input/output contracts for providers

### `arena_go_submit_order_payload`

```json
{
  "direction": "B | S",
  "secid": "string",
  "quantity": 0,
  "bot": "string"
}
```

### `polza_chat_completion_payload`

```json
{
  "model": "string",
  "messages": [
    {"role": "system | user | assistant", "content": "string"}
  ],
  "temperature": 0,
  "response_format": {"type": "json_object"},
  "reasoning": {
    "enabled": true,
    "effort": "low | medium | high",
    "summary": "auto"
  }
}
```
