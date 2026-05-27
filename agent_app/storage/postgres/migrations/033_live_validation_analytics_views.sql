BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.decision_fact AS
SELECT
  dr.decision_record_id AS decision_id,
  dr.decision_record_id,
  dr.decision_set_id,
  ds.decision_request_id,
  ds.horizon,
  dr.instrument_id,
  dr.action,
  dr.created_at AS decision_ts,
  req.run_mode,
  req.universe_id,
  req.weights_profile_id,
  dr.target_position_pct,
  dr.target_quantity,
  dr.confidence_score,
  dr.expected_edge_score,
  dr.expected_edge_score AS decision_score,
  dr.risk_score,
  dr.primary_reason_codes,
  COALESCE(payload_decision.payload, '{}'::jsonb) AS decision_payload,
  COALESCE(
    ARRAY(
      SELECT DISTINCT reason_code
      FROM (
        SELECT unnest(dr.primary_reason_codes) AS reason_code
        UNION ALL
        SELECT jsonb_array_elements_text(
          CASE
            WHEN jsonb_typeof(payload_decision.payload -> 'reason_codes') = 'array'
              THEN payload_decision.payload -> 'reason_codes'
            ELSE '[]'::jsonb
          END
        )
        UNION ALL
        SELECT jsonb_array_elements_text(
          CASE
            WHEN jsonb_typeof(payload_decision.payload -> 'cost_model_reason_codes') = 'array'
              THEN payload_decision.payload -> 'cost_model_reason_codes'
            ELSE '[]'::jsonb
          END
        )
      ) reason_source
      WHERE reason_code IS NOT NULL AND reason_code <> ''
    ),
    dr.primary_reason_codes
  ) AS reason_codes,
  CASE WHEN payload_decision.payload ->> 'raw_expected_edge_bps' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (payload_decision.payload ->> 'raw_expected_edge_bps')::numeric
  END AS raw_expected_edge_bps,
  CASE WHEN payload_decision.payload ->> 'expected_edge_after_cost_bps' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (payload_decision.payload ->> 'expected_edge_after_cost_bps')::numeric
  END AS expected_edge_after_cost_bps,
  CASE WHEN payload_decision.payload ->> 'expected_edge_after_cost_score' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (payload_decision.payload ->> 'expected_edge_after_cost_score')::numeric
  END AS expected_edge_after_cost_score,
  CASE WHEN payload_decision.payload ->> 'execution_cost_estimate_bps' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (payload_decision.payload ->> 'execution_cost_estimate_bps')::numeric
  END AS execution_cost_estimate_bps,
  CASE WHEN payload_decision.payload ->> 'edge_to_cost_ratio' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (payload_decision.payload ->> 'edge_to_cost_ratio')::numeric
  END AS edge_to_cost_ratio,
  payload_decision.payload -> 'cost_components' AS cost_components,
  payload_decision.payload ->> 'cost_model_version' AS cost_model_version,
  payload_decision.payload ->> 'cost_model_quality' AS cost_model_quality,
  CASE WHEN payload_decision.payload ->> 'planned_order_notional' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (payload_decision.payload ->> 'planned_order_notional')::numeric
  END AS planned_order_notional,
  payload_decision.payload ->> 'position_effect' AS position_effect,
  dr.feature_contributions,
  de.explanation
FROM decisions.decision_record dr
JOIN decisions.decision_set ds
  ON ds.decision_set_id = dr.decision_set_id
LEFT JOIN decisions.decision_request req
  ON req.decision_request_id = ds.decision_request_id
LEFT JOIN decisions.decision_explanation de
  ON de.decision_record_id = dr.decision_record_id
LEFT JOIN LATERAL (
  SELECT item AS payload
  FROM jsonb_array_elements(ds.decisions) item
  WHERE item ->> 'instrument_id' = dr.instrument_id
  ORDER BY
    CASE WHEN item ->> 'action' = dr.action THEN 0 ELSE 1 END,
    item ->> 'instrument_id'
  LIMIT 1
) payload_decision ON true;

