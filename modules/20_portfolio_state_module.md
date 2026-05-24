# Portfolio State Module — Состояние портфеля

## 1. Назначение

Поддерживает актуальное состояние портфеля: позиции, кэш, PnL, exposure, доступные лимиты, realized/unrealized PnL и reconciliation с брокером.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Portfolio State Module` |
| `module_type` | `service` |
| `primary_contour` | `execution_contour` |
| `secondary_contours` | `daily_contour, decision_contour` |
| `execution_mode` | `state_manager` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `time_range`
- `run_mode`
- `input_refs`

### Trigger policy

Запускается после fills, перед Decision/Risk, по расписанию reconciliation и после market close.

## 4. Input classification

- `fill_report`
- `broker_portfolio_response`
- `broker_positions_response`
- `market_prices`
- `portfolio_config`

## 5. Input contract

```json
{
  "portfolio_update_request": {
    "portfolio_id": "string",
    "fill_report_refs": [
      "string"
    ],
    "broker_snapshot_ref": "string",
    "price_snapshot_ref": "string",
    "run_mode": "paper_trading | live_trading | analysis_only",
    "as_of_ts": "string"
  }
}
```

## 6. External requests

Запрашивает broker portfolio/positions через Gateway. В paper mode использует внутренний simulated portfolio.

## 7. Processing rules

- `load_previous_snapshot`
- `apply_fills`
- `mark_to_market_positions`
- `compute_cash_balance`
- `compute_realized_unrealized_pnl`
- `compute_exposures`
- `reconcile_with_broker`
- `write_portfolio_snapshot`
- `emit_state_update_event`

## 8. Output classification

- `portfolio_snapshot`
- `position_state`
- `portfolio_reconciliation_report`

## 9. Output contract

```json
{
  "portfolio_snapshot": {
    "portfolio_snapshot_id": "string",
    "portfolio_id": "string",
    "as_of_ts": "string",
    "cash": "number",
    "equity_value": "number",
    "gross_exposure": "number",
    "net_exposure": "number",
    "realized_pnl": "number",
    "unrealized_pnl": "number",
    "positions": [
      {
        "instrument_id": "string",
        "quantity": "integer",
        "avg_price": "number",
        "market_price": "number",
        "market_value": "number",
        "unrealized_pnl": "number"
      }
    ],
    "data_quality_score": "number"
  }
}
```

## 10. Metrics / Records

- `cash`
- `equity_value`
- `gross_exposure`
- `net_exposure`
- `instrument_exposure`
- `sector_exposure`
- `realized_pnl`
- `unrealized_pnl`
- `daily_pnl`
- `drawdown`
- `available_risk_budget`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Order Store` |
| `read` | `Raw Market Data Store` |
| `read/write` | `Portfolio State Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

Portfolio snapshot для decision/execution должен быть fresh. Live trading требует reconciliation TTL по policy.

## 13. Failure policy

Если reconciliation mismatch превышает threshold, блокирует new risk approval через `portfolio_state_stale_or_inconsistent`.

## 14. Acceptance criteria

- `portfolio_snapshot_available_before_decision`
- `fills_applied_once`
- `broker_reconciliation_supported`
- `position_state_versioned`
- `stale_portfolio_detected`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `cash` | ArenaGo `cash_balance` or internal cash after reconciled trades |
| `equity_value` | `cash + sum(position_quantity * latest_price)` |
| `gross_exposure` | `sum(abs(position_value)) / equity_value` |
| `net_exposure` | `sum(position_value) / equity_value` |
| `instrument_exposure` | `position_value / equity_value` |
| `sector_exposure` | `sum(position_value by sector) / equity_value` |
| `realized_pnl` | sum of closed trade PnL after fees |
| `unrealized_pnl` | `sum((latest_price - average_price) * position_quantity)` adjusted for side |
| `daily_pnl` | `equity_value_t - equity_value_start_of_day` |
| `drawdown` | `equity_value / rolling_peak_equity - 1` |
| `available_risk_budget` | `total_risk_budget - used_risk_budget` |

## 16. ArenaGo sync rules

- Initial portfolio capital: `INITIAL_CAPITAL_RUB=1000000`.
- Use `get_bots` to read cash for configured `ARENA_GO_BOT_NAME`.
- Use `get_positions` to refresh position quantities and average prices.
- Use `get_trades` to reconcile fills and realized PnL.

## 17. Forbidden actions

- Запрещено отправлять заявки.
- Запрещено принимать решения.
- Запрещено overwrite provider state without reconciliation record.
- Запрещено provide fresh portfolio snapshot if ArenaGo sync failed and TTL expired.

## 18. Initial capital seed

At first launch, if no `portfolio_snapshot` exists, create:

```json
{
  "portfolio_id": "arena_go_default",
  "cash": 1000000,
  "equity_value": 1000000,
  "currency": "RUB",
  "source": "initial_seed",
  "as_of_ts": "string"
}
```

After first ArenaGo sync, provider state has priority over seed state.

## Turnover accounting

`Portfolio State Module` is the source of truth for realized turnover progress. It calculates rolling `gross_turnover_rub_1d` and `gross_turnover_rub_14d` from actual fills/trades, not from intended orders. These values feed `Decision Engine Module`, `Risk Control Module`, monitoring and validation.

## Runtime stabilization note v4

Turnover mandate metrics are calculated from realized fills/trades, not order intents. The 1-day turnover is summed for the current day, broker trades are filtered to the rolling mandate window, required daily turnover is based on remaining days, and projected 14-day turnover is based on observed average daily pace.

`turnover_target_status` is time-aware: actual progress is compared to expected progress for the elapsed part of the mandate window. This lets the agent become more active when it is genuinely behind schedule, without rewarding blind churn.

## Runtime hardening note v5

Turnover progress is a realized KPI, not an order-intent KPI. The module must calculate turnover from fills, execution results and normalized ArenaGo trades after timestamp normalization. These realized turnover fields are used by Decision Engine and Monitoring, and they are also required for `expected_edge_after_cost` / churn-quality analysis.

## Runtime hardening note v6

ArenaGo sync resolves the provider portfolio from exact `bots[].name`. `get_bots` is called first; `get_positions` and `get_trades` then use the same exact bot/portfolio name. Empty positions/trades are valid initial state when the bot exists and `cash_balance` is available. If broker sync fails and the previous live snapshot is missing or stale, the module must fail rather than fabricate a fresh portfolio state.
