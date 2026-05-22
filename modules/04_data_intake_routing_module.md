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

Для каждого активного инструмента модуль строит поисковые запросы по `ticker`, `issuer_name`, `aliases` и `related_entities`, затем создаёт `external_request` в `External Request Gateway Module` для источников `news_api`, `issuer_disclosure`, `regulatory_text`, `macro_text` и `corporate_site`, если они разрешены `text_source_config`.

Все внешние text requests выполняются через `External Request Gateway Module`.

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
      "news_api",
      "issuer_disclosure",
      "macro_text",
      "regulatory_text",
      "corporate_site"
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

Создаёт `external_request` с `request_type=text_search` или `text_fetch`. Для scheduled discovery создаёт минимум один `text_search` request на каждый активный `instrument_id` и каждый разрешённый source type, если source не отключён политикой. Не обращается к источникам напрямую.

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
- `duplicates_detected`
- `routing_targets_explicit`
- `raw_text_items_have_source_ref`
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