CREATE OR REPLACE VIEW analytics.decision_outcome_30m AS
SELECT
  decision.decision_id,
  decision.decision_set_id,
  decision.instrument_id,
  decision.horizon,
  decision.action,
  decision.decision_ts,
  entry.close_price AS price_at_decision,
  outcome.close_price AS price_30m,
  CASE
    WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0 AND outcome.close_price IS NOT NULL
      THEN (outcome.close_price / NULLIF(entry.close_price, 0)) - 1
    ELSE NULL
  END AS raw_forward_return_30m,
  CASE
    WHEN entry.close_price IS NULL OR entry.close_price = 0 OR outcome.close_price IS NULL THEN NULL
    WHEN decision.action IN ('sell', 'reduce', 'close')
      THEN (entry.close_price / NULLIF(outcome.close_price, 0)) - 1
    ELSE (outcome.close_price / NULLIF(entry.close_price, 0)) - 1
  END AS action_aligned_forward_return_30m,
  CASE
    WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0 AND outcome.close_price IS NOT NULL
      THEN ((outcome.close_price / NULLIF(entry.close_price, 0)) - 1) * 10000
    ELSE NULL
  END AS raw_forward_return_30m_bps,
  CASE
    WHEN entry.close_price IS NULL OR entry.close_price = 0 OR outcome.close_price IS NULL THEN NULL
    WHEN decision.action IN ('sell', 'reduce', 'close')
      THEN ((entry.close_price / NULLIF(outcome.close_price, 0)) - 1) * 10000
    ELSE ((outcome.close_price / NULLIF(entry.close_price, 0)) - 1) * 10000
  END AS action_aligned_forward_return_30m_bps,
  decision.expected_edge_after_cost_bps,
  decision.execution_cost_estimate_bps,
  decision.edge_to_cost_ratio,
  decision.expected_edge_score,
  decision.decision_score,
  decision.risk_score,
  decision.reason_codes,
  decision.cost_model_quality,
  (exec.execution_result_id IS NOT NULL AND exec.status IN ('filled', 'partially_filled')) AS was_executed,
  (risk.risk_check_id IS NOT NULL AND (
    risk.status = 'rejected'
    OR risk.rejected_decisions @> ARRAY['decisions.decision_set:' || decision.decision_set_id || ':' || decision.instrument_id]
  )) AS was_rejected_by_risk,
  COALESCE(risk.risk_flags, '{}'::text[]) AS risk_flags,
  ord.order_intent_id,
  exec.execution_result_id,
  exec.filled_quantity,
  ABS(COALESCE(exec.filled_quantity, 0) * COALESCE(exec.avg_fill_price, 0)) AS filled_notional,
  NULL::numeric AS realized_pnl_rub,
  decision.run_mode,
  COALESCE(ord.payload ->> 'portfolio_id', exec.payload ->> 'portfolio_id') AS portfolio_id,
  COALESCE(ord.payload ->> 'account_id', exec.payload ->> 'account_id') AS account_id,
  COALESCE(ord.payload ->> 'strategy_id', decision.weights_profile_id) AS strategy_id,
  (COALESCE(ord.payload ->> 'portfolio_id', exec.payload ->> 'portfolio_id') IS NULL) AS portfolio_scope_warning
FROM analytics.decision_fact decision
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = decision.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts <= decision.decision_ts
  ORDER BY candle.close_ts DESC
  LIMIT 1
) entry ON true
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = decision.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= decision.decision_ts + interval '30 minutes'
  ORDER BY candle.close_ts
  LIMIT 1
) outcome ON true
LEFT JOIN LATERAL (
  SELECT oi.*
  FROM orders.order_intent oi
  WHERE oi.decision_set_id = decision.decision_set_id
    AND oi.instrument_id = decision.instrument_id
  ORDER BY oi.created_at DESC
  LIMIT 1
) ord ON true
LEFT JOIN LATERAL (
  SELECT er.*
  FROM orders.execution_result er
  WHERE er.order_intent_id = ord.order_intent_id
  ORDER BY COALESCE(er.last_update_at, er.submitted_at) DESC NULLS LAST
  LIMIT 1
) exec ON true
LEFT JOIN LATERAL (
  SELECT rcr.*
  FROM risk.risk_check_result rcr
  WHERE rcr.decision_set_id = decision.decision_set_id
  ORDER BY rcr.checked_at DESC
  LIMIT 1
) risk ON true;

CREATE OR REPLACE VIEW analytics.decision_outcome_60m AS
SELECT
  decision.decision_id,
  decision.decision_set_id,
  decision.instrument_id,
  decision.horizon,
  decision.action,
  decision.decision_ts,
  entry.close_price AS price_at_decision,
  outcome.close_price AS price_60m,
  CASE
    WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0 AND outcome.close_price IS NOT NULL
      THEN (outcome.close_price / NULLIF(entry.close_price, 0)) - 1
    ELSE NULL
  END AS raw_forward_return_60m,
  CASE
    WHEN entry.close_price IS NULL OR entry.close_price = 0 OR outcome.close_price IS NULL THEN NULL
    WHEN decision.action IN ('sell', 'reduce', 'close')
      THEN (entry.close_price / NULLIF(outcome.close_price, 0)) - 1
    ELSE (outcome.close_price / NULLIF(entry.close_price, 0)) - 1
  END AS action_aligned_forward_return_60m,
  CASE
    WHEN entry.close_price IS NOT NULL AND entry.close_price <> 0 AND outcome.close_price IS NOT NULL
      THEN ((outcome.close_price / NULLIF(entry.close_price, 0)) - 1) * 10000
    ELSE NULL
  END AS raw_forward_return_60m_bps,
  CASE
    WHEN entry.close_price IS NULL OR entry.close_price = 0 OR outcome.close_price IS NULL THEN NULL
    WHEN decision.action IN ('sell', 'reduce', 'close')
      THEN ((entry.close_price / NULLIF(outcome.close_price, 0)) - 1) * 10000
    ELSE ((outcome.close_price / NULLIF(entry.close_price, 0)) - 1) * 10000
  END AS action_aligned_forward_return_60m_bps,
  decision.expected_edge_after_cost_bps,
  decision.execution_cost_estimate_bps,
  decision.edge_to_cost_ratio,
  decision.expected_edge_score,
  decision.decision_score,
  decision.risk_score,
  decision.reason_codes,
  decision.cost_model_quality,
  (exec.execution_result_id IS NOT NULL AND exec.status IN ('filled', 'partially_filled')) AS was_executed,
  (risk.risk_check_id IS NOT NULL AND (
    risk.status = 'rejected'
    OR risk.rejected_decisions @> ARRAY['decisions.decision_set:' || decision.decision_set_id || ':' || decision.instrument_id]
  )) AS was_rejected_by_risk,
  COALESCE(risk.risk_flags, '{}'::text[]) AS risk_flags,
  ord.order_intent_id,
  exec.execution_result_id,
  exec.filled_quantity,
  ABS(COALESCE(exec.filled_quantity, 0) * COALESCE(exec.avg_fill_price, 0)) AS filled_notional,
  NULL::numeric AS realized_pnl_rub,
  decision.run_mode,
  COALESCE(ord.payload ->> 'portfolio_id', exec.payload ->> 'portfolio_id') AS portfolio_id,
  COALESCE(ord.payload ->> 'account_id', exec.payload ->> 'account_id') AS account_id,
  COALESCE(ord.payload ->> 'strategy_id', decision.weights_profile_id) AS strategy_id,
  (COALESCE(ord.payload ->> 'portfolio_id', exec.payload ->> 'portfolio_id') IS NULL) AS portfolio_scope_warning
