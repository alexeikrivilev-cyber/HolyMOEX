# Market Data Metrics Module — Рыночные ценовые метрики

## 1. Назначение

Считает ценовые и относительные признаки: доходность, momentum, trend, gap, VWAP deviation, relative strength against market/sector.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Market Data Metrics Module` |
| `module_type` | `analytical` |
| `primary_contour` | `realtime_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `algorithmic` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `universe_id`
- `instrument_ids`
- `horizons`
- `time_range`
- `run_mode`

### Trigger policy

Запускается после обновления candles/trades/index values или по расписанию `1m-5m` для intraday и `daily_eod` для daily metrics.

## 4. Input classification

- `raw_candle`
- `raw_trade`
- `raw_index_value`
- `instrument_profile`
- `sector_mapping`

## 5. Input contract

```json
{
  "market_data_metrics_input": {
    "instrument_ids": [
      "string"
    ],
    "candles_ref": "string",
    "trades_ref": "string",
    "market_index_ref": "string",
    "sector_index_ref": "string",
    "timeframes": [
      "1m",
      "5m",
      "15m",
      "1d"
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

Создаёт `external_request` для `market_data`, `trades`, `instruments` только через Gateway, если raw data отсутствуют или устарели.

## 7. Processing rules

MOEX ISS intake contract:

- `market_data` requests are generated separately for every active `Selected Instruments DB` profile and every requested timeframe.
- Each request carries exactly one `instrument_id`, explicit `secid`, `board_id`, `timeframe`, `time_range.from_ts` and `time_range.to_ts`.
- `trades` and `instruments` lookup requests are also generated per instrument.
- After Gateway processing the module must re-read `raw_market.raw_candle`, `raw_market.raw_trade` and index rows before deciding that raw data is missing.

- `compute_returns`
- `compute_log_returns`
- `compute_rolling_momentum`
- `compute_trend_slope`
- `compute_trend_t_stat`
- `compute_gap_metrics`
- `compute_vwap_deviation`
- `compute_excess_return_vs_market`
- `compute_excess_return_vs_sector`
- `write_feature_records`

## 8. Output classification

- `feature_record`

## 9. Output contract

```json
{
  "feature_records": [
    {
      "metric_group": "price",
      "metric_type": "raw_metric | derived_metric | composite_score",
      "metric_name": "string",
      "raw_value": "number",
      "normalized_value": "number | null",
      "horizon": "intraday | swing | position",
      "ttl_seconds": "integer",
      "confidence_score": "number",
      "calculation_version": "string"
    }
  ]
}
```

## 10. Metrics / Records

- `return_1d`
- `return_5d`
- `return_20d`
- `return_60d`
- `intraday_return`
- `log_return`
- `momentum_5d`
- `momentum_20d`
- `momentum_acceleration`
- `trend_slope`
- `trend_t_stat`
- `distance_to_20d_high`
- `distance_to_60d_high`
- `distance_to_20d_low`
- `gap_open_pct`
- `gap_persistence_score`
- `price_vs_vwap_zscore`
- `excess_return_vs_market`
- `excess_return_vs_sector`
- `relative_strength_rank_market`
- `relative_strength_rank_sector`
- `price_strength_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Selected Instruments DB` |
| `read` | `Raw Market Data Store` |
| `write` | `Feature Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

Intraday price metrics: `60-300` seconds. Daily metrics: until next daily close. Relative strength recalculates whenever market/sector data changes.

## 13. Failure policy

Если market index или sector data отсутствуют, записывает price metrics без relative metrics и ставит `quality_flags=[low_context_coverage]`.

## 14. Acceptance criteria

- `no_lookahead_bias`
- `all_metrics_timestamped`
- `relative_metrics_use_same_time_window`
- `adjusted_prices_used_when_required`
- `calculation_version_present`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `return_1d` | `P_t / P_{t-1d} - 1` |
| `return_5d` | `P_t / P_{t-5d} - 1` |
| `return_20d` | `P_t / P_{t-20d} - 1` |
| `return_60d` | `P_t / P_{t-60d} - 1` |
| `intraday_return` | `P_t / O_session - 1` |
| `log_return` | `ln(P_t / P_{t-1})` |
| `momentum_5d` | `return_5d` normalized as `Z(return_5d, 252d)` or sector rank |
| `momentum_20d` | `return_20d` normalized as `Z(return_20d, 252d)` or sector rank |
| `momentum_acceleration` | `Z(return_5d,252d) - Z(return_20d,252d)` |
| `trend_slope` | OLS slope of `ln(P)` over configured window |
| `trend_t_stat` | `trend_slope / standard_error(trend_slope)` |
| `distance_to_20d_high` | `P_t / max(H,20d) - 1` |
| `distance_to_60d_high` | `P_t / max(H,60d) - 1` |
| `distance_to_20d_low` | `P_t / min(L,20d) - 1` |
| `gap_open_pct` | `O_t / P_prev_close - 1` |
| `gap_persistence_score` | `(P_t - O_t) / abs(O_t - P_prev_close)` clipped `[-1,1]`; if no gap then `0` |
| `price_vs_vwap_zscore` | `Z((P_t - VWAP_t) / VWAP_t, rolling_window)` |
| `excess_return_vs_market` | `return_n(instrument) - return_n(market_index)` |
| `excess_return_vs_sector` | `return_n(instrument) - return_n(sector_index)` |
| `relative_strength_rank_market` | `RankMarket(excess_return_vs_market)` within selected universe |
| `relative_strength_rank_sector` | `RankSector(excess_return_vs_sector)` within sector |
| `price_strength_score` | `WAvg([momentum_5d, momentum_20d, momentum_acceleration, trend_t_stat, relative_strength_rank_market], active_weights)` |

## 16. Forbidden actions

- Запрещено использовать future prices или incomplete candles как closed candles.
- Запрещено учитывать инструменты вне `Selected Instruments DB`.
- Запрещено писать news/event/fundamental metrics.
- Запрещено принимать решения или отправлять заявки.
- Запрещено изменять formula weights внутри модуля: веса только из `Metric Weights DB`.
