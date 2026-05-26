BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.trade_fact AS
SELECT
  er.execution_result_id,
  er.order_intent_id,
  oi.decision_set_id,
  oi.risk_check_id,
  dr.decision_record_id,
  oi.instrument_id,
  oi.side,
  CASE WHEN oi.side = 'buy' THEN 1 ELSE -1 END AS side_sign,
  er.status AS execution_status,
  oi.run_mode,
  COALESCE(er.payload ->> 'system_mode', oi.payload ->> 'system_mode') AS system_mode,
  COALESCE(er.payload ->> 'route', 'unknown') AS route,
  oi.quantity AS requested_quantity,
  er.filled_quantity,
  CASE
    WHEN oi.quantity IS NOT NULL AND oi.quantity <> 0 AND er.filled_quantity IS NOT NULL
      THEN er.filled_quantity / NULLIF(oi.quantity, 0)
    ELSE NULL
  END AS fill_ratio,
  er.avg_fill_price,
  ABS(COALESCE(er.filled_quantity, 0) * COALESCE(er.avg_fill_price, 0)) AS turnover_rub,
  COALESCE(er.fees, 0) AS fees,
  er.slippage_bps,
  dr.action AS decision_action,
  dr.expected_edge_score,
  (oi.payload #>> '{risk_metrics,expected_edge_after_cost_score}')::numeric AS expected_edge_after_cost_score,
  dr.risk_score,
  dr.primary_reason_codes,
  oi.payload ->> 'position_effect' AS position_effect,
  oi.payload #> '{risk_metrics}' AS risk_metrics,
  er.errors,
  er.submitted_at,
  er.last_update_at,
  oi.created_at AS order_created_at,
  COALESCE(er.last_update_at, er.submitted_at, oi.created_at) AS trade_ts
FROM orders.execution_result er
JOIN orders.order_intent oi
  ON oi.order_intent_id = er.order_intent_id
LEFT JOIN LATERAL (
  SELECT record.*
  FROM decisions.decision_record record
  WHERE record.decision_set_id = oi.decision_set_id
    AND record.instrument_id = oi.instrument_id
  ORDER BY record.created_at DESC
  LIMIT 1
) dr ON true;

CREATE OR REPLACE VIEW analytics.performance_daily AS
WITH trade_daily AS (
  SELECT
    (timezone('Europe/Moscow', trade_ts))::date AS trading_date,
    COUNT(*) AS execution_count,
    COUNT(*) FILTER (WHERE execution_status IN ('filled', 'partially_filled')) AS filled_execution_count,
    COUNT(*) FILTER (WHERE execution_status = 'rejected') AS rejected_execution_count,
    COUNT(*) FILTER (WHERE side = 'buy') AS buy_execution_count,
    COUNT(*) FILTER (WHERE side = 'sell') AS sell_execution_count,
    COALESCE(SUM(turnover_rub), 0) AS gross_turnover_rub,
    COALESCE(SUM(turnover_rub) FILTER (WHERE execution_status IN ('filled', 'partially_filled')), 0) AS filled_turnover_rub,
    COALESCE(SUM(fees), 0) AS fees_rub,
    AVG(slippage_bps) FILTER (WHERE slippage_bps IS NOT NULL) AS avg_slippage_bps,
    AVG(expected_edge_after_cost_score) FILTER (WHERE expected_edge_after_cost_score IS NOT NULL) AS avg_expected_edge_after_cost_score
  FROM analytics.trade_fact
  WHERE trade_ts IS NOT NULL
  GROUP BY (timezone('Europe/Moscow', trade_ts))::date
),
portfolio_daily AS (
  SELECT DISTINCT ON ((timezone('Europe/Moscow', as_of_ts))::date)
    (timezone('Europe/Moscow', as_of_ts))::date AS trading_date,
    portfolio_id,
    cash,
    equity,
    gross_exposure,
    net_exposure,
    realized_pnl,
    unrealized_pnl,
    as_of_ts AS latest_portfolio_ts
  FROM portfolio.portfolio_snapshot
  ORDER BY (timezone('Europe/Moscow', as_of_ts))::date, as_of_ts DESC, created_at DESC
),
all_dates AS (
  SELECT trading_date FROM trade_daily
  UNION
  SELECT trading_date FROM portfolio_daily
)
SELECT
  dates.trading_date,
  portfolio_daily.portfolio_id,
  portfolio_daily.cash,
  portfolio_daily.equity,
  portfolio_daily.gross_exposure,
  portfolio_daily.net_exposure,
  portfolio_daily.realized_pnl,
  portfolio_daily.unrealized_pnl,
  portfolio_daily.latest_portfolio_ts,
  COALESCE(trade_daily.execution_count, 0) AS execution_count,
  COALESCE(trade_daily.filled_execution_count, 0) AS filled_execution_count,
  COALESCE(trade_daily.rejected_execution_count, 0) AS rejected_execution_count,
  COALESCE(trade_daily.buy_execution_count, 0) AS buy_execution_count,
  COALESCE(trade_daily.sell_execution_count, 0) AS sell_execution_count,
  COALESCE(trade_daily.gross_turnover_rub, 0) AS gross_turnover_rub,
  COALESCE(trade_daily.filled_turnover_rub, 0) AS filled_turnover_rub,
  COALESCE(trade_daily.fees_rub, 0) AS fees_rub,
  trade_daily.avg_slippage_bps,
  trade_daily.avg_expected_edge_after_cost_score,
  CASE
    WHEN trade_daily.execution_count > 0
      THEN trade_daily.filled_execution_count::numeric / trade_daily.execution_count
    ELSE NULL
  END AS fill_success_ratio
FROM all_dates dates
LEFT JOIN trade_daily USING (trading_date)
LEFT JOIN portfolio_daily USING (trading_date);

CREATE OR REPLACE VIEW analytics.decision_outcome AS
WITH decision_base AS (
  SELECT
    dr.decision_record_id,
    dr.decision_set_id,
    dr.instrument_id,
    dr.action,
    dr.expected_edge_score,
    (de.explanation #>> '{decision_payload,expected_edge_after_cost_score}')::numeric AS expected_edge_after_cost_score,
    dr.risk_score,
    dr.primary_reason_codes,
    dr.created_at
  FROM decisions.decision_record dr
  LEFT JOIN decisions.decision_explanation de
    ON de.decision_record_id = dr.decision_record_id
)
SELECT
  base.*,
  entry.close_ts AS entry_ts,
  entry.close_price AS entry_price,
  candle_5m.close_ts AS outcome_5m_ts,
  candle_5m.close_price AS outcome_5m_price,
  CASE WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0
    THEN (candle_5m.close_price - entry.close_price) / entry.close_price
  END AS return_5m,
  candle_30m.close_ts AS outcome_30m_ts,
  candle_30m.close_price AS outcome_30m_price,
  CASE WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0
    THEN (candle_30m.close_price - entry.close_price) / entry.close_price
  END AS return_30m,
  candle_1h.close_ts AS outcome_1h_ts,
  candle_1h.close_price AS outcome_1h_price,
  CASE WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0
    THEN (candle_1h.close_price - entry.close_price) / entry.close_price
  END AS return_1h,
  candle_1d.close_ts AS outcome_1d_ts,
  candle_1d.close_price AS outcome_1d_price,
  CASE WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0
    THEN (candle_1d.close_price - entry.close_price) / entry.close_price
  END AS return_1d,
  CASE
    WHEN base.action = 'buy' AND entry.close_price IS NOT NULL AND candle_30m.close_price IS NOT NULL
      THEN candle_30m.close_price > entry.close_price
    WHEN base.action IN ('sell', 'reduce') AND entry.close_price IS NOT NULL AND candle_30m.close_price IS NOT NULL
      THEN candle_30m.close_price < entry.close_price
    ELSE NULL
  END AS direction_correct_30m
FROM decision_base base
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = base.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= base.created_at
  ORDER BY candle.close_ts
  LIMIT 1
) entry ON true
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = base.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= base.created_at + interval '5 minutes'
  ORDER BY candle.close_ts
  LIMIT 1
) candle_5m ON true
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = base.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= base.created_at + interval '30 minutes'
  ORDER BY candle.close_ts
  LIMIT 1
) candle_30m ON true
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = base.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= base.created_at + interval '1 hour'
  ORDER BY candle.close_ts
  LIMIT 1
) candle_1h ON true
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = base.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= base.created_at + interval '1 day'
  ORDER BY candle.close_ts
  LIMIT 1
) candle_1d ON true;

