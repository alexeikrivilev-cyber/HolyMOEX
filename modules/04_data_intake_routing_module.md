# Data Intake & Routing Module — Приём и маршрутизация текстовых данных

## 1. Назначение

Собирает текстовые и событийные данные только по инструментам из `Selected Instruments DB`, дедуплицирует, сопоставляет с инструментами, классифицирует источник и маршрутизирует данные в аналитические модули.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Data Intake & Routing Module` |
| `module_type` | `service` |
| `primary_contour` | `intraday_contour` |
| `secondary_contours` | `event_contour, daily_contour` |
| `execution_mode` | `batch + event_driven` |
| `llm_usage` | `optional_light_classification_only` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `universe_id`
- `instrument_ids`
- `time_range`
- `trigger_type`
- `run_mode`

### Trigger policy

Запускается по расписанию для `scheduled_external_news_discovery` и немедленно при event trigger. Scheduled discovery обязана проходить по каждому `instrument_id` из активной вселенной `Selected Instruments DB` с `is_active=true`.

Для каждого активного инструмента модуль строит поисковые запросы по `ticker`, `issuer_name`, `aliases` и `related_entities`, затем создаёт `external_request` в `External Request Gateway Module` для разрешённых источников из `text_source_config`. Базовый рабочий контур источников не является ручным: он сидится миграцией `008_public_data_source_contour.sql`.

Все внешние text requests выполняются через `External Request Gateway Module`.

### Public source contour

`text_source_config` обязан хранить конкретный источник, а не абстрактное намерение "найти новости". Логический provider остаётся одним из контрактных `news_api`, `issuer_disclosure`, `macro_api`, но конкретный URL/RSS/search endpoint задаётся в `query_template.endpoint`.

Обязательный open/public contour:

| Layer | `source_type` | Provider | Trust rule |
|---|---|---|---|
| Fast news | `rbc_news`, `tass_news`, `interfax_news`, `prime_news`, `finam_news` | `news_api` | early signal, not confirmed corporate event |
| Weak/fast news | `smartlab_news` | `news_api` | weak/medium signal, never direct trade trigger |
| Official disclosure | `issuer_disclosure`, `prime_disclosure`, `akm_disclosure` | `issuer_disclosure` | high-trust confirmation layer |
| Issuer sites | `corporate_site` | `issuer_disclosure` | high-trust only when `instrument_profile.metadata.issuer_ir_url` exists |
| Macro/regulatory | `macro_text`, `regulatory_text`, `cbr_macro`, `moex_macro` | `macro_api` | official macro/market context |
| Optional macro | `fred_eia_macro`, `rosstat_macro` | `macro_api` | optional external/public macro |

RSS/news feeds that cannot search per ticker must use `source_policy.fetch_scope = "market_wide_once"`: the module fetches the feed once per run, then maps items to instruments internally by aliases. Sources with `source_policy.requires_endpoint = true` are skipped with `source_missing_endpoint` until the selected instrument metadata contains the required endpoint.

Every stored `raw_text_item` must carry normalized product-intake metadata:

- `source` / `source_type`
- `source_url`
- `published_at`
- `fetched_at`
- `title`
- `body` or snippet
- `language`
- `trust_level`
- `confidence_score`
- `instrument_candidates`
- `issuer_candidates`
- `quality_flags`

Fast news sources are saved as `candidate_early_signal`. Official disclosure, CBR/MOEX regulatory text, and issuer corporate sites are the confirmation layer. A fast-news item about dividend, earnings, or corporate action must receive `requires_official_confirmation` until an official source confirms it.

## 4. Input classification

- `instrument_profile`
- `instrument_alias`
- `text_source_config`
- `scheduled_external_news_discovery`
- `raw_text_item`
- `external_response`

## 5. Input contract

```json
{
  "intake_request": {
    "universe_id": "string",
    "instrument_ids": [
      "string"
    ],
    "source_types": [
      "rbc_news",
      "tass_news",
      "interfax_news",
      "prime_news",
      "finam_news",
      "smartlab_news",
      "issuer_disclosure",
      "prime_disclosure",
      "akm_disclosure",
      "corporate_site",
      "regulatory_text",
      "macro_text",
      "cbr_macro",
      "moex_macro"
    ],
    "discovery_mode": "scheduled | event_driven | replay",
    "per_instrument_discovery": true,
    "time_range": {
      "from_ts": "string",
      "to_ts": "string"
    },
    "routing_targets": [
      "Event & News Intelligence Module",
      "Earnings & Dividend Intelligence Module",
      "Corporate Actions Adjustment Module",
      "Market Context Module"
    ]
  }
}
```

## 6. External requests

Создаёт `external_request` с `request_type=text_search` или `text_fetch`. Для scheduled discovery создаёт минимум один `text_search` request на каждый активный `instrument_id` и каждый разрешённый per-instrument source type, если source не отключён политикой. Для `market_wide_once` sources создаёт один request на source type за run и дальше сопоставляет новости с инструментами локально. Не обращается к источникам напрямую.

## 7. Processing rules

- `load_active_instruments`
- `build_alias_queries`
- `schedule_per_instrument_news_discovery`
- `create_text_search_external_requests`
- `fetch_text_via_gateway`
- `deduplicate_text`
- `detect_language`
- `map_text_to_instrument_ids`
- `score_source_credibility`
- `classify_text_category`
- `write_raw_text_item`
- `create_routing_message`

## 8. Output classification

- `raw_text_item`
- `external_text_search_request`
- `routing_message`
- `text_dedup_record`
- `source_credibility_record`

## 9. Output contract

```json
{
  "routing_message": {
    "routing_message_id": "string",
    "raw_text_ref": "string",
    "discovery_mode": "scheduled | event_driven | replay",
    "instrument_ids": [
      "string"
    ],
    "text_category": "news | earnings | dividend | corporate_action | macro | regulation | other",
    "source_type": "string",
    "source_credibility_score": "number",
    "relevance_score": "number",
    "target_modules": [
      "string"
    ],
    "created_at": "string"
  }
}
```

## 10. Metrics / Records

- `text_items_fetched`
- `scheduled_discovery_coverage_ratio`
- `duplicate_ratio`
- `text_search_requests_created`
- `instrument_mapping_confidence`
- `source_credibility_score`
- `relevance_score`
- `routing_latency_ms`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Selected Instruments DB` |
| `read/write` | `Raw Text Store` |
| `write` | `Event Routing Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

`routing_message` актуален до обработки целевым модулем или истечения `routing_ttl_seconds`. `raw_text_item` хранится долгосрочно для audit/research.

## 13. Failure policy

Если текст не сопоставлен с активным инструментом, он сохраняется как raw item, но не маршрутизируется в trading features.

## 14. Acceptance criteria

- `only_active_universe_processed`
- `scheduled_discovery_runs_for_each_active_instrument`
- `scheduled_discovery_uses_alias_issuer_and_related_entity_queries`
- `all_external_text_requests_via_gateway`
- `public_sources_configured_as_text_source_config`
- `rss_feeds_fetched_market_wide_once`
- `duplicates_detected`
- `routing_targets_explicit`
- `raw_text_items_have_source_ref`
- `raw_text_items_have_trust_candidates_confidence_and_quality_flags`
- `ordinary_news_is_candidate_until_official_confirmation`
- `no_trading_features_written_by_intake_module`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `text_items_fetched` | count of new `raw_text_item` received for `module_job` |
| `scheduled_discovery_coverage_ratio` | `active_instruments_with_discovery_request / active_instruments_total` for scheduled run |
| `text_search_requests_created` | count of `external_request` records with `request_type=text_search` created by discovery run |
| `duplicate_ratio` | `duplicate_text_items / fetched_text_items` using hash + near-duplicate similarity |
| `instrument_mapping_confidence` | max score from alias/ticker/issuer/entity matching, scaled `0..1` |
| `source_credibility_score` | configured source score from `source_registry`, scaled `0..1` |
| `relevance_score` | weighted score: `0.5*entity_match + 0.3*topic_match + 0.2*source_credibility` |
| `routing_latency_ms` | `routing_finished_at_ms - raw_text_received_at_ms` |

## 16. Forbidden actions

- Запрещено считать sentiment, materiality или trading signal.
- Запрещено писать trading features напрямую в `Feature Store`.
- Запрещено обрабатывать тексты вне `Selected Instruments DB`, кроме macro/market-wide routing.
- Запрещено напрямую вызывать news/API/LLM provider, минуя Gateway.
- Запрещено пропускать активный `instrument_id` в scheduled discovery без явного `source_disabled` или `instrument_not_eligible` audit reason.
- Запрещено удалять raw text; допускается только пометка duplicate/irrelevant.
