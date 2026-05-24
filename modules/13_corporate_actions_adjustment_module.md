# Corporate Actions Adjustment Module — Корпоративные действия и корректировки

## 1. Назначение

Обрабатывает дивиденды, splits, consolidations, ticker changes, redenomination, additional issues, buybacks, delisting/halt events и готовит корректировки historical price series.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Corporate Actions Adjustment Module` |
| `module_type` | `analytical` |
| `primary_contour` | `event_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `rules + data_adjustment` |
| `llm_usage` | `optional_for_text_extraction` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `instrument_ids`
- `input_refs`
- `time_range`
- `run_mode`

### Trigger policy

Запускается при corporate action event и daily после обновления справочников.

## 4. Input classification

- `structured_event_corporate_action`
- `instrument_profile`
- `raw_candle`
- `corporate_action_source`

## 5. Input contract

```json
{
  "corporate_actions_input": {
    "instrument_ids": [
      "string"
    ],
    "corporate_action_refs": [
      "string"
    ],
    "price_series_ref": "string",
    "instrument_profile_ref": "string",
    "adjustment_policy": "total_return | price_only | custom"
  }
}
```

## 6. External requests

Через Gateway запрашивает issuer disclosures, MOEX instrument metadata, trading status, historical data.

MOEX ISS corporate-action market requests:

- Historical candles and trading-status support requests must be generated per instrument profile.
- Each MOEX `market_data` request must carry one `instrument_id`, `payload.secid`, `payload.board_id`, `payload.timeframe=1d` and `time_range`.
- Instrument metadata requests may also be per instrument, but must not be used to write market rows for another instrument.

## 7. Processing rules

- `classify_corporate_action`
- `validate_effective_date`
- `compute_adjustment_factor`
- `update_instrument_mapping`
- `build_adjusted_price_series_ref`
- `write_corporate_action_record`
- `emit_downstream_recompute_trigger`

## 8. Output classification

- `corporate_action_record`
- `adjustment_factor`
- `adjusted_price_series_ref`
- `instrument_mapping_update`
- `recompute_trigger`

## 9. Output contract

```json
{
  "corporate_action_record": {
    "corporate_action_id": "string",
    "instrument_id": "string",
    "action_type": "dividend | split | consolidation | ticker_change | additional_issue | buyback | delisting | halt | other",
    "effective_date": "string",
    "adjustment_factor": "number | null",
    "source_refs": [
      "string"
    ],
    "confidence_score": "number",
    "requires_recompute": "boolean"
  }
}
```

## 10. Metrics / Records

- `corporate_action_pressure_score`
- `adjustment_factor`
- `halt_flag`
- `tradability_change_flag`
- `buyback_intensity`
- `free_float_change`
- `additional_supply_risk_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Event Store` |
| `read/write` | `Selected Instruments DB` |
| `read/write` | `Raw Market Data Store` |
| `write` | `Corporate Actions Store` |
| `write` | `Feature Store` |

## 12. TTL and freshness

Corporate action records постоянные. Adjustment factors версионируются и требуют пересчёта affected features.

## 13. Failure policy

Если corporate action подтверждён, но adjustment factor не рассчитан, модуль блокирует affected historical-return features до разрешения.

Модуль обрабатывает только подтверждённые corporate/dividend events. `events.structured_event.payload.confirmation_status = candidate_requires_official_confirmation` означает ранний новостной сигнал и не является основанием для corporate action adjustment до подтверждения через `issuer_disclosure`, `prime_disclosure`, `akm_disclosure`, `corporate_site`, MOEX/CBR или другой официальный слой.

## 14. Acceptance criteria

- `effective_date_required`
- `adjustment_policy_explicit`
- `affected_features_recompute_triggered`
- `instrument_mapping_versioned`
- `halt_and_tradability_flags_propagated`
- `candidate_news_events_are_not_adjusted`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `corporate_action_pressure_score` | `WAvg([buyback_intensity, -additional_supply_risk_score, tradability_change_impact], active_weights)` |
| `adjustment_factor` | provider/exchange adjustment factor for split/dividend/corporate action; if internal: `adjusted_price / raw_price` |
| `halt_flag` | `1` if trading halt detected for instrument, else `0` |
| `tradability_change_flag` | `1` if instrument tradability changed, else `0` |
| `buyback_intensity` | `buyback_value_period / free_float_market_cap` |
| `free_float_change` | `free_float_current - free_float_previous` |
| `additional_supply_risk_score` | scaled expected dilution: `new_shares_expected / current_shares_outstanding` |

## 16. Forbidden actions

- Запрещено корректировать historical prices без сохранения raw values.
- Запрещено менять `instrument_profile.tradable` без source and audit record.
- Запрещено отправлять заявки.
- Запрещено делать LLM extraction напрямую, минуя Event modules/Gateway.
