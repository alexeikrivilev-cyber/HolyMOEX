# Monitoring & Audit Module — Мониторинг и аудит

## 1. Назначение

Следит за здоровьем системы, качеством данных, ошибками, задержками, стоимостью LLM/API, решениями, заявками, отклонениями и ручными overrides. Делает систему воспроизводимой.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Monitoring & Audit Module` |
| `module_type` | `service` |
| `primary_contour` | `monitoring_contour` |
| `secondary_contours` | `all_contours` |
| `execution_mode` | `continuous_observability` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `time_range`
- `run_mode`

### Trigger policy

Работает непрерывно и получает события от всех модулей.

## 4. Input classification

- `module_job_result`
- `external_request_log`
- `data_quality_report`
- `decision_record`
- `risk_event`
- `execution_result`
- `portfolio_snapshot`
- `system_health_signal`

## 5. Input contract

```json
{
  "monitoring_event": {
    "event_id": "string",
    "event_type": "module_run | request | data_quality | decision | risk | execution | portfolio | system",
    "severity": "debug | info | warning | error | critical",
    "source_module": "string",
    "payload_ref": "string",
    "created_at": "string"
  }
}
```

## 6. External requests

Не делает торговых запросов. Допускается technical alert delivery через configured notification provider через Gateway, если включено.

## 7. Processing rules

- `ingest_monitoring_events`
- `compute_health_metrics`
- `detect_sla_breach`
- `detect_data_staleness`
- `detect_llm_cost_anomaly`
- `detect_execution_anomaly`
- `raise_alert`
- `write_audit_log`
- `maintain_decision_replay_index`

## 8. Output classification

- `health_report`
- `alert`
- `audit_record`
- `replay_index_record`

## 9. Output contract

```json
{
  "health_report": {
    "health_report_id": "string",
    "as_of_ts": "string",
    "system_status": "healthy | degraded | critical | stopped",
    "module_statuses": {
      "module_name": "healthy | degraded | failed"
    },
    "active_alerts": [
      "string"
    ],
    "sla_breaches": [
      "string"
    ]
  }
}
```

## 10. Metrics / Records

- `system_uptime`
- `module_latency_ms`
- `module_error_rate`
- `data_staleness_seconds`
- `decision_count`
- `risk_rejection_rate`
- `execution_error_rate`
- `llm_cost_units`
- `gateway_error_rate`
- `alert_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Audit Log Store` |
| `read` | `Module Job Result Store` |
| `read` | `Request Log Store` |
| `read` | `Decision Store` |
| `read` | `Order Store` |
| `read` | `Portfolio State Store` |
| `write` | `Monitoring Store` |
| `write` | `Audit Log Store` |

### Operational DB readiness

`audit.database_readiness_check` is an operator/admin preflight view, not a trading input. It may be queried before assembly or deployment to confirm that schemas, seed governance data, provider configs, schedules, dependency graph, initial portfolio state, risk policy, and paper/analysis weights are present.

Required operator query:

```sql
SELECT *
  FROM audit.database_readiness_check
 ORDER BY check_name;
```

Every row must have `status = 'pass'` before the system is considered database-ready. This does not mean market, text, macro, feature, order, or research history has been filled by the owner.

Metric weights have a dedicated preflight:

```sql
SELECT *
  FROM audit.metric_weights_readiness_check
 ORDER BY check_name;
```

Every row must have `status = 'pass'`. This confirms active `product_baseline` profiles, product rule totals, Decision schedule references, deprecated `strict_default` profiles, liquidity component weights, and absence of active live-trading weights.

## 12. TTL and freshness

Monitoring events постоянные для audit. Health report обновляется continuously.

## 13. Failure policy

При critical alert может включить `global_kill_switch` через Risk Policy Store только если это явно разрешено governance config.

## 14. Acceptance criteria

- `all_modules_emit_monitoring_events`
- `decision_replay_possible`
- `critical_alerts_generated`
- `request_costs_tracked`
- `kill_switch_audited`
- `health_report_available`
- `database_readiness_preflight_available`
- `metric_weights_readiness_preflight_available`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `system_uptime` | `uptime_seconds / total_observation_seconds` |
| `module_latency_ms` | `module_finished_at_ms - module_started_at_ms` |
| `module_error_rate` | `failed_module_jobs / total_module_jobs` |
| `data_staleness_seconds` | `now_ts - latest_record_timestamp` by store/module |
| `decision_count` | count of `decision_record` over window |
| `risk_rejection_rate` | `rejected_risk_checks / total_risk_checks` |
| `execution_error_rate` | `failed_execution_results / total_execution_attempts` |
| `llm_cost_units` | sum of PolzaAI provider-reported cost over window |
| `gateway_error_rate` | `failed_external_requests / total_external_requests` |
| `alert_count` | count of alerts created over window |

## 16. Forbidden actions

- Запрещено менять trading decisions.
- Запрещено отправлять заявки.
- Запрещено скрывать or delete audit records.
- Запрещено store raw API keys/secrets in logs.

## Turnover and autonomous live monitoring

Monitoring must track `gross_turnover_rub_14d`, `turnover_progress_ratio`, `remaining_turnover_rub_14d`, `projected_turnover_rub_14d`, `turnover_target_status`, risk rejections, slippage/commission drag and harmful churn. Falling behind the target is an alert, not a reason to bypass risk controls.

## Runtime stabilization note v4

Monitoring should treat `turnover_target_status`, `projected_turnover_rub_14d`, `required_daily_turnover_rub`, risk rejections, execution skips, stale-source warnings and harmful churn as first-class live health signals. A skipped execution cycle is normal when Risk Control produced no approved orders; repeated skips while the turnover mandate is behind should raise an operational alert, not bypass risk.

## Runtime hardening note v5

Monitoring must treat `source_missing_endpoint`, non-normalized live weights, failed readiness checks, stale scheduler ticks, high commission/slippage drag and negative post-cost edge as product-readiness issues. For server deployment, monitoring should distinguish optional/manual services from the primary autonomous `scheduler_worker` daemon.