FROM analytics.decision_fact decision
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = decision.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts <= decision.decision_ts
  ORDER BY candle.close_ts DESC
  LIMIT 1
) entry ON true
LEFT JOIN LATERAL (
  SELECT close_ts, close_price
  FROM raw_market.raw_candle candle
  WHERE candle.instrument_id = decision.instrument_id
    AND candle.close_price IS NOT NULL
    AND candle.close_ts >= decision.decision_ts + interval '60 minutes'
  ORDER BY candle.close_ts
  LIMIT 1
) outcome ON true
LEFT JOIN LATERAL (
  SELECT oi.*
  FROM orders.order_intent oi
  WHERE oi.decision_set_id = decision.decision_set_id
    AND oi.instrument_id = decision.instrument_id
  ORDER BY oi.created_at DESC
  LIMIT 1
) ord ON true
LEFT JOIN LATERAL (
  SELECT er.*
  FROM orders.execution_result er
  WHERE er.order_intent_id = ord.order_intent_id
  ORDER BY COALESCE(er.last_update_at, er.submitted_at) DESC NULLS LAST
  LIMIT 1
) exec ON true
LEFT JOIN LATERAL (
  SELECT rcr.*
  FROM risk.risk_check_result rcr
  WHERE rcr.decision_set_id = decision.decision_set_id
  ORDER BY rcr.checked_at DESC
  LIMIT 1
) risk ON true;

CREATE OR REPLACE VIEW analytics.churn_round_trips AS
WITH trades AS (
  SELECT
    tf.execution_result_id,
    tf.order_intent_id,
    tf.decision_record_id,
    tf.instrument_id,
    COALESCE(tf.decision_action, tf.side) AS action,
    tf.trade_ts,
    tf.turnover_rub,
    tf.avg_fill_price,
    tf.run_mode,
    tf.primary_reason_codes AS reason_codes,
    oi.payload ->> 'portfolio_id' AS portfolio_id,
    oi.payload ->> 'account_id' AS account_id,
    oi.payload ->> 'strategy_id' AS strategy_id
  FROM analytics.trade_fact tf
  LEFT JOIN orders.order_intent oi
    ON oi.order_intent_id = tf.order_intent_id
  WHERE tf.execution_status IN ('filled', 'partially_filled')
    AND tf.trade_ts IS NOT NULL
),
pairs AS (
  SELECT
    first_trade.instrument_id,
    first_trade.action AS first_action,
    second_trade.action AS second_action,
    first_trade.trade_ts AS first_ts,
    second_trade.trade_ts AS second_ts,
    EXTRACT(epoch FROM (second_trade.trade_ts - first_trade.trade_ts)) / 60.0 AS minutes_between,
    first_trade.execution_result_id AS first_execution_id,
    second_trade.execution_result_id AS second_execution_id,
    first_trade.turnover_rub AS first_notional,
    second_trade.turnover_rub AS second_notional,
    NULL::numeric AS estimated_round_trip_pnl_rub,
    NULL::numeric AS spread_cost_estimate_bps,
    first_trade.reason_codes AS reason_codes_first,
    second_trade.reason_codes AS reason_codes_second,
    COALESCE(first_trade.portfolio_id, second_trade.portfolio_id) AS portfolio_id,
    COALESCE(first_trade.account_id, second_trade.account_id) AS account_id,
    COALESCE(first_trade.strategy_id, second_trade.strategy_id) AS strategy_id,
    first_trade.run_mode,
    (first_trade.portfolio_id IS NULL OR second_trade.portfolio_id IS NULL) AS portfolio_scope_warning
  FROM trades first_trade
  JOIN trades second_trade
    ON second_trade.instrument_id = first_trade.instrument_id
   AND second_trade.order_intent_id <> first_trade.order_intent_id
   AND second_trade.trade_ts > first_trade.trade_ts
   AND second_trade.trade_ts <= first_trade.trade_ts + interval '15 minutes'
   AND (
     (first_trade.action = 'buy' AND second_trade.action IN ('sell', 'reduce', 'close'))
     OR (first_trade.action IN ('sell', 'reduce', 'close') AND second_trade.action = 'buy')
   )
   AND second_trade.run_mode IS NOT DISTINCT FROM first_trade.run_mode
   AND second_trade.portfolio_id IS NOT DISTINCT FROM first_trade.portfolio_id
)
SELECT DISTINCT ON (first_execution_id, second_execution_id)
  *
