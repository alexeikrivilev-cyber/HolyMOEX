# Derivatives & Positioning Module — Деривативы и позиционирование

## 1. Назначение

Считает признаки по фьючерсам, опционам и открытым позициям там, где данные ликвидны и доступны. Модуль опционален по инструменту.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Derivatives & Positioning Module` |
| `module_type` | `analytical` |
| `primary_contour` | `intraday_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `algorithmic_optional` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `instrument_ids`
- `time_range`
- `horizons`
- `run_mode`

### Trigger policy

Запускается только для инструментов с `derivatives_enabled=true` или для index-level context.

## 4. Input classification

- `raw_futures_data`
- `raw_options_data`
- `open_interest_data`
- `spot_market_data`
- `instrument_profile`

## 5. Input contract

```json
{
  "derivatives_input": {
    "instrument_ids": [
      "string"
    ],
    "futures_refs": [
      "string"
    ],
    "options_refs": [
      "string"
    ],
    "spot_refs": [
      "string"
    ],
    "liquidity_thresholds": {
      "min_turnover": "number",
      "min_open_interest": "number"
    }
  }
}
```

## 6. External requests

Через Gateway запрашивает derivatives market data, open interest и spot reference data.

## 7. Processing rules

- `check_derivatives_liquidity`
- `compute_futures_basis`
- `compute_basis_change`
- `compute_open_interest_change`
- `compute_volume_oi_ratio`
- `compute_implied_volatility_if_available`
- `compute_iv_rv_spread`
- `compute_options_skew`
- `write_features_or_skip`

## 8. Output classification

- `feature_record`
- `derivatives_availability_record`

## 9. Output contract

```json
{
  "derivatives_availability_record": {
    "instrument_id": "string",
    "derivatives_enabled": "boolean",
    "skip_reason": "no_data | low_liquidity | unsupported | none",
    "as_of_ts": "string"
  },
  "feature_records": [
    "feature_record"
  ]
}
```

## 10. Metrics / Records

- `futures_basis`
- `basis_change`
- `open_interest_change`
- `volume_oi_ratio`
- `implied_volatility`
- `iv_rv_spread`
- `put_call_ratio`
- `options_skew`
- `derivatives_pressure_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Market Data Store` |
| `read` | `Selected Instruments DB` |
| `write` | `Feature Store` |
| `write` | `Derivatives Availability Store` |

## 12. TTL and freshness

Intraday derivatives metrics живут минуты/часы. Daily OI metrics живут до следующего clearing/update.

## 13. Failure policy

Если ликвидность ниже threshold, модуль возвращает `skipped`, а не генерирует слабые псевдометрики.

## 14. Acceptance criteria

- `module_can_skip_per_instrument`
- `liquidity_thresholds_enforced`
- `no_synthetic_derivatives_features`
- `spot_and_derivative_timestamps_aligned`
- `availability_record_written`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `futures_basis` | `(futures_price - spot_price) / spot_price` |
| `basis_change` | `futures_basis_t - futures_basis_{t-1}` |
| `open_interest_change` | `open_interest_t / open_interest_{t-1} - 1` |
| `volume_oi_ratio` | `derivative_volume / open_interest` |
| `implied_volatility` | provider IV or solved IV from option price using configured model |
| `iv_rv_spread` | `implied_volatility - realized_volatility` |
| `put_call_ratio` | `put_volume / call_volume` or `put_oi / call_oi` by config |
| `options_skew` | `IV_put_25delta - IV_call_25delta` or nearest available proxy |
| `derivatives_pressure_score` | `WAvg([basis_change_z, open_interest_change_z, volume_oi_ratio_z, put_call_ratio_z, options_skew_z], active_weights)` |

## 16. Forbidden actions

- Запрещено писать derivative metrics when derivative liquidity below threshold.
- Запрещено использовать stale option chain.
- Запрещено подставлять нули вместо unavailable derivatives data.
- Запрещено отправлять заявки или принимать решения.