CREATE OR REPLACE VIEW analytics.feature_contribution AS
SELECT
  contribution.key AS metric_name,
  outcome.action,
  COUNT(*) AS decision_count,
  AVG((contribution.value)::numeric) AS avg_contribution,
  AVG(ABS((contribution.value)::numeric)) AS avg_abs_contribution,
  AVG(outcome.expected_edge_score) AS avg_expected_edge_score,
  AVG(outcome.expected_edge_after_cost_score) AS avg_expected_edge_after_cost_score,
  AVG(outcome.return_30m) FILTER (WHERE outcome.return_30m IS NOT NULL) AS avg_return_30m,
  AVG(CASE WHEN outcome.direction_correct_30m THEN 1.0 ELSE 0.0 END)
    FILTER (WHERE outcome.direction_correct_30m IS NOT NULL) AS direction_accuracy_30m
FROM decisions.decision_record record
JOIN analytics.decision_outcome outcome
  ON outcome.decision_record_id = record.decision_record_id
CROSS JOIN LATERAL jsonb_each_text(record.feature_contributions) AS contribution(key, value)
WHERE contribution.value ~ '^-?[0-9]+(\.[0-9]+)?([eE]-?[0-9]+)?$'
GROUP BY contribution.key, outcome.action;

CREATE OR REPLACE VIEW analytics.llm_quality AS
SELECT
  (timezone('Europe/Moscow', created_at))::date AS observed_date,
  caller_module,
  provider,
  request_type,
  status,
  COUNT(*) AS request_count,
  AVG(latency_ms) AS avg_latency_ms,
  MAX(latency_ms) AS max_latency_ms,
  COALESCE(SUM(cost_units), 0) AS cost_units,
  COUNT(*) FILTER (WHERE status = 'success')::numeric / NULLIF(COUNT(*), 0) AS success_ratio,
  COUNT(*) FILTER (WHERE status = 'timeout')::numeric / NULLIF(COUNT(*), 0) AS timeout_ratio,
  COUNT(*) FILTER (WHERE status = 'failed')::numeric / NULLIF(COUNT(*), 0) AS failed_ratio,
  MAX(created_at) AS last_request_at
