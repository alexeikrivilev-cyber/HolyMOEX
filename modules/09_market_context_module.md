# Market Context Module — Макро, сектор и режим рынка

## 1. Назначение

Формирует общий контекст рынка: ставка, ОФЗ, валюта, нефть, sector strength, breadth, correlation regime, risk-on/risk-off и режимы рынка.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Market Context Module` |
| `module_type` | `analytical` |
| `primary_contour` | `global_contour` |
| `secondary_contours` | `daily_contour, event_contour` |
| `execution_mode` | `algorithmic + optional_ml` |
| `llm_usage` | `optional_for_macro_text_classification` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `universe_id`
- `time_range`
- `horizons`
- `run_mode`

### Trigger policy

Запускается каждые `4h-8h`, после macro events и после закрытия рынка.

## 4. Input classification

- `raw_macro_point`
- `raw_index_value`
- `raw_candle`
- `structured_event`
- `sector_mapping`

## 5. Input contract

```json
{
  "market_context_input": {
    "universe_id": "string",
    "instrument_ids": [
      "string"
    ],
    "macro_refs": [
      "string"
    ],
    "index_refs": [
      "string"
    ],
    "sector_refs": [
      "string"
    ],
    "event_refs": [
      "string"
    ],
    "windows": [
      5,
      20,
      60
    ]
  }
}
```

## 6. External requests

Запрашивает `macro_series`, index values и macro text через Gateway. LLM-запросы допускаются только для текстов ЦБ/регуляторов с output в structured fields.

## 7. Processing rules

Public macro/market intake contract:

- CBR loaders: `key_rate`, `ruonia`, `usd_rub_cbr`/`currency`, `cny_rub_cbr`, `ofz_1y`, `ofz_2y`, `ofz_10y`.
- FRED/EIA loaders: `oil`/`brent_fred`, optional `wti_fred`.
- MOEX ISS loaders: `IMOEX`, `RTSI`, `RGBI`, `USD000UTSTOM`, `CNYRUB_TOM` and regular instrument candles.
- Every MOEX ISS market-series request must be a single-secid request with explicit `payload.secid`, `payload.board_id`, `payload.timeframe=1d` and `time_range`.
- Rosstat loaders are optional and may stay absent without blocking the module.
- When rows are missing or stale, the module creates one source-specific Gateway request per known series.
- After Gateway processing the module must re-read `raw_macro.raw_macro_point`, `raw_market.raw_index_value` and `raw_market.raw_candle` before deciding whether the run is skipped or computable.

- `compute_index_returns`
- `compute_market_breadth`
- `compute_sector_strength`
- `compute_rate_metrics`
- `compute_currency_pressure`
- `compute_oil_commodity_pressure`
- `compute_risk_on_risk_off`
- `classify_market_regime`
- `classify_correlation_regime`
- `write_context_features`

## 8. Output classification

- `feature_record`
- `market_state_record`
- `regime_record`

## 9. Output contract

```json
{
  "market_state_record": {
    "market_state_id": "string",
    "as_of_ts": "string",
    "market_regime": "trend | range | stress | recovery | unknown",
    "volatility_regime": "low | normal | high | extreme",
    "liquidity_regime": "normal | thin | stressed",
    "correlation_regime": "low | normal | high",
    "risk_on_risk_off_score": "number",
    "confidence_score": "number",
    "source_refs": [
      "string"
    ]
  }
}
```

## 10. Metrics / Records

- `key_rate_level`
- `key_rate_change`
- `ofz_1y_yield`
- `ofz_2y_yield`
- `ofz_10y_yield`
- `yield_curve_slope`
- `equity_risk_premium_proxy`
- `currency_return_1d`
- `currency_return_5d`
- `oil_return_1d`
- `oil_return_5d`
- `market_breadth`
- `sector_strength_rank`
- `index_volatility_regime`
- `market_regime`
- `volatility_regime`
- `liquidity_regime`
- `correlation_regime`
- `risk_on_risk_off_score`
- `macro_pressure_score`
- `sector_pressure_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Macro Data Store` |
| `read` | `Raw Market Data Store` |
| `read` | `Event Store` |
| `write` | `Feature Store` |
| `write` | `Market State Store` |

`raw_macro.raw_macro_point` rows consumed by this module must include `provider`, `series_name`, `point_ts`, `value`, `unit`, `source_url`, `confidence_score` and `quality_flags`. Source-specific parsing quality is represented through flags such as `cbr_xml_dynamic_loader`, `cbr_public_html_loader`, `cbr_zcyc_public_html_loader`, `fred_csv_loader`, `moex_iss_index_candles_loader` and `moex_iss_fx_candles_loader`.

## 12. TTL and freshness

Macro point TTL зависит от источника. Market regime TTL: `4h-24h`, но сбрасывается при critical macro event.

## 13. Failure policy

Если macro source недоступен, сохраняет последний `market_state` со статусом `stale` и снижает confidence.

## 14. Acceptance criteria

- `market_state_available_for_decision`
- `regime_confidence_present`
- `macro_features_timestamped`
- `sector_strength_uses_active_universe`
- `stale_macro_downweighted`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `key_rate_level` | current Central Bank key rate from approved source |
| `key_rate_change` | `key_rate_level - previous_key_rate_level` |
| `ofz_1y_yield` | latest 1Y OFZ yield |
| `ofz_2y_yield` | latest 2Y OFZ yield |
| `ofz_10y_yield` | latest 10Y OFZ yield |
| `yield_curve_slope` | `ofz_10y_yield - ofz_2y_yield` |
| `equity_risk_premium_proxy` | `market_earnings_yield - ofz_10y_yield` |
| `currency_return_1d` | `currency_rate_t / currency_rate_{t-1d} - 1` |
| `currency_return_5d` | `currency_rate_t / currency_rate_{t-5d} - 1` |
| `oil_return_1d` | `oil_price_t / oil_price_{t-1d} - 1` |
| `oil_return_5d` | `oil_price_t / oil_price_{t-5d} - 1` |
| `market_breadth` | `advancing_instruments / active_instruments` |
| `sector_strength_rank` | cross-sectional rank of sector returns over configured window |
| `index_volatility_regime` | bucket from `PctRank(index_realized_vol_20d, 252d)` |
| `market_regime` | classifier output from trend + vol + breadth rules: `trend | range | stress | recovery` |
| `volatility_regime` | bucket: `low | normal | high | extreme` by volatility percentile |
| `liquidity_regime` | bucket from market-wide spread/depth/turnover percentiles |
| `correlation_regime` | bucket from average pairwise correlation in selected universe |
| `risk_on_risk_off_score` | `WAvg([market_return_z, breadth_z, -volatility_percentile, -ofz_change_z], active_weights)` |
| `macro_pressure_score` | `WAvg([key_rate_change_z, ofz_change_z, currency_return_z, oil_return_z], active_weights)` with sector-specific signs |
| `sector_pressure_score` | `WAvg([sector_strength_rank, sector_news_score, sector_volatility_regime], active_weights)` |

## 16. Forbidden actions

- Запрещено делать issuer-level выводы без instrument mapping.
- Запрещено вызывать LLM напрямую для macro text.
- Запрещено изменять `market_regime` вручную без audit record.
- Запрещено отправлять заявки или менять portfolio state.
