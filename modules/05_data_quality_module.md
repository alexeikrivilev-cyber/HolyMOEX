# Data Quality Module — Контроль качества данных

## 1. Назначение

Проверяет свежесть, полноту, непротиворечивость и пригодность данных перед записью признаков и перед решением.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Data Quality Module` |
| `module_type` | `service` |
| `primary_contour` | `service_contour` |
| `secondary_contours` | `all_contours` |
| `execution_mode` | `dependency_driven` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `input_refs`
- `contour`
- `time_range`
- `run_mode`

### Trigger policy

Запускается после загрузки raw data, после расчёта feature records и перед Decision/Risk stages.

## 4. Input classification

- `raw_market_data`
- `raw_text_item`
- `raw_macro_data`
- `feature_record`
- `feature_vector`
- `portfolio_snapshot`

## 5. Input contract

```json
{
  "quality_check_request": {
    "input_refs": [
      "string"
    ],
    "check_level": "raw | feature | decision | execution",
    "required_freshness_seconds": "integer",
    "required_coverage_ratio": "number",
    "critical_fields": [
      "string"
    ]
  }
}
```

## 6. External requests

Не делает внешних запросов. Проверяет только данные из stores.

## 7. Processing rules

- `check_missing_values`
- `check_stale_timestamps`
- `check_duplicate_records`
- `check_outliers`
- `check_source_conflicts`
- `check_feature_ttl`
- `check_required_fields`
- `assign_quality_flags`
- `compute_data_quality_score`

## 8. Output classification

- `data_quality_report`
- `quality_flags`
- `data_quality_feature_records`

## 9. Output contract

```json
{
  "data_quality_report": {
    "quality_report_id": "string",
    "input_refs": [
      "string"
    ],
    "check_level": "raw | feature | decision | execution",
    "data_quality_score": "number",
    "coverage_ratio": "number",
    "freshness_status": "fresh | stale | expired",
    "quality_flags": [
      "missing_data | stale_data | duplicate_data | outlier_data | source_conflict | low_coverage"
    ],
    "blocking_errors": [
      "string"
    ],
    "created_at": "string"
  }
}
```

## 10. Metrics / Records

- `data_quality_score`
- `coverage_ratio`
- `stale_record_count`
- `missing_field_count`
- `outlier_count`
- `source_conflict_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Market Data Store` |
| `read` | `Raw Text Store` |
| `read` | `Feature Store` |
| `read` | `Portfolio State Store` |
| `write` | `Data Quality Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

Data quality reports use the same TTL as checked object or shorter. Decision-level reports expire before next decision cycle.

## 13. Failure policy

При критических ошибках возвращает blocking flags. Decision и Execution должны блокироваться при `data_quality_score` ниже policy threshold.

## 14. Acceptance criteria

- `quality_flags_standardized`
- `blocking_errors_explicit`
- `quality_score_available_for_decision`
- `stale_data_detected`
- `source_conflicts_logged`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `data_quality_score` | `Clip(1 - 0.35*missing_rate - 0.25*stale_rate - 0.2*outlier_rate - 0.2*conflict_rate, 0, 1)` |
| `coverage_ratio` | `available_required_records / expected_required_records` |
| `stale_record_count` | count of records where `now_ts - timestamp > ttl_seconds` |
| `missing_field_count` | count of required fields that are null/empty/invalid |
| `outlier_count` | count where `abs(Z(value,w)) > configured_threshold` |
| `source_conflict_count` | count of conflicting values across sources beyond tolerance |

## 16. Forbidden actions

- Запрещено исправлять данные без записи `quality_flags` и `calculation_version`.
- Запрещено удалять записи из raw stores.
- Запрещено принимать trading decision или менять веса.
- Запрещено пропускать failed quality checks без записи в `Audit Log Store`.
