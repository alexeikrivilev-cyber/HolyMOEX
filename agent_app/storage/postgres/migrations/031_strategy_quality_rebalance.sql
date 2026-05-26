-- Rebalance live autonomous sizing/short policy after enabling explicit shorts.
-- The goal is to keep shorts available, but symmetric with long entries and
-- backed by meaningful post-cost edge instead of degraded macro/noise.

UPDATE risk.risk_policy
   SET rules = rules
       || jsonb_build_object(
            'min_expected_edge_after_cost_score', 0.015,
            'min_lot_round_up_edge_after_cost_score', 0.050,
            'min_risk_increasing_order_value_rub', 5000,
            'decision_short_entry_threshold', 0.050,
            'decision_short_add_threshold', 0.060,
            'decision_min_target_position_pct', 0.012,
            'decision_target_full_edge_score', 0.180,
            'macro_degraded_edge_penalty', 0.0,
            'macro_overlay_short_bias_enabled', false,
            'strategy_quality_rebalance_version', 'strategy_quality_rebalance_v1'
          )
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1';

INSERT INTO risk.portfolio_limit (
  risk_policy_id, limit_name, limit_value, payload
)
VALUES
  (
    'risk_policy:live_autonomous_turnover:v1',
    'min_expected_edge_after_cost_score',
    0.015,
    '{"unit":"score","reason":"risk gate aligned with post-cost live trading economics"}'::jsonb
  ),
  (
    'risk_policy:live_autonomous_turnover:v1',
    'min_risk_increasing_order_value_rub',
    5000,
    '{"unit":"RUB","risk_reducing_orders_exempt":true,"reason":"prevent micro-churn from tiny risk-increasing orders"}'::jsonb
  )
ON CONFLICT (risk_policy_id, limit_name) DO UPDATE SET
  limit_value = EXCLUDED.limit_value,
  payload = EXCLUDED.payload;

INSERT INTO audit.audit_record (
  module_name, job_id, severity, event_type, message,
  object_type, object_ref, reason_codes, payload
)
SELECT
  'Risk Control Module',
  'migration_031_strategy_quality_rebalance',
  'info',
  'strategy_quality_rebalance_applied',
  'Live autonomous shorts remain enabled, but short entry threshold, min-lot economics, and risk-increasing order sizing were rebalanced to prevent small noisy short churn.',
  'risk_policy',
  'risk.risk_policy:risk_policy:live_autonomous_turnover:v1',
  ARRAY['short_threshold_symmetric', 'micro_churn_guard', 'macro_missing_not_short_signal'],
  jsonb_build_object(
    'min_expected_edge_after_cost_score', 0.015,
    'min_lot_round_up_edge_after_cost_score', 0.050,
    'min_risk_increasing_order_value_rub', 5000,
    'decision_short_entry_threshold', 0.050
  )
WHERE NOT EXISTS (
  SELECT 1
  FROM audit.audit_record
  WHERE event_type = 'strategy_quality_rebalance_applied'
    AND object_ref = 'risk.risk_policy:risk_policy:live_autonomous_turnover:v1'
);
