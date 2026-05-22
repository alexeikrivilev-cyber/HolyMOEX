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
