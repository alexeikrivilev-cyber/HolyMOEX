# Liquidity & Microstructure Module — Ликвидность и микроструктура

## 1. Назначение

Оценивает стоимость исполнения и краткосрочное давление рынка через стакан, спред, глубину, сделки и imbalance.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Liquidity & Microstructure Module` |
| `module_type` | `analytical` |
| `primary_contour` | `realtime_contour` |
| `secondary_contours` | `intraday_contour` |
| `execution_mode` | `algorithmic` |
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

Запускается при обновлении orderbook/trades или по расписанию `1m-5m`.

## 4. Input classification

- `raw_orderbook`
- `raw_trade`
- `raw_quote`
- `instrument_profile`

## 5. Input contract

```json
{
  "liquidity_input": {
    "instrument_ids": [
      "string"
    ],
    "orderbook_ref": "string",
    "trades_ref": "string",
    "quotes_ref": "string",
    "depth_levels": [
      "10bps",
      "30bps",
      "50bps",
      "100bps"
    ],
    "notional_scenarios": [
      100000,
      1000000
    ]
  }
}
```

## 6. External requests

Запрашивает `orderbook`, `trades`, `market_data` через Gateway, если данные устарели.

## 7. Processing rules

MOEX ISS intake contract:

- `orderbook` and `trades` requests are generated separately for every active `Selected Instruments DB` profile.
- Each request carries exactly one `instrument_id`, explicit `secid`, `board_id`, `time_range.from_ts` and `time_range.to_ts`.
- The module must re-read `raw_market.raw_orderbook` and `raw_market.raw_trade` after Gateway processing before computing liquidity metrics.
- Missing profiles may trigger per-instrument `instruments` metadata requests only; multi-instrument `market_data`, `orderbook` or `trades` requests are forbidden.

- `compute_bid_ask_spread`
- `select_latest_orderbook_candidate`
- `classify_orderbook_freshness`
- `compute_order_book_depth`
- `estimate_slippage`
- `compute_amihud_illiquidity`
- `compute_order_book_imbalance`
- `classify_aggressive_trades`
- `compute_trade_imbalance`
- `compute_short_term_pressure`
- `write_feature_records`

## 8. Output classification

- `feature_record`
- `execution_constraint_hint`

## 9. Output contract

```json
{
  "feature_records": [
    {
      "metric_group": "liquidity",
      "metric_type": "raw_metric | derived_metric | composite_score",
      "metric_name": "string",
      "raw_value": "number",
      "horizon": "intraday",
      "ttl_seconds": "integer",
      "confidence_score": "number",
      "quality_flags": [
        "string"
      ]
    }
  ],
  "execution_constraint_hint": {
    "instrument_id": "string",
    "max_suggested_order_notional": "number",
    "market_order_allowed": "boolean",
    "reason_codes": [
      "string"
    ]
  }
}
```

## 10. Metrics / Records

- `bid_ask_spread_bps`
- `spread_percentile`
- `order_book_depth_10bps`
- `order_book_depth_30bps`
- `order_book_depth_50bps`
- `estimated_slippage_100k`
- `estimated_slippage_1m`
- `amihud_illiquidity`
- `order_book_imbalance`
- `order_flow_imbalance`
- `trade_imbalance`
- `aggressive_buy_ratio`
- `aggressive_sell_ratio`
- `quote_velocity`
- `spread_widening_flag`
- `short_term_pressure_score`
- `liquidity_risk_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Market Data Store` |
| `read` | `Selected Instruments DB` |
| `read` | `Metric Weights DB` |
| `write` | `Feature Store` |
| `write` | `Execution Constraint Store` |

## 12. TTL and freshness

Orderbook и imbalance metrics живут `30-180` seconds. Slippage estimates живут не дольше `300` seconds.

For every instrument the module must select the latest available `raw_orderbook` snapshot with `snapshot_ts <= module_job.time_range.to_ts`, including a snapshot before `time_range.from_ts`. Freshness is then classified by `module_job.time_range.to_ts - snapshot_ts`. `missing_orderbook` is allowed only when no orderbook snapshot exists for the instrument at or before `time_range.to_ts`; an older snapshot must be reported as `stale_orderbook`.

## 13. Failure policy

Если стакан устарел, модуль пишет `stale_orderbook` и запрещает market execution hint. Если стакан полностью отсутствует до `time_range.to_ts`, модуль пишет `missing_orderbook` и также запрещает market execution hint.

## 14. Acceptance criteria

- `stale_orderbook_detected`
- `missing_orderbook_not_confused_with_stale_orderbook`
- `slippage_estimates_have_notional_scenario`
- `market_order_allowed_explicit`
- `imbalance_uses_same_snapshot_time`
- `execution_hints_written`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `bid_ask_spread_bps` | `(best_ask - best_bid) / mid_price * 10000`, `mid_price=(best_ask+best_bid)/2` |
| `spread_percentile` | `PctRank(bid_ask_spread_bps, rolling_window)` |
| `order_book_depth_10bps` | sum bid+ask volume within `10 bps` from mid price |
| `order_book_depth_30bps` | sum bid+ask volume within `30 bps` from mid price |
| `order_book_depth_50bps` | sum bid+ask volume within `50 bps` from mid price |
| `estimated_slippage_100k` | simulated average execution price for `100000 RUB` order vs mid, in bps |
| `estimated_slippage_1m` | simulated average execution price for `1000000 RUB` order vs mid, in bps |
| `amihud_illiquidity` | `abs(return_1d) / Turnover_1d` |
| `order_book_imbalance` | `(bid_depth - ask_depth) / (bid_depth + ask_depth)` for configured bps band |
| `order_flow_imbalance` | `(buy_order_flow - sell_order_flow) / (buy_order_flow + sell_order_flow)` |
| `trade_imbalance` | `(aggressive_buy_volume - aggressive_sell_volume) / total_trade_volume` |
| `aggressive_buy_ratio` | `aggressive_buy_volume / total_trade_volume` |
| `aggressive_sell_ratio` | `aggressive_sell_volume / total_trade_volume` |
| `quote_velocity` | `quote_update_count / window_seconds` |
| `spread_widening_flag` | `1` if `bid_ask_spread_bps > Pctile(spread, 95%)`, else `0` |
| `short_term_pressure_score` | `WAvg([order_book_imbalance, trade_imbalance, order_flow_imbalance], active_weights)` |
| `liquidity_risk_score` | `WAvg([spread_percentile, estimated_slippage_1m, amihud_illiquidity, -order_book_depth_30bps_z], active_weights)` |

Composite liquidity weights are read from active `Metric Weights DB` profile `liquidity_microstructure_component_weights`. The module may not create, update, activate, or deprecate weight profiles. Seeded component weights are allowed only for `analysis_only` and `paper_trading` unless a separate governance process approves a new version.

## 16. Forbidden actions

- Запрещено симулировать стакан, если orderbook data отсутствует.
- Запрещено использовать stale orderbook для execution approval.
- Запрещено отправлять заявки.
- Запрещено писать price/fundamental/news metrics.
- Запрещено изменять `Metric Weights DB`.
- Запрещено скрывать низкую ликвидность через нормализацию без `quality_flags`.
