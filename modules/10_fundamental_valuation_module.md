# Fundamental & Valuation Module — Фундаментал и оценка

## 1. Назначение

Считает фундаментальные и оценочные признаки по последней доступной отчётности, мультипликаторам, долгу, profitability, margins и sector-relative valuation.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Fundamental & Valuation Module` |
| `module_type` | `analytical` |
| `primary_contour` | `daily_contour` |
| `secondary_contours` | `event_contour` |
| `execution_mode` | `algorithmic + parser_assisted` |
| `llm_usage` | `only_for_extracting_tables_or_text_fields_when_needed` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `instrument_ids`
- `time_range`
- `horizons`
- `run_mode`

### Trigger policy

Запускается после выхода отчётности, после обновления financial data или daily refresh.

## 4. Input classification

- `financial_statement`
- `market_cap_data`
- `sector_peer_data`
- `structured_event_earnings`
- `instrument_profile`

## 5. Input contract

```json
{
  "fundamental_input": {
    "instrument_ids": [
      "string"
    ],
    "financial_statement_refs": [
      "string"
    ],
    "market_cap_ref": "string",
    "peer_group_ref": "string",
    "reporting_standard": "ifrs | ras | mixed | unknown",
    "period": "string"
  }
}
```

## 6. External requests

Запрашивает issuer disclosures, financial data и market cap через Gateway. LLM допускается только для извлечения чисел/полей из текстов с evidence.

## 7. Processing rules

- `parse_financial_fields`
- `validate_statement_period`
- `compute_valuation_multiples`
- `compute_profitability_metrics`
- `compute_leverage_metrics`
- `compute_growth_metrics`
- `compute_margin_dynamics`
- `compute_sector_relative_values`
- `write_feature_records`

## 8. Output classification

- `feature_record`
- `fundamental_snapshot`

## 9. Output contract

```json
{
  "fundamental_snapshot": {
    "fundamental_snapshot_id": "string",
    "instrument_id": "string",
    "period": "string",
    "reporting_standard": "ifrs | ras | mixed | unknown",
    "source_refs": [
      "string"
    ],
    "features_ref": "string",
    "confidence_score": "number",
    "calculation_version": "string"
  }
}
```

## 10. Metrics / Records

- `pe_relative_to_history`
- `ev_ebitda_relative_to_sector`
- `pb_relative`
- `ps_relative`
- `earnings_yield`
- `fcf_yield`
- `roe`
- `roic`
- `net_debt_ebitda`
- `interest_coverage`
- `revenue_growth_yoy`
- `ebitda_growth_yoy`
- `net_income_growth_yoy`
- `margin_change`
- `fundamental_quality_score`
- `valuation_attractiveness_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Text Store` |
| `read` | `Event Store` |
| `read` | `Raw Market Data Store` |
| `write` | `Feature Store` |
| `write` | `Fundamental Snapshot Store` |

## 12. TTL and freshness

Fundamental features живут до следующей отчётности или material financial event. Daily refresh обновляет market-dependent ratios.

## 13. Failure policy

Если financial fields неполные, модуль пишет частичный snapshot с `low_fundamental_coverage` и не заполняет отсутствующие ratios синтетическими значениями.

## 14. Acceptance criteria

- `financial_period_explicit`
- `reporting_standard_explicit`
- `no_synthetic_zero_for_missing_fields`
- `sector_relative_metrics_have_peer_group`
- `source_refs_required`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `pe_relative_to_history` | `(PE_current - median(PE_history)) / std(PE_history)` |
| `ev_ebitda_relative_to_sector` | `EV_EBITDA_current - median(EV_EBITDA_sector)` or sector percentile |
| `pb_relative` | `(PB_current - median(PB_history)) / std(PB_history)` |
| `ps_relative` | `(PS_current - median(PS_history)) / std(PS_history)` |
| `earnings_yield` | `net_income_ttm / market_cap` |
| `fcf_yield` | `free_cash_flow_ttm / market_cap` |
| `roe` | `net_income_ttm / average_equity` |
| `roic` | `NOPAT / invested_capital` |
| `net_debt_ebitda` | `(debt - cash_and_equivalents) / EBITDA_ttm` |
| `interest_coverage` | `EBIT / interest_expense` |
| `revenue_growth_yoy` | `revenue_period / revenue_same_period_prev_year - 1` |
| `ebitda_growth_yoy` | `EBITDA_period / EBITDA_same_period_prev_year - 1` |
| `net_income_growth_yoy` | `net_income_period / net_income_same_period_prev_year - 1` |
| `margin_change` | `current_margin - margin_same_period_prev_year` |
| `fundamental_quality_score` | `WAvg([roe_z, roic_z, -net_debt_ebitda_z, interest_coverage_z, margin_change_z], active_weights)` |
| `valuation_attractiveness_score` | `WAvg([-pe_relative_to_history, -ev_ebitda_relative_to_sector, earnings_yield_z, fcf_yield_z], active_weights)` |

## 16. Forbidden actions

- Запрещено обновлять фундаментальные данные без source reference.
- Запрещено смешивать IFRS/RAS без `accounting_standard`.
- Запрещено считать valuation score при stale market_cap без quality flag.
- Запрещено принимать решение или отправлять заявки.
