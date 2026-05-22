# Feature Validation & Research Module — Проверка признаков и исследования

## 1. Назначение

Проверяет полезность метрик на истории: IC, rank IC, decay, buckets, stability, turnover impact, regime sensitivity. Не меняет активные веса автоматически.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Feature Validation & Research Module` |
| `module_type` | `research` |
| `primary_contour` | `research_contour` |
| `secondary_contours` | `daily_contour` |
| `execution_mode` | `batch_research` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `time_range`
- `horizons`
- `run_mode`
- `config_ref`

### Trigger policy

Запускается по расписанию, вручную или после появления нового `calculation_version`.

## 4. Input classification

- `historical_feature_records`
- `historical_returns`
- `executed_orders`
- `market_state_history`
- `validation_config`

## 5. Input contract

```json
{
  "validation_input": {
    "feature_set_ref": "string",
    "return_targets": [
      "forward_return_1d",
      "forward_return_5d",
      "forward_volatility",
      "max_drawdown"
    ],
    "horizons": [
      "intraday",
      "swing",
      "position"
    ],
    "time_range": {
      "from_ts": "string",
      "to_ts": "string"
    },
    "regime_filters": [
      "string"
    ]
  }
}
```

## 6. External requests

Не делает external requests в production. Для backfill может запрашивать historical data через Gateway по отдельному research job.

## 7. Processing rules

- `align_features_and_targets`
- `prevent_lookahead_bias`
- `compute_information_coefficient`
- `compute_rank_ic`
- `compute_bucket_returns`
- `compute_feature_decay`
- `compute_stability_by_regime`
- `compute_turnover_impact`
- `write_validation_report`
- `propose_weight_profile_draft`

## 8. Output classification

- `validation_report`
- `feature_quality_record`
- `weights_profile_draft`

## 9. Output contract

```json
{
  "validation_report": {
    "validation_report_id": "string",
    "feature_set_ref": "string",
    "time_range": {
      "from_ts": "string",
      "to_ts": "string"
    },
    "metrics": {
      "feature_ic": "number",
      "rank_ic": "number",
      "feature_decay": "number",
      "hit_rate_by_quantile": "number",
      "stability_score": "number"
    },
    "recommendation": "keep | reduce_weight | disable | research_more",
    "created_at": "string"
  }
}
```

## 10. Metrics / Records

- `feature_ic`
- `rank_ic`
- `feature_decay`
- `hit_rate_by_quantile`
- `return_by_feature_bucket`
- `stability_score`
- `turnover_impact`
- `feature_drift_score`
- `regime_sensitivity_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Feature Store` |
| `read` | `Order Store` |
| `read` | `Market State Store` |
| `write` | `Research Store` |
| `write` | `Metric Weights DB` |

## 12. TTL and freshness

Research reports постоянные и версионируемые. Draft weights не становятся active без approval.

## 13. Failure policy

Если обнаружен lookahead bias или data leakage, отчёт помечается `invalid` и не может быть использован для weights approval.

## 14. Acceptance criteria

- `lookahead_bias_check_required`
- `validation_report_versioned`
- `active_weights_not_changed_automatically`
- `feature_quality_by_horizon`
- `regime_sensitivity_reported`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `feature_ic` | Pearson correlation between feature value and forward return |
| `rank_ic` | Spearman correlation between feature rank and forward return rank |
| `feature_decay` | half-life of predictive correlation across forward horizons |
| `hit_rate_by_quantile` | share of positive forward returns per feature quantile |
| `return_by_feature_bucket` | mean forward return per feature bucket/quantile |
| `stability_score` | `1 - std(rolling_rank_ic) / abs(mean(rolling_rank_ic))` clipped `0..1` |
| `turnover_impact` | average portfolio turnover implied by feature rank changes |
| `feature_drift_score` | population stability index or distribution distance vs baseline |
| `regime_sensitivity_score` | variance of feature IC across `market_regime` buckets |

## 16. Forbidden actions

- Запрещено автоматически активировать new `weights_profile`.
- Запрещено использовать future data in validation.
- Запрещено изменять production Feature Store records.
- Запрещено отправлять заявки или менять portfolio state.