FROM pairs
ORDER BY first_execution_id, second_execution_id, minutes_between;

CREATE OR REPLACE VIEW analytics.guardrail_rejections AS
SELECT
  rcr.risk_check_id AS risk_check_result_id,
  decision.decision_id,
  decision.instrument_id,
  decision.action,
  rcr.status AS risk_status,
  rcr.risk_flags,
  flag.reason_code AS rejection_reason_code,
  rcr.checked_at AS rejected_at,
  decision.expected_edge_after_cost_bps,
  decision.execution_cost_estimate_bps,
  decision.edge_to_cost_ratio,
  COALESCE(decision.run_mode, rcr.payload ->> 'run_mode') AS run_mode,
  NULL::text AS portfolio_id,
  'turnover_mandate_urgency' = ANY(decision.reason_codes) AS was_turnover_mandate,
  flag.reason_code IN ('rejected_instrument_quarantine', 'rejected_instrument_blocked', 'watchlist_edge_threshold_increased') AS was_quarantine_related,
  flag.reason_code IN ('rejected_anti_churn_opposite_action', 'rejected_reentry_cooldown', 'rejected_trade_frequency_hourly', 'rejected_trade_frequency_daily', 'guardrail_trade_history_missing') AS was_churn_related,
  flag.reason_code IN ('rejected_low_expected_edge_after_cost', 'rejected_turnover_without_edge') AS was_edge_related
FROM risk.risk_check_result rcr
JOIN analytics.decision_fact decision
  ON decision.decision_set_id = rcr.decision_set_id
CROSS JOIN LATERAL unnest(COALESCE(rcr.risk_flags, '{}'::text[])) AS flag(reason_code)
WHERE flag.reason_code = ANY(ARRAY[
  'rejected_anti_churn_opposite_action',
  'rejected_reentry_cooldown',
  'rejected_trade_frequency_hourly',
  'rejected_trade_frequency_daily',
  'rejected_low_expected_edge_after_cost',
  'rejected_turnover_without_edge',
  'daily_soft_loss_reduce_only',
  'daily_hard_loss_observation_only',
  'rejected_instrument_quarantine',
  'rejected_instrument_blocked',
  'watchlist_edge_threshold_increased',
  'guardrail_trade_history_missing'
])
AND (
  rcr.rejected_decisions @> ARRAY['decisions.decision_set:' || decision.decision_set_id || ':' || decision.instrument_id]
  OR flag.reason_code = 'guardrail_trade_history_missing'
);

CREATE OR REPLACE VIEW analytics.edge_calibration AS
SELECT
  outcome30.instrument_id,
  outcome30.horizon,
  outcome30.action,
  outcome30.decision_id,
  outcome30.decision_ts,
  outcome30.expected_edge_after_cost_bps,
  outcome30.action_aligned_forward_return_30m_bps AS realized_action_aligned_return_30m_bps,
  outcome60.action_aligned_forward_return_60m_bps AS realized_action_aligned_return_60m_bps,
  outcome30.action_aligned_forward_return_30m_bps - outcome30.expected_edge_after_cost_bps AS calibration_error_30m_bps,
  outcome60.action_aligned_forward_return_60m_bps - outcome30.expected_edge_after_cost_bps AS calibration_error_60m_bps,
  CASE
    WHEN outcome30.expected_edge_after_cost_bps IS NULL THEN 'unknown'
    WHEN outcome30.expected_edge_after_cost_bps < 0 THEN 'negative'
    WHEN outcome30.expected_edge_after_cost_bps < 10 THEN '0_10_bps'
    WHEN outcome30.expected_edge_after_cost_bps < 30 THEN '10_30_bps'
    WHEN outcome30.expected_edge_after_cost_bps < 50 THEN '30_50_bps'
    WHEN outcome30.expected_edge_after_cost_bps < 100 THEN '50_100_bps'
    ELSE '100_plus_bps'
  END AS edge_bucket,
  outcome30.cost_model_quality,
  outcome30.reason_codes
