# Decision Engine Module — Модуль принятия решений

## 1. Назначение

Принимает торговое решение на базе `feature_vector`, `Metric Weights DB`, `Portfolio State Store` и decision policy. Не исполняет заявки и не обращается к брокеру.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Decision Engine Module` |
| `module_type` | `decision` |
| `primary_contour` | `decision_contour` |
| `secondary_contours` | `event_contour, intraday_contour, daily_contour` |
| `execution_mode` | `algorithmic_scoring` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `instrument_ids`
- `horizons`
- `run_mode`
- `config_ref`

### Trigger policy

Запускается после обновления feature_vector, по расписанию или при important event. В `analysis_only` пишет решения без order intents.

## 4. Input classification

- `feature_vector`
- `weights_profile`
- `metric_weight_rule`
- `portfolio_snapshot`
- `market_state_record`
- `decision_policy`

## 5. Input contract

```json
{
  "decision_request": {
    "decision_request_id": "string",
    "universe_id": "string",
    "instrument_ids": [
      "string"
    ],
    "horizon": "intraday | swing | position",
    "as_of_ts": "string",
    "feature_vector_refs": [
      "string"
    ],
    "portfolio_state_ref": "string",
    "weights_profile_id": "string",
    "run_mode": "analysis_only | paper_trading | live_trading",
    "decision_mode": "normal | risk_off | reduce_only | manual_approval_required"
  }
}
```

## 6. External requests

Не делает внешних запросов.

## 7. Processing rules

- `load_feature_vectors`
- `load_active_weights_profile`
- `validate_feature_coverage`
- `apply_metric_weights`
- `apply_horizon_rules`
- `compute_expected_edge_score`
- `compute_risk_score`
- `apply_position_targets`
- `generate_feature_contributions`
- `write_decision_set`

## 8. Output classification

- `decision_set`
- `decision_record`
- `decision_explanation`

## 9. Output contract

```json
{
  "decision_set": "decision_set"
}
```

## 10. Metrics / Records

- `expected_edge_score`
- `risk_score`
- `decision_confidence_score`
- `feature_contribution`
- `target_position_pct`
- `target_quantity`
- `decision_threshold_margin`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Feature Store` |
| `read` | `Metric Weights DB` |
| `read` | `Portfolio State Store` |
| `read` | `Market State Store` |
| `write` | `Decision Store` |
| `write` | `Audit Log Store` |

Active paper/analysis decision schedules must use:

- `weights:product_baseline:intraday:v1`
- `weights:product_baseline:swing:v1`
- `weights:product_baseline:position:v1`

The deprecated `strict_default` profiles may be used only for audit/replay comparisons, not as the runtime schedule default. `live_trading` requires a separate active profile approved by governance.

## 12. TTL and freshness

Decision TTL должен быть меньше или равен TTL входного `feature_vector`. Event-driven decisions могут иметь отдельный short TTL.

## 13. Failure policy

Если weights profile не active, feature coverage низкий или portfolio state stale, модуль возвращает `block` или `manual_approval_required`.

## 14. Acceptance criteria

- `no_llm_final_decision`
- `weights_loaded_from_db`
- `feature_contributions_written`
- `decision_reproducible`
- `run_mode_respected`
- `no_broker_calls`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `expected_edge_score` | `sum(normalized_feature_i * weight_i * direction_i * confidence_i)` over active profile |
| `risk_score` | `WAvg([volatility_risk_score, liquidity_risk_score, portfolio_concentration_risk, data_quality_penalty], active_weights)` |
| `decision_confidence_score` | `coverage_ratio * mean(feature_confidence) * weights_profile_confidence` |
| `feature_contribution` | `normalized_feature_i * weight_i * direction_i * confidence_i` |
| `target_position_pct` | sizing function of `expected_edge_score`, `risk_score`, `portfolio_limits`, clipped by max exposure |
| `target_quantity` | `floor((target_position_value / latest_price) / quantity_step) * quantity_step` |
| `decision_threshold_margin` | `abs(expected_edge_score) - configured_action_threshold` |

## 16. Forbidden actions

- Запрещено отправлять заявки напрямую.
- Запрещено изменять `Metric Weights DB`.
- Запрещено использовать features with `ttl_status=expired` unless profile explicitly allows.
- Запрещено принимать решение без `Portfolio State Store` snapshot.
- Запрещено bypass `Risk Control Module`.

## 17. Capital and sizing baseline

Base portfolio capital comes from `Portfolio State Store`. Initial capital must be seeded as:

```json
{
  "portfolio_id": "string",
  "initial_capital_rub": 1000000,
  "cash": 1000000,
  "source": "initial_seed"
}
```

`Decision Engine Module` cannot assume fixed cash after startup. It must use latest `portfolio_snapshot`.

## Autonomous live turnover mandate

`Decision Engine Module` treats `trading_mandate:live:turnover_10m_14d:v1` as a strategy objective in `live_trading`. It may raise `trade_urgency_score` when `Portfolio State Module` reports `turnover_target_status = behind | critically_behind`, but it must not generate trades with negative expected edge only to create volume. Turnover-driven decisions must include reason code `turnover_mandate_urgency` and remain subject to `Risk Control Module`.

## Runtime stabilization note v4

`reason_codes` are not automatically blocking. The module distinguishes hard-blocking reason codes from explanatory/warning reason codes. `turnover_mandate_urgency` is explanatory: it may increase trade urgency for a positive-edge decision but must not by itself convert the decision action to `block`.

Hard blocking remains valid for missing feature vectors, insufficient coverage, disallowed run mode, inactive or mismatched weights profile, stale portfolio/market state, expired features under block policy and explicit manual-review/risk-off conditions.

## Post-cost turnover note v5

For autonomous live turnover, the module must emit both gross/pre-cost edge and signed post-cost economics in the `decision_set.decisions` payload: `gross_expected_edge_score`, `expected_edge_after_cost_score`, `execution_cost_estimate_bps`, `commission_bps`, `edge_to_cost_ratio` and `position_effect`. Action selection is net-edge-first: new `buy`/`sell` candidates are ranked on post-cost edge, and turnover urgency cannot turn a below-threshold post-cost signal into a trade. `Risk Control Module` remains the hard gate, but Decision should avoid cost-blind candidates.

Position semantics are explicit:

- `open_long`, `increase_long`: buy to create/add long exposure.
- `reduce_long`, `close_long`: sell existing long exposure.
- `open_short`, `increase_short`: sell to create/add short exposure when `DECISION_ALLOW_SHORT_SELLING=true` and provider capability is enabled.
- `reduce_short`, `close_short`: buy to cover existing short exposure.

Existing long positions use a post-cost exit overlay. Stop-loss is a hard reduce/close proposal. If profit exceeds `DECISION_TAKE_PROFIT_PCT` and post-cost continuation edge weakens, Decision may propose partial take-profit via `DECISION_PARTIAL_TAKE_PROFIT_RATIO`; if continuation edge remains strong it can hold and let the trend continue. A profitable long with non-positive post-cost edge should not be held only because old pre-cost edge was positive.

Short positions use the same side-aware policy: profitable shorts can be partially covered when negative post-cost edge weakens, losing shorts are reduced/covered at `DECISION_SHORT_STOP_LOSS_PCT`, and new shorts require explicit negative post-cost edge below `DECISION_SHORT_ENTRY_THRESHOLD`.
