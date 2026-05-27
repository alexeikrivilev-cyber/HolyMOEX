# Live Validation Analytics

Этот слой добавляет только read-only SQL views в schema `analytics`. Он не меняет runtime-логику торговли, Risk Control, Decision Engine scoring, Execution Engine или веса.

## Views

| View | Назначение |
|---|---|
| `analytics.decision_fact` | Нормализованный слой по `decision_record` + payload из `decision_set.decisions`. |
| `analytics.decision_outcome_30m` | Forward outcome через 30 минут, включая action-aligned return и связку с risk/execution. |
| `analytics.decision_outcome_60m` | То же для горизонта 60 минут. |
| `analytics.instrument_live_stats` | Live-качество по инструментам: accuracy, churn, turnover, calibration, status hint. |
| `analytics.churn_round_trips` | Быстрые flip-flop сделки по одному инструменту в пределах 15 минут. |
| `analytics.guardrail_rejections` | Какие Profitability Guardrails отрезают решения. |
| `analytics.edge_calibration` | Сравнение `expected_edge_after_cost_bps` с realized action-aligned return. |
| `analytics.pnl_by_reason_code` | Какие reason codes связаны с прибылью/убытком. |
| `analytics.execution_cost_realized` | Сравнение ожидаемых execution costs с фактическими fill/slippage. |
| `analytics.feature_outcome_attribution` | Связь feature contributions с последующим outcome. |
| `analytics.live_dashboard_summary` | Однострочный live-summary состояния агента. |

## Быстрые запросы

Общий статус:

```sql
SELECT *
  FROM analytics.live_dashboard_summary;
```

Проблемные инструменты:

```sql
SELECT instrument_id, status_hint, executed_count, buy_accuracy_30m,
       avg_action_aligned_return_30m, edge_calibration_error_bps,
       churn_flip_count, turnover_rub
  FROM analytics.instrument_live_stats
 ORDER BY
       CASE status_hint
         WHEN 'quarantine_candidate' THEN 0
         WHEN 'watchlist_candidate' THEN 1
         ELSE 2
       END,
       edge_calibration_error_bps ASC NULLS LAST;
```

Churn / flip-flop:

```sql
SELECT *
  FROM analytics.churn_round_trips
 ORDER BY second_ts DESC
 LIMIT 50;
```

Guardrail rejects:

```sql
SELECT rejection_reason_code, count(*) AS rejects
  FROM analytics.guardrail_rejections
 GROUP BY rejection_reason_code
 ORDER BY rejects DESC;
```

Edge calibration:

```sql
SELECT edge_bucket,
       count(*) AS decisions,
       avg(expected_edge_after_cost_bps) AS expected_bps,
       avg(realized_action_aligned_return_30m_bps) AS realized_30m_bps,
       avg(calibration_error_30m_bps) AS calibration_error_30m_bps
  FROM analytics.edge_calibration
 GROUP BY edge_bucket
 ORDER BY edge_bucket;
```

Reason codes:

```sql
SELECT reason_code, decisions_count, executed_count, rejected_count,
       avg_expected_edge_after_cost_bps,
       avg_action_aligned_return_30m_bps,
       win_rate_30m,
       turnover_rub
  FROM analytics.pnl_by_reason_code
 ORDER BY avg_action_aligned_return_30m_bps ASC NULLS LAST;
```

Important: `analytics.pnl_by_reason_code` intentionally expands one decision into multiple rows when it has several reason codes. Use it for attribution by reason code, not as a total PnL/turnover source. Filter by `run_mode` and `portfolio_id` when comparing live, paper, or multiple portfolios.

Execution cost error:

```sql
SELECT instrument_id, side, expected_execution_cost_bps,
       realized_cost_bps, cost_error_bps, filled_notional
  FROM analytics.execution_cost_realized
 ORDER BY order_ts DESC NULLS LAST
 LIMIT 50;
```

## Как читать метрики

`buy_accuracy_30m`: доля buy-решений, после которых цена через 30 минут была выше цены решения. Низкое значение при `executed_count >= 5` указывает на кандидата в quarantine.

`churn_flip_count`: количество быстрых противоположных сделок по инструменту. Рост этого числа означает, что агент платит spread/slippage за развороты вместо удержания edge.

`guardrail_rejection_rate`: доля решений, отрезанных Risk Control. Высокое значение после внедрения guardrails нормально, если одновременно падает churn и улучшается realized outcome.

`edge_calibration_error_bps`: `realized_action_aligned_return_bps - expected_edge_after_cost_bps`. Отрицательное значение означает, что модель переоценивает edge.

`quarantine_candidate`: аналитический сигнал, а не runtime-блокировка. Он показывает инструменты, которые стоит вручную проверить перед изменением guardrail/quarantine policy.