FROM analytics.decision_outcome_30m outcome30
LEFT JOIN analytics.decision_outcome_60m outcome60
  ON outcome60.decision_id = outcome30.decision_id;

CREATE OR REPLACE VIEW analytics.instrument_live_stats AS
WITH outcome AS (
  SELECT *
  FROM analytics.decision_outcome_30m
),
trade AS (
  SELECT
    tf.instrument_id,
    tf.run_mode,
    oi.payload ->> 'portfolio_id' AS portfolio_id,
    oi.payload ->> 'account_id' AS account_id,
    oi.payload ->> 'strategy_id' AS strategy_id,
    COUNT(*) FILTER (WHERE execution_status IN ('filled', 'partially_filled')) AS executed_count,
    COALESCE(SUM(turnover_rub) FILTER (WHERE execution_status IN ('filled', 'partially_filled')), 0) AS turnover_rub,
    MAX(trade_ts) AS last_trade_ts
  FROM analytics.trade_fact tf
  LEFT JOIN orders.order_intent oi
    ON oi.order_intent_id = tf.order_intent_id
  GROUP BY tf.instrument_id, tf.run_mode, oi.payload ->> 'portfolio_id', oi.payload ->> 'account_id', oi.payload ->> 'strategy_id'
),
churn AS (
  SELECT instrument_id, run_mode, portfolio_id, account_id, strategy_id, COUNT(*) AS churn_flip_count
  FROM analytics.churn_round_trips
  GROUP BY instrument_id, run_mode, portfolio_id, account_id, strategy_id
),
portfolio_pnl AS (
  SELECT DISTINCT ON (portfolio_id, instrument_id)
    portfolio_id,
    instrument_id,
    unrealized_pnl,
    payload
  FROM portfolio.position_state
  ORDER BY portfolio_id, instrument_id, as_of_ts DESC
)
SELECT
  outcome.instrument_id,
  outcome.run_mode,
  outcome.portfolio_id,
  outcome.account_id,
  outcome.strategy_id,
  outcome.portfolio_scope_warning,
  COUNT(*) AS decisions_count,
  COALESCE(MAX(trade.executed_count), 0) AS executed_count,
  COUNT(*) FILTER (WHERE outcome.was_rejected_by_risk) AS rejected_count,
  COUNT(*) FILTER (WHERE outcome.action = 'buy') AS buy_count,
  COUNT(*) FILTER (WHERE outcome.action = 'sell') AS sell_count,
  COUNT(*) FILTER (WHERE outcome.action = 'reduce') AS reduce_count,
  COUNT(*) FILTER (WHERE outcome.action = 'hold') AS hold_count,
  AVG(CASE WHEN outcome.action = 'buy' AND outcome.raw_forward_return_30m > 0 THEN 1.0 WHEN outcome.action = 'buy' AND outcome.raw_forward_return_30m IS NOT NULL THEN 0.0 END) AS buy_accuracy_30m,
  AVG(CASE WHEN outcome.action IN ('sell', 'reduce', 'close') AND outcome.action_aligned_forward_return_30m > 0 THEN 1.0 WHEN outcome.action IN ('sell', 'reduce', 'close') AND outcome.action_aligned_forward_return_30m IS NOT NULL THEN 0.0 END) AS sell_accuracy_30m,
  AVG(outcome.raw_forward_return_30m) FILTER (WHERE outcome.action = 'buy') AS avg_buy_return_30m,
  AVG(outcome.action_aligned_forward_return_30m) FILTER (WHERE outcome.action IN ('sell', 'reduce', 'close')) AS avg_sell_action_aligned_return_30m,
  AVG(outcome.action_aligned_forward_return_30m) AS avg_action_aligned_return_30m,
  NULL::numeric AS realized_pnl_rub,
  MAX(portfolio_pnl.unrealized_pnl) AS unrealized_pnl_rub,
  COALESCE(MAX(trade.turnover_rub), 0) AS turnover_rub,
  AVG(outcome.expected_edge_after_cost_bps) AS avg_expected_edge_after_cost_bps,
  AVG(outcome.action_aligned_forward_return_30m_bps) AS avg_realized_forward_return_bps,
  AVG(outcome.action_aligned_forward_return_30m_bps - outcome.expected_edge_after_cost_bps) AS edge_calibration_error_bps,
  COUNT(*) FILTER (WHERE outcome.was_rejected_by_risk) AS guardrail_rejection_count,
  COALESCE(MAX(churn.churn_flip_count), 0) AS churn_flip_count,
  MAX(trade.last_trade_ts) AS last_trade_ts,
  CASE
    WHEN COALESCE(MAX(trade.executed_count), 0) >= 5
      AND AVG(CASE WHEN outcome.action = 'buy' AND outcome.raw_forward_return_30m > 0 THEN 1.0 WHEN outcome.action = 'buy' AND outcome.raw_forward_return_30m IS NOT NULL THEN 0.0 END) < 0.35
      THEN 'quarantine_candidate'
    WHEN AVG(outcome.action_aligned_forward_return_30m_bps) <= -50
      THEN 'quarantine_candidate'
    WHEN COALESCE(MAX(churn.churn_flip_count), 0) >= 3
      THEN 'quarantine_candidate'
    WHEN COALESCE(MAX(trade.executed_count), 0) >= 3
      AND AVG(outcome.action_aligned_forward_return_30m) < 0
      THEN 'watchlist_candidate'
    ELSE 'normal'
  END AS status_hint
