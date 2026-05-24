# Volatility & Risk Metrics Module — Волатильность и рыночный риск

## 1. Назначение

Считает реализованную волатильность, downside risk, jump/gap risk, beta, correlations и drawdown/recovery features.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Volatility & Risk Metrics Module` |
| `module_type` | `analytical` |
| `primary_contour` | `realtime_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `algorithmic` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `instrument_ids`
- `horizons`
- `time_range`
- `run_mode`

### Trigger policy

Intraday-часть запускается при обновлении candles. Daily risk metrics пересчитываются после закрытия рынка.

## 4. Input classification

- `raw_candle`
- `raw_index_value`
- `raw_macro_point`
- `feature_record_price`

## 5. Input contract

```json
{
  "risk_metrics_input": {
    "instrument_ids": [
      "string"
    ],
    "candles_ref": "string",
    "market_index_ref": "string",
    "sector_index_ref": "string",
    "macro_refs": [
      "string"
    ],
    "windows": [
      5,
      20,
      60,
      120
    ],
    "horizons": [
      "intraday",
      "swing",
      "position"
    ]
  }
}
```

## 6. External requests

Запрашивает market/index/macro series через Gateway только при отсутствии свежих raw data.

MOEX ISS raw-market requests:

- Instrument candles must be requested separately for every `instrument_id` and timeframe.
- Every request must include exactly one `instrument_id`, explicit `payload.secid`, `payload.board_id`, `payload.timeframe` and `time_range`.
- Market and sector index candles must be requested as separate single-secid index requests.
- The module must not create a single MOEX `market_data` request for the whole `instrument_ids` list.

## 7. Processing rules

- `compute_realized_volatility`
- `compute_intraday_range`
- `compute_downside_volatility`
- `compute_jump_risk`
- `compute_gap_risk`
- `compute_max_drawdown`
- `compute_recovery_ratio`
- `compute_beta_to_market`
- `compute_rolling_correlations`
- `write_feature_records`

## 8. Output classification

- `feature_record`
- `risk_context_record`

## 9. Output contract

```json
{
  "feature_records": [
    {
      "metric_group": "volatility",
      "metric_type": "raw_metric | derived_metric | composite_score",
      "metric_name": "string",
      "raw_value": "number",
      "horizon": "intraday | swing | position",
      "ttl_seconds": "integer",
      "confidence_score": "number"
    }
  ]
}
```

## 10. Metrics / Records

- `realized_vol_5d`
- `realized_vol_20d`
- `realized_vol_60d`
- `volatility_percentile`
- `intraday_range_percentile`
- `atr_14`
- `downside_volatility`
- `jump_risk_score`
- `gap_risk_score`
- `max_drawdown_60d`
- `recovery_ratio`
- `beta_to_market`
- `beta_stability`
- `correlation_to_sector`
- `correlation_to_currency`
- `correlation_to_oil`
- `correlation_to_rates`
- `systematic_risk_share`
- `idiosyncratic_risk_share`
- `volatility_risk_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Market Data Store` |
| `read` | `Raw Macro Data Store` |
| `write` | `Feature Store` |
| `write` | `Risk Context Store` |

## 12. TTL and freshness

Intraday volatility metrics: minutes. Beta/correlation/drawdown: daily or until full recompute.

## 13. Failure policy

Если windows недостаточны, метрики не заполняются нулями. Записывается `insufficient_history`.

## 14. Acceptance criteria

- `no_zero_fallback_for_missing_history`
- `window_lengths_recorded`
- `market_beta_uses_adjusted_returns`
- `risk_metrics_horizon_tagged`
- `stale_correlations_detected`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `realized_vol_5d` | `std(log_return,5d) * sqrt(252)` |
| `realized_vol_20d` | `std(log_return,20d) * sqrt(252)` |
| `realized_vol_60d` | `std(log_return,60d) * sqrt(252)` |
| `volatility_percentile` | `PctRank(realized_vol_20d, 252d)` |
| `intraday_range_percentile` | `PctRank((H_t-L_t)/P_{t-1}, 252d)` |
| `atr_14` | `mean(TrueRange,14)`, `TrueRange=max(H-L, abs(H-C_prev), abs(L-C_prev))` |
| `downside_volatility` | `std(min(log_return,0), window) * sqrt(252)` |
| `jump_risk_score` | `1` if `abs(log_return) > k*std(log_return,window)`, else scaled exceedance |
| `gap_risk_score` | `PctRank(abs(gap_open_pct), 252d)` |
| `max_drawdown_60d` | `min(P_t / rolling_max(P,60d) - 1)` |
| `recovery_ratio` | `(P_t - drawdown_low) / (pre_drawdown_high - drawdown_low)` clipped `0..1` |
| `beta_to_market` | `cov(ret_instrument, ret_market) / var(ret_market)` over rolling window |
| `beta_stability` | `1 - PctRank(std(rolling_beta, window), history_window)` |
| `correlation_to_sector` | `corr(ret_instrument, ret_sector)` over rolling window |
| `correlation_to_currency` | `corr(ret_instrument, ret_currency)` over rolling window |
| `correlation_to_oil` | `corr(ret_instrument, ret_oil)` over rolling window |
| `correlation_to_rates` | `corr(ret_instrument, delta_ofz_yield)` over rolling window |
| `systematic_risk_share` | `R^2` from regression on market/sector/macro factors |
| `idiosyncratic_risk_share` | `1 - systematic_risk_share` |
| `volatility_risk_score` | `WAvg([volatility_percentile, gap_risk_score, jump_risk_score, downside_volatility_z], active_weights)` |

## 16. Forbidden actions

- Запрещено использовать volatility metrics как direction signal без Decision Engine.
- Запрещено считать beta/correlation на несинхронных рядах.
- Запрещено подменять отсутствующие macro series нулями.
- Запрещено отправлять заявки или менять risk limits.
