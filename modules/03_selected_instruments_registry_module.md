# Selected Instruments Registry Module — Реестр выбранных инструментов

## 1. Назначение

Хранит и валидирует ручную вселенную инструментов. Ограничивает систему до выбранных активных акций и даёт всем модулям единый mapping: ticker, FIGI, ISIN, board, lot, aliases, sector.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Selected Instruments Registry Module` |
| `module_type` | `service/storage` |
| `primary_contour` | `service_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `metadata_service` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `module_name`
- `universe_id`
- `run_mode`
- `idempotency_key`

### Trigger policy

Обновляется вручную, по расписанию metadata refresh или при изменении списка инструментов.

## 4. Input classification

- `instrument_profile_update`
- `instrument_metadata_request`
- `provider_instrument_metadata`
- `manual_universe_config`

## 5. Input contract

```json
{
  "universe_id": "string",
  "instrument_updates": [
    {
      "instrument_id": "string",
      "ticker": "string",
      "figi": "string",
      "isin": "string",
      "class_code": "string",
      "board_id": "string",
      "lot_size": "integer",
      "min_price_increment": "number",
      "currency": "string",
      "sector": "string",
      "issuer_name": "string",
      "aliases": [
        "string"
      ],
      "related_entities": [
        "string"
      ],
      "is_active": "boolean",
      "tradable": "boolean",
      "allowed_horizons": [
        "intraday",
        "swing",
        "position"
      ]
    }
  ]
}
```

## 6. External requests

Для metadata refresh создаёт `external_request` с `request_type=instruments` через `External Request Gateway Module`.

## 7. Processing rules

- `validate_unique_instrument_id`
- `validate_active_count_max_20`
- `map_provider_ids`
- `validate_lot_size`
- `validate_board_id`
- `normalize_aliases`
- `detect_duplicate_aliases`
- `write_instrument_profile`
- `publish_universe_snapshot`

## 8. Output classification

- `instrument_profile`
- `instrument_mapping`
- `universe_snapshot`

## 9. Output contract

```json
{
  "universe_snapshot_id": "string",
  "universe_id": "string",
  "active_instrument_ids": [
    "string"
  ],
  "instrument_profiles_ref": "string",
  "created_at": "string",
  "validation_status": "valid | invalid",
  "errors": [
    "string"
  ]
}
```

## 10. Metrics / Records

- `active_instrument_count`
- `metadata_completeness_score`
- `mapping_conflict_count`
- `inactive_instrument_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read/write` | `Selected Instruments DB` |
| `read` | `Raw Market Data Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

`instrument_profile` живёт до ручного изменения или metadata refresh. `tradable` и trading status должны обновляться чаще через market/session data.

## 13. Failure policy

Если mapping неполный, инструмент получает `tradable=false` и не допускается до decision/execution.

## 14. Acceptance criteria

- `active_instrument_count_lte_20`
- `all_active_instruments_have_required_ids`
- `aliases_normalized`
- `inactive_instruments_excluded_from_feature_generation`
- `universe_snapshot_versioned`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `active_instrument_count` | count of `instrument_profile` where `is_active=true` |
| `metadata_completeness_score` | `filled_required_fields / total_required_fields` averaged across active instruments |
| `mapping_conflict_count` | count of duplicate/conflicting `ticker`, `isin`, `figi`, `aliases` mappings |
| `inactive_instrument_count` | count of `instrument_profile` where `is_active=false` |

## 16. Required fields extension

`instrument_profile` must include:

```json
{
  "arena_go_secid": "string",
  "arena_go_quantity_mode": "shares | lots",
  "max_trade_quantity": 0,
  "execution_enabled": true
}
```

ArenaGo sandbox automatic-live universe is fixed at 20 tickers: `LKOH`, `SBER`, `ROSN`, `GAZP`, `VTBR`, `YDEX`, `PLZL`, `T`, `NVTK`, `X5`, `GMKN`, `MGNT`, `ALRS`, `AFLT`, `CHMF`, `NLMK`, `MOEX`, `SNGSP`, `MTSS`, `PIKK`.

All allowed equities must use `board_id=TQBR`, `currency=RUB`, `tradable=true`, `execution_enabled=true`, `arena_go_quantity_mode=shares`, and a non-empty `arena_go_secid`. Missing `issuer_ir_url` is a registry gap reported by `audit.registry_reconciliation_report`; it must not crash public disclosure intake and should produce a controlled `source_missing_endpoint` skip for corporate-site discovery.

## 17. Forbidden actions

- Запрещено автоматически добавлять инструменты в universe без ручного approval.
- Запрещено превышать limit `20` active instruments.
- Запрещено разрешать execution по инструменту без `arena_go_secid`.
- Запрещено менять `is_active` на основании LLM-output без human/governance approval.