FROM outcome
LEFT JOIN trade
  ON trade.instrument_id = outcome.instrument_id
 AND trade.run_mode IS NOT DISTINCT FROM outcome.run_mode
 AND trade.portfolio_id IS NOT DISTINCT FROM outcome.portfolio_id
 AND trade.account_id IS NOT DISTINCT FROM outcome.account_id
 AND trade.strategy_id IS NOT DISTINCT FROM outcome.strategy_id
LEFT JOIN churn
  ON churn.instrument_id = outcome.instrument_id
 AND churn.run_mode IS NOT DISTINCT FROM outcome.run_mode
 AND churn.portfolio_id IS NOT DISTINCT FROM outcome.portfolio_id
 AND churn.account_id IS NOT DISTINCT FROM outcome.account_id
 AND churn.strategy_id IS NOT DISTINCT FROM outcome.strategy_id
LEFT JOIN portfolio_pnl
  ON portfolio_pnl.instrument_id = outcome.instrument_id
 AND portfolio_pnl.portfolio_id IS NOT DISTINCT FROM outcome.portfolio_id
GROUP BY outcome.instrument_id, outcome.run_mode, outcome.portfolio_id, outcome.account_id, outcome.strategy_id, outcome.portfolio_scope_warning;

CREATE OR REPLACE VIEW analytics.pnl_by_reason_code AS
WITH reason_rows AS (
  SELECT
    outcome.*,
    reason.reason_code
  FROM analytics.decision_outcome_30m outcome
  CROSS JOIN LATERAL unnest(COALESCE(outcome.reason_codes, '{}'::text[])) AS reason(reason_code)
)
SELECT
  run_mode,
  portfolio_id,
  account_id,
  strategy_id,
  portfolio_scope_warning,
  reason_code,
  COUNT(*) AS decisions_count,
  COUNT(*) FILTER (WHERE was_executed) AS executed_count,
  COUNT(*) FILTER (WHERE was_rejected_by_risk) AS rejected_count,
  AVG(raw_forward_return_30m_bps) AS avg_forward_return_30m_bps,
  AVG(action_aligned_forward_return_30m_bps) AS avg_action_aligned_return_30m_bps,
  AVG(expected_edge_after_cost_bps) AS avg_expected_edge_after_cost_bps,
  NULL::numeric AS realized_pnl_rub,
  COALESCE(SUM(filled_notional), 0) AS turnover_rub,
  AVG(CASE WHEN action_aligned_forward_return_30m_bps > 0 THEN 1.0 WHEN action_aligned_forward_return_30m_bps IS NOT NULL THEN 0.0 END) AS win_rate_30m,
  AVG(execution_cost_estimate_bps) AS avg_cost_bps,
  COUNT(DISTINCT instrument_id) AS instruments_count
FROM reason_rows
WHERE reason_code IS NOT NULL AND reason_code <> ''
GROUP BY run_mode, portfolio_id, account_id, strategy_id, portfolio_scope_warning, reason_code;

