# Earnings & Dividend Intelligence Module — Отчётность и дивиденды

## 1. Назначение

Обрабатывает отчётность, финансовые сюрпризы, дивидендные рекомендации, отсечки и payout sustainability. Разделяет извлечение фактов и расчёт метрик.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Earnings & Dividend Intelligence Module` |
| `module_type` | `hybrid_llm` |
| `primary_contour` | `event_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `llm_extraction + algorithmic_calculation` |
| `llm_usage` | `required_for_unstructured_reports_optional_for_structured_data` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `instrument_ids`
- `input_refs`
- `time_range`
- `horizons`
- `run_mode`

### Trigger policy

Запускается при report/dividend event, после corporate disclosure или daily refresh дивидендного календаря.

## 4. Input classification

- `raw_text_item_report`
- `structured_event_earnings`
- `structured_event_dividend`
- `financial_statement`
- `dividend_calendar`
- `historical_gap_data`

## 5. Input contract

```json
{
  "earnings_dividend_input": {
    "instrument_ids": [
      "string"
    ],
    "report_refs": [
      "string"
    ],
    "dividend_event_refs": [
      "string"
    ],
    "financial_expectation_ref": "string",
    "historical_gap_ref": "string",
    "llm_prompt_version": "string"
  }
}
```

## 6. External requests

Через Gateway запрашивает issuer disclosures, reports, dividend data, market data for gap history, LLM extraction.

## 7. Processing rules

- `extract_financial_facts`
- `validate_report_period`
- `compare_to_expectations`
- `compute_earnings_surprises`
- `extract_dividend_terms`
- `compute_expected_dividend_yield`
- `compute_dividend_probability`
- `compute_gap_history`
- `compute_dividend_sustainability`
- `write_events_and_features`

## 8. Output classification

- `structured_event`
- `feature_record`
- `earnings_snapshot`
- `dividend_snapshot`

## 9. Output contract

```json
{
  "earnings_snapshot": {
    "earnings_snapshot_id": "string",
    "instrument_id": "string",
    "period": "string",
    "revenue_surprise": "number | null",
    "ebitda_surprise": "number | null",
    "net_income_surprise": "number | null",
    "confidence_score": "number",
    "source_refs": [
      "string"
    ]
  },
  "dividend_snapshot": {
    "dividend_snapshot_id": "string",
    "instrument_id": "string",
    "expected_dividend_yield": "number | null",
    "dividend_probability": "number",
    "days_to_record_date": "integer | null",
    "confidence_score": "number",
    "source_refs": [
      "string"
    ]
  }
}
```

## 10. Metrics / Records

- `revenue_surprise`
- `ebitda_surprise`
- `net_income_surprise`
- `fcf_surprise`
- `margin_surprise`
- `debt_surprise`
- `capex_surprise`
- `earnings_quality_score`
- `report_materiality_score`
- `report_sentiment_score`
- `expected_dividend_yield`
- `dividend_probability`
- `dividend_surprise`
- `days_to_record_date`
- `historical_gap_size`
- `expected_gap_risk`
- `gap_close_probability_20d`
- `gap_close_speed_median`
- `payout_ratio`
- `dividend_sustainability_score`
- `dividend_carry_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Text Store` |
| `read` | `Event Store` |
| `read` | `Raw Market Data Store` |
| `write` | `Event Store` |
| `write` | `Feature Store` |
| `write` | `Earnings Dividend Store` |

## 12. TTL and freshness

Earnings features живут до следующего отчётного события или material revision. Dividend features живут до отсечки, отмены/изменения рекомендации или следующего dividend event.

## 13. Failure policy

Если нет expectation baseline, surprise metrics пишутся как `null` с `missing_expectation_baseline`, а не как 0.

## 14. Acceptance criteria

- `facts_and_scores_separated`
- `surprise_requires_baseline`
- `dividend_dates_explicit`
- `historical_gap_uses_adjusted_prices`
- `source_refs_required`
- `llm_evidence_required`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `revenue_surprise` | `(revenue_actual - revenue_expected) / abs(revenue_expected)` |
| `ebitda_surprise` | `(ebitda_actual - ebitda_expected) / abs(ebitda_expected)` |
| `net_income_surprise` | `(net_income_actual - net_income_expected) / abs(net_income_expected)` |
| `fcf_surprise` | `(fcf_actual - fcf_expected) / abs(fcf_expected)` |
| `margin_surprise` | `margin_actual - margin_expected` |
| `debt_surprise` | `(net_debt_actual - net_debt_expected) / abs(net_debt_expected)` |
| `capex_surprise` | `(capex_actual - capex_expected) / abs(capex_expected)` |
| `earnings_quality_score` | `WAvg([fcf_conversion_z, margin_quality_z, -one_off_items_ratio_z, accruals_quality_z], active_weights)` |
| `report_materiality_score` | `WAvg([abs(revenue_surprise), abs(ebitda_surprise), abs(net_income_surprise), guidance_change_score], active_weights)` |
| `report_sentiment_score` | LLM/classifier score `[-1,1]` from report text with evidence |
| `expected_dividend_yield` | `expected_dividend_per_share / P_t` |
| `dividend_probability` | model/rule probability `0..1` based on policy, earnings, FCF, management statements |
| `dividend_surprise` | `(announced_dividend - expected_dividend) / abs(expected_dividend)` |
| `days_to_record_date` | calendar days from `as_of_date` to `record_date` |
| `historical_gap_size` | median `abs(open_after_record_date / close_before_record_date - 1)` over history |
| `expected_gap_risk` | `expected_dividend_yield * (1 - gap_close_probability_20d)` adjusted by volatility |
| `gap_close_probability_20d` | historical share of dividend gaps closed within 20 trading days for instrument/sector |
| `gap_close_speed_median` | median trading days to close dividend gap historically |
| `payout_ratio` | `dividends_total / net_income` or `dividends_total / fcf` depending policy |
| `dividend_sustainability_score` | `WAvg([payout_safety_z, fcf_coverage_z, -leverage_z, dividend_policy_confidence], active_weights)` |
| `dividend_carry_score` | `expected_dividend_yield * dividend_probability - expected_gap_risk - expected_transaction_cost` |

## 16. Forbidden actions

- Запрещено принимать announced dividend без source reference.
- Запрещено использовать expected values без указания source/model version.
- Запрещено смешивать consensus и internal estimates без поля `estimate_source`.
- Запрещено отправлять заявки или менять portfolio state.
