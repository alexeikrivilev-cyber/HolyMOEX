# Risk Control Module — Контроль риска

## 1. Назначение

Проверяет `decision_set` до исполнения: лимиты на инструмент, портфель, день, ликвидность, проскальзывание, drawdown, stale data, market regime и kill switches.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Risk Control Module` |
| `module_type` | `risk` |
| `primary_contour` | `decision_contour` |
| `secondary_contours` | `execution_contour` |
| `execution_mode` | `rules + limits + stress_checks` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `input_refs`
- `run_mode`
- `config_ref`

### Trigger policy

Запускается для каждого `decision_set`, перед созданием `order_intent` и при обновлении kill switch/policy.

## 4. Input classification

- `decision_set`
- `risk_policy`
- `portfolio_snapshot`
- `position_state`
- `market_state_record`
- `liquidity_features`
- `data_quality_report`

## 5. Input contract

```json
{
  "risk_check_request": {
    "decision_set_id": "string",
    "portfolio_state_ref": "string",
    "risk_policy_id": "string",
    "market_state_ref": "string",
    "data_quality_report_ref": "string",
    "run_mode": "paper_trading | live_trading | analysis_only"
  }
}
```

## 6. External requests

Не делает внешних запросов. Если требуется свежий portfolio/order state, запрашивает его через `Portfolio State Module`, а не напрямую.

## 7. Processing rules

- `check_global_kill_switch`
- `check_run_mode`
- `check_data_quality`
- `check_position_limits`
- `check_portfolio_exposure`
- `check_daily_loss_limit`
- `check_drawdown_limit`
- `check_liquidity_constraints`
- `check_slippage_limits`
- `check_market_regime_blocks`
- `adjust_or_reject_decisions`
- `create_approved_order_intents`

## 8. Output classification

- `risk_check_result`
- `order_intent`
- `risk_event`

## 9. Output contract

```json
{
  "risk_check_result": "risk_check_result"
}
```

## 10. Metrics / Records

- `portfolio_exposure_after_trade`
- `instrument_exposure_after_trade`
- `daily_loss_usage`
- `drawdown_usage`
- `liquidity_limit_usage`
- `slippage_limit_usage`
- `risk_budget_usage`
- `risk_rejection_count`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Decision Store` |
| `read` | `Risk Policy Store` |
| `read` | `Portfolio State Store` |
| `read` | `Feature Store` |
| `write` | `Risk Store` |
| `write` | `Order Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

Risk check expires quickly: before execution and no longer than liquidity/portfolio snapshot TTL.

## 13. Failure policy

При любой critical неопределённости возвращает `rejected` или `manual_review_required`, а не `approved`.

## 14. Acceptance criteria

- `risk_can_block_any_decision`
- `approved_orders_have_risk_check_id`
- `kill_switch_enforced`
- `stale_portfolio_blocks_execution`
- `liquidity_limits_checked`
- `risk_adjustments_logged`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `portfolio_exposure_after_trade` | `(current_gross_exposure + proposed_trade_value) / portfolio_equity` |
| `instrument_exposure_after_trade` | `(current_instrument_value + proposed_trade_value) / portfolio_equity` |
| `daily_loss_usage` | `abs(min(daily_pnl,0)) / max_daily_loss_limit` |
| `drawdown_usage` | `abs(current_drawdown) / max_drawdown_limit` |
| `liquidity_limit_usage` | `estimated_order_slippage_bps / max_allowed_slippage_bps` |
| `slippage_limit_usage` | `estimated_slippage_bps / order_intent.max_slippage_bps` |
| `risk_budget_usage` | `used_risk_budget / total_risk_budget` |
| `risk_rejection_count` | count of rejected decisions/orders over window |

## 16. ArenaGo-specific risk checks

- Check `remaining_cash` and `cash_balance` before buy orders.
- Check `ARENA_GO_DAILY_TRADE_LIMIT=1000` against daily submitted trade/order count.
- Reject if `market_session_status != open` or ArenaGo returns `market_closed`.
- Reject if `arena_go_secid` is missing or provider returns `invalid_instrument`.

## 17. Forbidden actions

- Запрещено увеличивать position size above policy.
- Запрещено отправлять заявки напрямую.
- Запрещено менять Decision Engine output except through `risk_check_result.adjustments`.
- Запрещено игнорировать `global_kill_switch`.
- Запрещено approve order without fresh portfolio snapshot.

## 18. Mandatory pre-trade checks

Every approved `order_intent` must pass:

- `cash_check`
- `position_limit_check`
- `instrument_limit_check`
- `sector_limit_check`
- `daily_loss_limit_check`
- `drawdown_limit_check`
- `liquidity_check`
- `slippage_check`
- `market_session_check`
- `arena_go_daily_trade_limit_check`
- `portfolio_snapshot_freshness_check`
- `global_kill_switch_check`

## Live autonomous turnover guardrails

`Risk Control Module` owns the hard safety boundary for the turnover mandate. `target_gross_turnover_rub_14d = 10000000` is not permission to churn. Live orders are approved only if risk policy `risk_policy:live_autonomous_turnover:v1` allows them after checking market session, data freshness, portfolio freshness, exposure, cash, daily loss, drawdown, spread/slippage, expected edge after costs, daily turnover limit and ArenaGo constraints.

## Runtime hardening note v5

`max_daily_loss_rub` and `max_daily_loss_pct` are distinct. If only percentage loss is configured, `Risk Control Module` converts it to RUB using the latest `portfolio_snapshot.equity` / `cash` / `initial_capital_rub`. A ratio such as `0.02` must never be interpreted as a two-kopeck absolute loss limit.

For turnover-driven decisions, the authoritative profitability gate is `expected_edge_after_cost_score`. If Decision Engine supplies the value, Risk Control uses it directly. Otherwise Risk Control computes a conservative signed proxy from `expected_edge_score` and spread/slippage/commission cost features: costs reduce positive long edge toward zero and reduce negative short edge toward zero. Orders with `turnover_mandate_urgency` must still pass `min_expected_edge_after_cost_score` and all liquidity/risk gates.

Risk Control does not infer short selling from an ambiguous `sell`. It receives or recomputes `position_effect` and treats `sell` from a zero/short position as `open_short`/`increase_short` only when both `DECISION_ALLOW_SHORT_SELLING` and `ARENA_GO_SHORTS_ALLOWED` are enabled. If short capability is disabled or unknown, new/increased shorts are rejected with `short_selling_not_supported`; sell-to-reduce-long and buy-to-cover-short remain risk-reducing actions. Short exposure is governed separately by `max_short_position_pct`, `max_total_short_exposure_pct` and `max_single_short_order_value_rub`.

When several risk-increasing candidates compete for the same cycle limit, Risk Control ranks long and short entries together by absolute post-cost edge strength after putting risk-reducing exits first. This prevents weak long entries from consuming the cycle budget before stronger `open_short` candidates are assessed.
