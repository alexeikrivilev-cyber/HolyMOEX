# Backtesting & Paper Trading Module — Бэктест и бумажная торговля

## 1. Назначение

Проверяет стратегии и decision pipeline на истории и в paper mode с учётом комиссий, проскальзывания, ликвидности, задержек, корпоративных действий и trading calendar.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Backtesting & Paper Trading Module` |
| `module_type` | `research/execution_sim` |
| `primary_contour` | `research_contour` |
| `secondary_contours` | `execution_contour` |
| `execution_mode` | `historical_simulation + live_simulation` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `time_range`
- `universe_id`
- `horizons`
- `run_mode`
- `config_ref`

### Trigger policy

Запускается вручную, по research schedule или как постоянный paper-trading контур.

## 4. Input classification

- `historical_raw_data`
- `feature_history`
- `weights_profile`
- `risk_policy`
- `execution_policy`
- `corporate_actions`
- `trading_calendar`

## 5. Input contract

```json
{
  "simulation_request": {
    "simulation_id": "string",
    "universe_id": "string",
    "time_range": {
      "from_ts": "string",
      "to_ts": "string"
    },
    "weights_profile_id": "string",
    "risk_policy_id": "string",
    "execution_model_id": "string",
    "cost_model_id": "string",
    "run_mode": "backtest | paper_trading"
  }
}
```

## 6. External requests

Для backfill исторических данных может использовать Gateway. Во время simulation не должен обращаться к future data.

## 7. Processing rules

- `load_historical_data`
- `apply_corporate_action_adjustments`
- `rebuild_historical_features`
- `prevent_lookahead_bias`
- `simulate_decisions`
- `simulate_risk_checks`
- `simulate_execution_costs`
- `compute_performance_metrics`
- `write_simulation_report`

## 8. Output classification

- `simulation_report`
- `paper_execution_result`
- `performance_record`
- `validation_report`

## 9. Output contract

```json
{
  "simulation_report": {
    "simulation_report_id": "string",
    "simulation_id": "string",
    "time_range": {
      "from_ts": "string",
      "to_ts": "string"
    },
    "performance": {
      "total_return": "number",
      "annualized_return": "number",
      "max_drawdown": "number",
      "sharpe_ratio": "number",
      "turnover": "number",
      "win_rate": "number",
      "avg_slippage_bps": "number"
    },
    "bias_checks": [
      "string"
    ],
    "created_at": "string"
  }
}
```

## 10. Metrics / Records

- `total_return`
- `annualized_return`
- `max_drawdown`
- `volatility`
- `sharpe_ratio`
- `sortino_ratio`
- `turnover`
- `win_rate`
- `profit_factor`
- `avg_slippage_bps`
- `fees_total`
- `capacity_estimate`
- `feature_leakage_flag`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Market Data Store` |
| `read` | `Feature Store` |
| `read` | `Metric Weights DB` |
| `read` | `Risk Policy Store` |
| `read` | `Corporate Actions Store` |
| `write` | `Research Store` |
| `write` | `Order Store` |

## 12. TTL and freshness

Backtest reports постоянные и привязаны к input versions. Paper-trading state обновляется live.

## 13. Failure policy

Если найден lookahead bias, missing corporate adjustment или invalid calendar alignment, simulation report получает `invalid`.

## 14. Acceptance criteria

- `transaction_costs_included`
- `slippage_model_explicit`
- `corporate_actions_applied`
- `no_future_data_access`
- `performance_report_versioned`
- `paper_mode_uses_same_decision_risk_contracts`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `total_return` | `ending_equity / starting_equity - 1` |
| `annualized_return` | `(1 + total_return)^(252/trading_days) - 1` |
| `max_drawdown` | `min(equity_curve / rolling_peak_equity - 1)` |
| `volatility` | `std(daily_returns) * sqrt(252)` |
| `sharpe_ratio` | `(annualized_return - risk_free_rate) / volatility` |
| `sortino_ratio` | `(annualized_return - risk_free_rate) / downside_volatility` |
| `turnover` | `sum(abs(trade_value)) / average_equity` |
| `win_rate` | `profitable_trades / closed_trades` |
| `profit_factor` | `gross_profit / abs(gross_loss)` |
| `avg_slippage_bps` | mean simulated/actual `slippage_bps` |
| `fees_total` | sum of simulated/actual fees |
| `capacity_estimate` | maximum capital where estimated slippage remains below configured threshold |
| `feature_leakage_flag` | `1` if feature timestamp > decision timestamp or adjusted data leakage detected |

## 16. Forbidden actions

- Запрещено писать simulated trades as live trades.
- Запрещено менять active weights automatically.
- Запрещено использовать future data.
- Запрещено отправлять ArenaGo orders.