CREATE OR REPLACE VIEW analytics.execution_cost_realized AS
SELECT
  exec.execution_result_id,
  ord.order_intent_id,
  decision.decision_id,
  ord.instrument_id,
  COALESCE(ord.side, decision.action) AS side,
  decision.action,
  ord.order_type,
  decision.decision_ts,
  ord.created_at AS order_ts,
  fill_agg.fill_ts,
  outcome.price_at_decision,
  ord.limit_price,
  COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) AS avg_fill_price,
  decision.execution_cost_estimate_bps AS expected_execution_cost_bps,
  CASE WHEN decision.cost_components ->> 'spread_cost_bps' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (decision.cost_components ->> 'spread_cost_bps')::numeric
  END AS expected_spread_cost_bps,
  CASE WHEN decision.cost_components ->> 'estimated_slippage_bps' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (decision.cost_components ->> 'estimated_slippage_bps')::numeric
  END AS expected_slippage_bps,
  CASE WHEN decision.cost_components ->> 'commission_bps' ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
    THEN (decision.cost_components ->> 'commission_bps')::numeric
  END AS expected_commission_bps,
  CASE
    WHEN outcome.price_at_decision IS NULL OR outcome.price_at_decision = 0 OR COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) IS NULL THEN NULL
    WHEN ord.side = 'sell'
      THEN ((outcome.price_at_decision - COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price)) / NULLIF(outcome.price_at_decision, 0)) * 10000
    ELSE ((COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) - outcome.price_at_decision) / NULLIF(outcome.price_at_decision, 0)) * 10000
  END AS realized_slippage_bps,
  CASE
    WHEN outcome.price_at_decision IS NULL OR outcome.price_at_decision = 0 OR COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) IS NULL THEN NULL
    ELSE (
      CASE
        WHEN ord.side = 'sell'
          THEN ((outcome.price_at_decision - COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price)) / NULLIF(outcome.price_at_decision, 0)) * 10000
        ELSE ((COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) - outcome.price_at_decision) / NULLIF(outcome.price_at_decision, 0)) * 10000
      END
      + COALESCE(exec.fees, fill_agg.fees, 0) / NULLIF(ABS(COALESCE(exec.filled_quantity, fill_agg.filled_quantity, 0) * COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price, 0)), 0) * 10000
    )
  END AS realized_cost_bps,
  CASE
    WHEN decision.execution_cost_estimate_bps IS NULL THEN NULL
    ELSE (
      CASE
        WHEN outcome.price_at_decision IS NULL OR outcome.price_at_decision = 0 OR COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) IS NULL THEN NULL
        ELSE (
          CASE
            WHEN ord.side = 'sell'
              THEN ((outcome.price_at_decision - COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price)) / NULLIF(outcome.price_at_decision, 0)) * 10000
            ELSE ((COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price) - outcome.price_at_decision) / NULLIF(outcome.price_at_decision, 0)) * 10000
          END
          + COALESCE(exec.fees, fill_agg.fees, 0) / NULLIF(ABS(COALESCE(exec.filled_quantity, fill_agg.filled_quantity, 0) * COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price, 0)), 0) * 10000
        )
      END
    ) - decision.execution_cost_estimate_bps
  END AS cost_error_bps,
  COALESCE(exec.filled_quantity, fill_agg.filled_quantity) AS filled_quantity,
  ABS(COALESCE(exec.filled_quantity, fill_agg.filled_quantity, 0) * COALESCE(fill_agg.avg_fill_price, exec.avg_fill_price, 0)) AS filled_notional,
  ARRAY[]::text[] AS liquidity_flags,
  COALESCE(risk.risk_flags, '{}'::text[]) AS risk_flags
FROM orders.execution_result exec
JOIN orders.order_intent ord
  ON ord.order_intent_id = exec.order_intent_id
LEFT JOIN analytics.decision_fact decision
  ON decision.decision_set_id = ord.decision_set_id
 AND decision.instrument_id = ord.instrument_id
LEFT JOIN analytics.decision_outcome_30m outcome
  ON outcome.decision_id = decision.decision_id
LEFT JOIN LATERAL (
  SELECT
    MAX(fill_ts) AS fill_ts,
    SUM(filled_quantity) AS filled_quantity,
    SUM(filled_quantity * fill_price) / NULLIF(SUM(filled_quantity), 0) AS avg_fill_price,
    SUM(fees) AS fees
  FROM orders.fill_report fill
  WHERE fill.order_intent_id = ord.order_intent_id
) fill_agg ON true
LEFT JOIN risk.risk_check_result risk
  ON risk.risk_check_id = ord.risk_check_id;

CREATE OR REPLACE VIEW analytics.feature_outcome_attribution AS
SELECT
  contribution.key AS metric_name,
  outcome.action,
  outcome.horizon,
  CASE
    WHEN (contribution.value)::numeric > 0 THEN 'positive'
    WHEN (contribution.value)::numeric < 0 THEN 'negative'
    ELSE 'zero'
  END AS contribution_sign,
  COUNT(*) AS decisions_count,
  COUNT(*) FILTER (WHERE outcome.was_executed) AS executed_count,
  AVG((contribution.value)::numeric) AS avg_feature_contribution,
  AVG(outcome.raw_forward_return_30m_bps) AS avg_forward_return_30m_bps,
  AVG(outcome.action_aligned_forward_return_30m_bps) AS avg_action_aligned_return_30m_bps,
  AVG(outcome.expected_edge_after_cost_bps) AS avg_expected_edge_after_cost_bps,
  AVG(CASE WHEN outcome.action_aligned_forward_return_30m_bps > 0 THEN 1.0 WHEN outcome.action_aligned_forward_return_30m_bps IS NOT NULL THEN 0.0 END) AS win_rate_30m,
  COUNT(DISTINCT outcome.instrument_id) AS instruments_count
FROM analytics.decision_outcome_30m outcome
JOIN decisions.decision_record record
  ON record.decision_record_id = outcome.decision_id
CROSS JOIN LATERAL jsonb_each_text(record.feature_contributions) AS contribution(key, value)
WHERE contribution.value ~ '^-?[0-9]+(\.[0-9]+)?([eE]-?[0-9]+)?$'
GROUP BY contribution.key, outcome.action, outcome.horizon,
  CASE
    WHEN (contribution.value)::numeric > 0 THEN 'positive'
    WHEN (contribution.value)::numeric < 0 THEN 'negative'
    ELSE 'zero'
  END;

