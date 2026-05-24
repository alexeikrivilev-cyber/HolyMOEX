# Orchestration Module — Модуль оркестрации

## 1. Назначение

Центральный управляющий модуль. Создаёт `module_job`, запускает модули по расписанию, событию или dependency graph, контролирует retries, idempotency, порядок выполнения, статусы и деградацию системы.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Orchestration Module` |
| `module_type` | `service` |
| `primary_contour` | `service_contour` |
| `secondary_contours` | `all_contours` |
| `execution_mode` | `scheduler + event_runner + dependency_runner` |
| `llm_usage` | `none` |

## 3. Запуск

`Orchestration Module` является корневым системным сервисом и не требует входного `module_job`. Он запускается по биржевому календарю MOEX, состоянию торговых сессий, service schedule и разрешённым системным событиям.

### Required runtime trigger fields

- `scheduler_tick_id`
- `exchange_calendar_ref`
- `market_session_status`
- `contour`
- `trigger_type`
- `universe_id`
- `instrument_ids`
- `horizons`
- `time_range`
- `run_mode`
- `idempotency_key`

### Trigger policy

Запускается как системный scheduler/service. Штатный запуск привязан к торговому календарю и времени биржи. `manual` и `replay` triggers допустимы только для maintenance/research сценариев и не должны обходить `run_mode`, `risk_policy` или dependency graph. Не выполняет расчёт метрик самостоятельно.

## 4. Input classification

- `schedule_config`
- `module_dependency_graph`
- `event_trigger`
- `manual_trigger`
- `module_job_result`
- `system_health_signal`

## 5. Input contract

```json
{
  "schedule_config_ref": "string",
  "dependency_graph_ref": "string",
  "incoming_trigger": {
    "trigger_type": "scheduled | event | dependency | manual | replay",
    "source_module": "string",
    "payload_ref": "string",
    "priority": "low | normal | high | critical"
  },
  "system_mode": "analysis_only | paper_trading | live_trading | maintenance"
}
```

## 6. External requests

Модуль не должен делать внешние market/news/broker requests. Разрешён только технический health-check через `External Request Gateway Module`, если это нужно для проверки доступности провайдеров.

## 7. Processing rules

- `build_module_job`
- `resolve_dependencies`
- `check_idempotency_key`
- `enforce_universe_filter`
- `route_job_to_module`
- `execute_module_job`
- `persist_module_job_result`
- `collect_module_job_result`
- `write_audit_record`
- `trigger_downstream_jobs`
- `apply_retry_policy`
- `stop_pipeline_on_critical_dependency_failure`

## 8. Output classification

- `module_job`
- `module_job_result`
- `pipeline_run`
- `orchestration_state`
- `audit_record`

## 9. Output contract

```json
{
  "pipeline_run_id": "string",
  "created_jobs": [
    "module_job"
  ],
  "executed_results": [
    "module_job_result"
  ],
  "status": "running | completed | degraded | failed",
  "critical_path": [
    "string"
  ],
  "skipped_modules": [
    "string"
  ],
  "audit_ref": "string"
}
```

## 10. Metrics / Records

- `pipeline_latency_ms`
- `module_success_rate`
- `module_failure_rate`
- `retry_count`
- `dependency_wait_time_ms`
- `stale_dependency_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Schedule Config Store` |
| `read` | `Module Dependency Graph Store` |
| `read/write` | `Audit Log Store` |
| `write` | `Module Job Store` |
| `write` | `Module Run Store` |
| `write` | `Module Job Result Store` |

## 12. TTL and freshness

`orchestration_state` обновляется непрерывно. `module_job` должен иметь ограниченный срок актуальности через `time_range` и `idempotency_key`.

## 13. Failure policy

При ошибке non-critical модуля выставляет `status=degraded` и продолжает pipeline. При ошибке critical dependency блокирует downstream jobs и пишет `audit_record`.

Execution failures inside a routed module are converted to `module_job_result.status=failed`; Orchestration must persist the failed result and audit record instead of dropping the job silently.

## 14. Acceptance criteria

- `all_worker_modules_started_only_by_orchestration`
- `orchestration_runs_by_exchange_calendar`
- `job_id_unique`
- `idempotency_enforced`
- `dependency_graph_respected`
- `failed_jobs_logged`
- `critical_failures_block_downstream`
- `created_jobs_are_executed_through_module_executor`
- `module_job_results_persisted_after_execution`
- `orchestration_does_not_inline_worker_business_logic`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `pipeline_latency_ms` | `finished_at_ms - started_at_ms` for full pipeline run |
| `module_success_rate` | `success_jobs / total_jobs` over rolling window |
| `module_failure_rate` | `failed_jobs / total_jobs` over rolling window |
| `retry_count` | count of retry attempts per `job_id` |
| `dependency_wait_time_ms` | `module_start_allowed_at_ms - dependency_ready_at_ms` |
| `stale_dependency_count` | count of dependencies where `ttl_status in (stale, expired)` |

## 16. Forbidden actions

- Запрещено выполнять бизнес-логику аналитических модулей внутри orchestration.
- Запрещено напрямую отправлять заявки или менять позиции.
- Запрещено напрямую вызывать внешние API, кроме создания `external_request` через Gateway job.
- Запрещено игнорировать `module_dependency_graph`.
- Запрещено запускать рабочие модули вне `module_job`.
- Запрещено запускать торговый pipeline вне разрешённого биржевого календаря, кроме `research_contour`, `backtest`, `replay` или maintenance.

## 17. Module execution contract

Orchestration executes worker modules only through a `ModuleExecutor` adapter.

`ModuleExecutor` responsibilities:

- resolve the documented module input payload from orchestration context or `module_job.input_refs`;
- call the target module public interface: `process(payload, module_job)` or `run(payload, module_job)`;
- extract or synthesize a valid `module_job_result`;
- return failures as `module_job_result.status=failed` without losing `job_id`, `module_name`, warnings, errors, or audit context.

Orchestration responsibilities after execution:

- save `module_run.status=running` before execution;
- save final `module_run.status` after execution;
- save `module_job_result`;
- write an `audit_record` for executed, skipped, and failed jobs;
- keep business calculations inside the target module, not inside Orchestration.

## Runtime stabilization note v4

`Orchestration Module` must preserve current-cycle `output_refs` and pass them into subsequent `module_job.input_refs`. This prevents live jobs from relying on stale placeholder refs such as `decisions.decision_set:latest` or `orders.order_intent:latest` when a current-cycle object exists.

The autonomous server worker is persistent and repeatedly triggers orchestration for market data, macro, text, feature, decision, execution, portfolio and monitoring sources. It does not call analytical modules directly and does not bypass the `External Request Gateway Module`, `Decision Engine Module`, `Risk Control Module` or `Execution Engine Module` boundaries.

`Execution Engine Module` must receive explicit current-cycle `order_intent_refs`. If the current risk check produced no approved order intents, execution is skipped rather than falling back to a stale latest order.

## Runtime hardening note v5

`scheduler_worker` is schedule-aware in PostgreSQL runtime. It reads enabled `audit.schedule_config` rows, derives intervals from `interval_seconds` or `frequency`, and triggers Orchestration by source. Pure event-driven schedules such as `on_decision_set` and `on_approved_order` are not launched blindly by timer; they are handled by current-cycle refs produced by upstream modules.

Docker/server runtime must treat `scheduler_worker` as the primary long-running process. `agent_app` is a manual one-shot control entrypoint, and `research_worker` is an optional batch profile. Restart-loops around one-shot entrypoints are forbidden.