FROM request_logs.external_request_log
WHERE provider = 'polza_ai'
   OR request_type = 'llm_completion'
GROUP BY (timezone('Europe/Moscow', created_at))::date, caller_module, provider, request_type, status;

CREATE OR REPLACE VIEW analytics.risk_gate_effectiveness AS
WITH decision_reasons AS (
  SELECT
    dr.decision_record_id,
    dr.decision_set_id,
    dr.instrument_id,
    dr.action,
    dr.expected_edge_score,
    dr.risk_score,
    dr.created_at,
    unnest(dr.primary_reason_codes) AS reason_code
  FROM decisions.decision_record dr
),
trade_by_decision AS (
  SELECT
    decision_record_id,
    COUNT(*) AS execution_count,
    COUNT(*) FILTER (WHERE execution_status IN ('filled', 'partially_filled')) AS filled_execution_count,
    COALESCE(SUM(turnover_rub), 0) AS turnover_rub,
    AVG(slippage_bps) FILTER (WHERE slippage_bps IS NOT NULL) AS avg_slippage_bps
  FROM analytics.trade_fact
  WHERE decision_record_id IS NOT NULL
  GROUP BY decision_record_id
)
SELECT
  reason.reason_code,
  reason.action,
  COUNT(*) AS decision_count,
  AVG(reason.expected_edge_score) AS avg_expected_edge_score,
  AVG(reason.risk_score) AS avg_risk_score,
  COALESCE(SUM(trade.execution_count), 0) AS execution_count,
  COALESCE(SUM(trade.filled_execution_count), 0) AS filled_execution_count,
  COALESCE(SUM(trade.turnover_rub), 0) AS turnover_rub,
  AVG(trade.avg_slippage_bps) FILTER (WHERE trade.avg_slippage_bps IS NOT NULL) AS avg_slippage_bps
FROM decision_reasons reason
LEFT JOIN trade_by_decision trade
  ON trade.decision_record_id = reason.decision_record_id
GROUP BY reason.reason_code, reason.action;

INSERT INTO audit.audit_record (
  module_name, job_id, severity, event_type, message,
  object_type, object_ref, reason_codes, payload
)
SELECT
  'Monitoring & Audit Module',
  'migration_032_analytics_performance_views',
  'info',
  'analytics_performance_views_created',
  'Read-only analytics views were added for trade facts, daily performance, decision outcomes, feature contribution, LLM quality and risk gate effectiveness.',
  'migration',
  '032_analytics_performance_views',
  ARRAY['analytics_read_only', 'decision_replay_support', 'weight_research_support'],
  jsonb_build_object(
    'schema', 'analytics',
    'views', ARRAY[
      'trade_fact',
      'performance_daily',
      'decision_outcome',
      'feature_contribution',
      'llm_quality',
      'risk_gate_effectiveness'
    ]
  )
WHERE NOT EXISTS (
  SELECT 1
  FROM audit.audit_record
  WHERE event_type = 'analytics_performance_views_created'
    AND object_ref = '032_analytics_performance_views'
);

COMMIT;
