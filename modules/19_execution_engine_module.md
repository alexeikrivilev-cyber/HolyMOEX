# Execution Engine Module — Исполнение сделок

## 1. Назначение

Преобразует утверждённые `order_intent` в заявки брокера или paper-trading заявки, управляет их статусом, отменой, TTL, fills и slippage.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Execution Engine Module` |
| `module_type` | `execution` |
| `primary_contour` | `execution_contour` |
| `secondary_contours` | `realtime_contour` |
| `execution_mode` | `broker_order_manager` |
| `llm_usage` | `none` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `input_refs`
- `run_mode`
- `idempotency_key`

### Trigger policy

Запускается только после `risk_check_result.status=approved | approved_with_changes` и только для `paper_trading` или `live_trading`.

## 4. Input classification

- `order_intent`
- `risk_check_result`
- `instrument_profile`
- `market_session_status`
- `portfolio_snapshot`
- `execution_policy`

## 5. Input contract

```json
{
  "execution_request": {
    "order_intent_refs": [
      "string"
    ],
    "market_session_status_ref": "string",
    "execution_policy_id": "string",
    "run_mode": "paper_trading | live_trading",
    "idempotency_key": "string"
  }
}
```

## 6. External requests

В live mode отправляет broker orders только через Gateway с `request_type=orders`. В paper mode не обращается к broker order endpoint.

## 7. Processing rules

- `validate_risk_approval`
- `validate_market_session`
- `validate_order_ttl`
- `validate_instrument_tradable`
- `select_execution_route`
- `submit_order_or_simulate`
- `track_order_status`
- `handle_partial_fills`
- `cancel_expired_orders`
- `compute_execution_metrics`
- `write_execution_result`
- `notify_portfolio_state_module`

## 8. Output classification

- `execution_result`
- `order_status`
- `fill_report`
- `execution_audit_record`

## 9. Output contract

```json
{
  "execution_result": "execution_result"
}
```

## 10. Metrics / Records

- `submitted_order_count`
- `fill_ratio`
- `avg_fill_price`
- `slippage_bps`
- `fees`
- `time_to_fill_ms`
- `cancelled_order_count`
- `rejected_order_count`
- `partial_fill_ratio`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Order Store` |
| `read` | `Selected Instruments DB` |
| `read` | `Risk Store` |
| `read` | `Portfolio State Store` |
| `write` | `Order Store` |
| `write` | `Execution Log Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

`order_intent` исполняется только до `execution_ttl_seconds`. Order status обновляется до terminal state.

## 13. Failure policy

При broker error не повторяет заявку без проверки `idempotency_key`. При истечении TTL отменяет/помечает order as expired.

## 14. Acceptance criteria

- `no_execution_without_risk_approval`
- `broker_calls_only_via_gateway`
- `idempotency_enforced`
- `order_ttl_enforced`
- `fills_written`
- `portfolio_update_triggered`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `submitted_order_count` | count of accepted submit attempts over window |
| `fill_ratio` | `filled_quantity / submitted_quantity` |
| `avg_fill_price` | `sum(fill_price * fill_quantity) / sum(fill_quantity)` |
| `slippage_bps` | for buy: `(avg_fill_price - reference_price)/reference_price*10000`; for sell: `(reference_price - avg_fill_price)/reference_price*10000` |
| `fees` | provider-reported fees or configured fee model estimate |
| `time_to_fill_ms` | `last_fill_at_ms - submitted_at_ms` |
| `cancelled_order_count` | count of cancelled/expired orders over window |
| `rejected_order_count` | count of provider/risk rejected orders over window |
| `partial_fill_ratio` | `partially_filled_orders / submitted_orders` |

## 16. ArenaGo execution mapping

`order_intent.side` maps to ArenaGo `direction`:

| `order_intent.side` | ArenaGo `direction` |
|---|---|
| `buy` | `B` |
| `sell` | `S` |

`instrument_id` maps to ArenaGo `secid` through `instrument_profile.arena_go_secid`.

`order_intent.quantity` maps to ArenaGo `quantity` after applying `arena_go_quantity_mode` and `lot_size`.

Gateway request:

```json
{
  "provider": "arena_go",
  "request_type": "submit_order",
  "payload": {
    "direction": "B | S",
    "secid": "string",
    "quantity": 0,
    "bot": "string"
  }
}
```

## 17. Forbidden actions

- Запрещено создавать торговое решение.
- Запрещено исполнять without approved `risk_check_result`.
- Запрещено напрямую вызывать ArenaGo HTTP API, минуя Gateway.
- Запрещено повторно отправлять identical order without idempotency check.
- Запрещено исполнять if `RUN_MODE=analysis_only`.

## 18. ArenaGo error handling

| `error_code` | Required module behavior |
|---|---|
| `market_closed` | mark `execution_result.status=rejected`; do not retry until next allowed session |
| `invalid_instrument` | mark rejected; emit alert; request registry validation |
| `insufficient_cash` | mark rejected; request immediate portfolio refresh |
| `daily_trade_limit_reached` | enable bot execution block for current day |
| `provider_timeout` | retry only if idempotency check confirms order was not submitted |

## 19. Output mapping from ArenaGo success

ArenaGo success response must be mapped into `execution_result` and `portfolio_update_hint`:

```json
{
  "execution_result": {
    "status": "submitted | filled",
    "filled_quantity": 0,
    "avg_fill_price": 0.0,
    "errors": []
  },
  "portfolio_update_hint": {
    "remaining_cash": 0.0,
    "provider_price": 0.0,
    "provider_quantity": 0
  }
}
```
