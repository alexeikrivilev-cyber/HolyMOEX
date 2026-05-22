# Normalization & Feature Vector Module — Нормализация и сбор feature-vector

## 1. Назначение

Нормализует feature records, применяет z-score/percentile/rank/winsorization, разделяет горизонты и собирает `feature_vector` для Decision Engine.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Normalization & Feature Vector Module` |
| `module_type` | `analytical/service` |
| `primary_contour` | `decision_contour` |
| `secondary_contours` | `all_contours` |
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

Запускается после обновления feature records, перед Decision Engine и в batch/research режимах.

## 4. Input classification

- `feature_record`
- `normalization_config`
- `instrument_profile`
- `market_state_record`
- `data_quality_report`

## 5. Input contract

```json
{
  "normalization_input": {
    "instrument_ids": [
      "string"
    ],
    "horizons": [
      "intraday",
      "swing",
      "position"
    ],
    "feature_refs": [
      "string"
    ],
    "normalization_profile_id": "string",
    "as_of_ts": "string"
  }
}
```

## 6. External requests

Не делает внешних запросов.

## 7. Processing rules

- `load_feature_records`
- `filter_by_horizon`
- `check_ttl_status`
- `apply_winsorization`
- `compute_zscore`
- `compute_percentile`
- `compute_market_rank`
- `compute_sector_rank`
- `build_feature_vector`
- `compute_coverage_ratio`
- `write_feature_vector`

## 8. Output classification

- `feature_record_normalized`
- `feature_vector`

## 9. Output contract

```json
{
  "feature_vector": {
    "feature_vector_id": "string",
    "instrument_id": "string",
    "horizon": "intraday | swing | position",
    "as_of_ts": "string",
    "features": {
      "metric_name": {
        "normalized_value": "number",
        "confidence_score": "number",
        "ttl_status": "fresh | stale | expired",
        "source_feature_id": "string"
      }
    },
    "coverage_ratio": "number",
    "data_quality_score": "number",
    "build_version": "string"
  }
}
```

## 10. Metrics / Records

- `metric_zscore`
- `metric_percentile`
- `market_rank`
- `sector_rank`
- `historical_rank`
- `winsorized_value`
- `coverage_ratio`
- `fresh_feature_ratio`
- `expired_feature_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Feature Store` |
| `read` | `Data Quality Store` |
| `read` | `Selected Instruments DB` |
| `write` | `Feature Store` |

## 12. TTL and freshness

`feature_vector` expires according to shortest critical feature TTL for its horizon or explicit `feature_vector_ttl_seconds`.

## 13. Failure policy

Если coverage ниже threshold, создаёт feature_vector с `coverage_ratio`, но Decision Engine может заблокировать решение по policy.

## 14. Acceptance criteria

- `horizon_separation_enforced`
- `normalization_profile_versioned`
- `raw_value_preserved`
- `expired_features_marked`
- `coverage_ratio_computed`
- `feature_vector_reproducible`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `metric_zscore` | `(raw_value - rolling_mean(raw_value,w)) / rolling_std(raw_value,w)` |
| `metric_percentile` | `PctRank(raw_value,w)` |
| `market_rank` | cross-sectional rank within selected universe at same timestamp |
| `sector_rank` | cross-sectional rank within sector at same timestamp |
| `historical_rank` | percentile of current value against instrument history |
| `winsorized_value` | `Clip(raw_value, percentile_1, percentile_99)` or config bounds |
| `coverage_ratio` | `available_features / required_features` for vector profile |
| `fresh_feature_ratio` | `fresh_features / available_features` |
| `expired_feature_count` | count where `ttl_status=expired` |

## 16. Forbidden actions

- Запрещено менять raw feature values.
- Запрещено смешивать horizons in one `feature_vector`.
- Запрещено включать expired features if `stale_policy=block_decision`.
- Запрещено принимать trading decision.