CREATE OR REPLACE VIEW analytics.live_dashboard_summary AS
WITH latest_portfolio AS (
  SELECT *
  FROM portfolio.portfolio_snapshot
  ORDER BY as_of_ts DESC, created_at DESC
  LIMIT 1
),
decision_stats AS (
  SELECT
    COUNT(*) AS decisions_count,
    COUNT(*) FILTER (WHERE was_executed) AS executed_count,
    COUNT(*) FILTER (WHERE was_rejected_by_risk) AS rejected_count,
    AVG(CASE WHEN action = 'buy' AND raw_forward_return_30m > 0 THEN 1.0 WHEN action = 'buy' AND raw_forward_return_30m IS NOT NULL THEN 0.0 END) AS buy_accuracy_30m,
    AVG(raw_forward_return_30m_bps) FILTER (WHERE action = 'buy') AS avg_buy_return_30m_bps,
    AVG(action_aligned_forward_return_30m_bps) AS avg_action_aligned_return_30m_bps
  FROM analytics.decision_outcome_30m
),
trade_stats AS (
  SELECT
    COALESCE(SUM(turnover_rub) FILTER (WHERE execution_status IN ('filled', 'partially_filled')), 0) AS total_turnover_rub
  FROM analytics.trade_fact
),
churn_stats AS (
  SELECT COUNT(*) AS churn_flip_count
  FROM analytics.churn_round_trips
),
position_stats AS (
  SELECT COUNT(*) FILTER (WHERE quantity <> 0) AS active_positions_count
  FROM (
    SELECT DISTINCT ON (portfolio_id, instrument_id) *
    FROM portfolio.position_state
    ORDER BY portfolio_id, instrument_id, as_of_ts DESC
  ) latest_position
),
instrument_rank AS (
  SELECT
    instrument_id,
    COALESCE(realized_pnl_rub, 0) + COALESCE(unrealized_pnl_rub, 0) AS pnl_rub
  FROM analytics.instrument_live_stats
)
SELECT
  now() AS as_of_ts,
  latest_portfolio.equity AS equity_rub,
  COALESCE(latest_portfolio.realized_pnl, 0) + COALESCE(latest_portfolio.unrealized_pnl, 0) AS daily_pnl_rub,
  (COALESCE(latest_portfolio.realized_pnl, 0) + COALESCE(latest_portfolio.unrealized_pnl, 0)) / NULLIF(latest_portfolio.equity, 0) AS daily_pnl_pct,
  trade_stats.total_turnover_rub,
  decision_stats.decisions_count,
  decision_stats.executed_count,
  decision_stats.rejected_count,
  decision_stats.rejected_count::numeric / NULLIF(decision_stats.decisions_count, 0) AS guardrail_rejection_rate,
  decision_stats.buy_accuracy_30m,
  decision_stats.avg_buy_return_30m_bps,
  decision_stats.avg_action_aligned_return_30m_bps,
  churn_stats.churn_flip_count,
  position_stats.active_positions_count,
  (SELECT instrument_id FROM instrument_rank ORDER BY pnl_rub ASC NULLS LAST LIMIT 1) AS worst_instrument_by_pnl,
  (SELECT instrument_id FROM instrument_rank ORDER BY pnl_rub DESC NULLS LAST LIMIT 1) AS best_instrument_by_pnl,
  (
    (COALESCE(latest_portfolio.realized_pnl, 0) + COALESCE(latest_portfolio.unrealized_pnl, 0)) / NULLIF(latest_portfolio.equity, 0) <= -0.0015
    OR churn_stats.churn_flip_count >= 3
  ) AS observation_only_recommended,
  (
    (COALESCE(latest_portfolio.realized_pnl, 0) + COALESCE(latest_portfolio.unrealized_pnl, 0)) / NULLIF(latest_portfolio.equity, 0) <= -0.001
    OR decision_stats.avg_action_aligned_return_30m_bps < 0
  ) AS reduce_only_recommended
FROM latest_portfolio
CROSS JOIN decision_stats
CROSS JOIN trade_stats
CROSS JOIN churn_stats
CROSS JOIN position_stats;

INSERT INTO audit.audit_record (
  module_name, job_id, severity, event_type, message,
  object_type, object_ref, reason_codes, payload
)
SELECT
  'Monitoring & Audit Module',
  'migration_033_live_validation_analytics_views',
  'info',
  'live_validation_analytics_views_created',
  'Read-only analytics views were added for live validation of guardrails, post-cost edge, churn, instrument toxicity and realized execution cost.',
  'migration',
  '033_live_validation_analytics_views',
  ARRAY['analytics_read_only', 'guardrail_validation', 'post_cost_edge_validation'],
  jsonb_build_object(
    'schema', 'analytics',
    'views', ARRAY[
      'decision_fact',
      'decision_outcome_30m',
      'decision_outcome_60m',
      'instrument_live_stats',
      'churn_round_trips',
      'guardrail_rejections',
      'edge_calibration',
      'pnl_by_reason_code',
      'execution_cost_realized',
      'feature_outcome_attribution',
      'live_dashboard_summary'
    ]
  )
WHERE NOT EXISTS (
  SELECT 1
  FROM audit.audit_record
  WHERE event_type = 'live_validation_analytics_views_created'
    AND object_ref = '033_live_validation_analytics_views'
);

COMMIT;
