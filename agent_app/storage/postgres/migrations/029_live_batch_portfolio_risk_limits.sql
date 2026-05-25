BEGIN;

-- Prevent a single autonomous live cycle from fanning out into broad
-- risk-increasing exposure across the whole selected universe.  Risk-reducing
-- orders remain allowed; new long/short exposure is ranked and capped by Risk
-- Control.
UPDATE risk.risk_policy
   SET rules = COALESCE(rules, '{}'::jsonb) || jsonb_build_object(
       'max_risk_increasing_order_intents_per_cycle', 4,
       'max_new_long_order_intents_per_cycle', 3,
       'max_new_short_order_intents_per_cycle', 3,
       'batch_order_selection', 'rank_by_post_cost_edge_then_apply_cycle_caps'
     )
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1';

INSERT INTO risk.portfolio_limit (risk_policy_id, limit_name, limit_value, payload) VALUES
  (
    'risk_policy:live_autonomous_turnover:v1',
    'max_risk_increasing_order_intents_per_cycle',
    4,
    '{"unit":"orders","scope":"risk_check_cycle","risk_reducing_orders_exempt":true}'::jsonb
  ),
  (
    'risk_policy:live_autonomous_turnover:v1',
    'max_new_long_order_intents_per_cycle',
    3,
    '{"unit":"orders","scope":"risk_check_cycle","risk_reducing_orders_exempt":true}'::jsonb
  ),
  (
    'risk_policy:live_autonomous_turnover:v1',
    'max_new_short_order_intents_per_cycle',
    3,
    '{"unit":"orders","scope":"risk_check_cycle","risk_reducing_orders_exempt":true}'::jsonb
  )
ON CONFLICT (risk_policy_id, limit_name) DO UPDATE SET
  limit_value = EXCLUDED.limit_value,
  payload = EXCLUDED.payload;

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Risk Control Module',
    'info',
    'live_batch_portfolio_risk_limits_enabled',
    'Live autonomous Risk Control now ranks decisions by post-cost edge and caps risk-increasing order fan-out per cycle.',
    'migration',
    '029_live_batch_portfolio_risk_limits',
    ARRAY['batch_portfolio_gate', 'new_long_fanout_limited', 'short_opening_supported'],
    jsonb_build_object(
      'risk_policy_id', 'risk_policy:live_autonomous_turnover:v1',
      'max_risk_increasing_order_intents_per_cycle', 4,
      'max_new_long_order_intents_per_cycle', 3,
      'max_new_short_order_intents_per_cycle', 3,
      'risk_reducing_orders_exempt', true
    )
);

COMMIT;
