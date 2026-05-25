BEGIN;

-- Strategy quality hardening v2: keep Risk Control as the hard gate, but make
-- live scoring less cost-blind and add explicit short-selling governance.
INSERT INTO weights.metric_weight_rule (
  metric_weight_rule_id,
  weights_profile_id,
  metric_name,
  metric_group,
  horizon,
  instrument_scope,
  instrument_ids,
  sector,
  weight,
  direction,
  transform,
  min_confidence_score,
  stale_policy,
  calculation_version
) VALUES
  ('weight:live_autonomous:intraday:edge_to_cost_ratio', 'weights:live_autonomous:intraday:v1', 'edge_to_cost_ratio', 'execution_cost', 'intraday', 'all', ARRAY[]::TEXT[], NULL, 0.0600000000, 'positive', 'identity', 0.550000, 'downweight', 'strategy_quality_short_hardening_v2'),
  ('weight:live_autonomous:swing:edge_to_cost_ratio', 'weights:live_autonomous:swing:v1', 'edge_to_cost_ratio', 'execution_cost', 'swing', 'all', ARRAY[]::TEXT[], NULL, 0.0400000000, 'positive', 'identity', 0.550000, 'downweight', 'strategy_quality_short_hardening_v2'),
  ('weight:live_autonomous:position:edge_to_cost_ratio', 'weights:live_autonomous:position:v1', 'edge_to_cost_ratio', 'execution_cost', 'position', 'all', ARRAY[]::TEXT[], NULL, 0.0300000000, 'positive', 'identity', 0.550000, 'downweight', 'strategy_quality_short_hardening_v2'),
  ('weight:live_autonomous:intraday:execution_cost_estimate_bps', 'weights:live_autonomous:intraday:v1', 'execution_cost_estimate_bps', 'execution_cost', 'intraday', 'all', ARRAY[]::TEXT[], NULL, 0.0500000000, 'negative', 'identity', 0.550000, 'downweight', 'strategy_quality_short_hardening_v2'),
  ('weight:live_autonomous:swing:execution_cost_estimate_bps', 'weights:live_autonomous:swing:v1', 'execution_cost_estimate_bps', 'execution_cost', 'swing', 'all', ARRAY[]::TEXT[], NULL, 0.0350000000, 'negative', 'identity', 0.550000, 'downweight', 'strategy_quality_short_hardening_v2'),
  ('weight:live_autonomous:position:execution_cost_estimate_bps', 'weights:live_autonomous:position:v1', 'execution_cost_estimate_bps', 'execution_cost', 'position', 'all', ARRAY[]::TEXT[], NULL, 0.0250000000, 'negative', 'identity', 0.550000, 'downweight', 'strategy_quality_short_hardening_v2')
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weight = EXCLUDED.weight,
  direction = EXCLUDED.direction,
  min_confidence_score = EXCLUDED.min_confidence_score,
  stale_policy = EXCLUDED.stale_policy,
  calculation_version = EXCLUDED.calculation_version;

UPDATE weights.metric_weight_rule
   SET weight = weight * 0.85,
       calculation_version = 'strategy_quality_short_hardening_v2'
 WHERE weights_profile_id IN (
        'weights:live_autonomous:intraday:v1',
        'weights:live_autonomous:swing:v1',
        'weights:live_autonomous:position:v1'
      )
   AND metric_name IN ('volatility_risk_score', 'liquidity_risk_score', 'portfolio_concentration_risk', 'data_quality_penalty');

UPDATE risk.risk_policy
   SET rules = rules || jsonb_build_object(
       'decision_uses_post_cost_edge_for_actions', true,
       'turnover_urgency_cannot_override_post_cost_edge', true,
       'position_effect_required_for_execution', true,
       'short_selling_policy_status', 'explicit_capability_required',
       'short_cover_on_risk_off', true
     )
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1';

INSERT INTO risk.portfolio_limit (risk_policy_id, limit_name, limit_value, payload) VALUES
  ('risk_policy:live_autonomous_turnover:v1', 'max_short_position_pct', 0.0500000000, '{"unit":"ratio","scope":"single_instrument"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_total_short_exposure_pct', 0.2000000000, '{"unit":"ratio","scope":"portfolio"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_single_short_order_value_rub', 75000.0000000000, '{"unit":"RUB"}'::jsonb)
ON CONFLICT (risk_policy_id, limit_name) DO UPDATE SET
  limit_value = EXCLUDED.limit_value,
  payload = risk.portfolio_limit.payload || EXCLUDED.payload;

WITH totals AS (
  SELECT weights_profile_id, horizon, SUM(ABS(weight)) AS total_weight
    FROM weights.metric_weight_rule
   WHERE weights_profile_id IN (
          'weights:live_autonomous:intraday:v1',
          'weights:live_autonomous:swing:v1',
          'weights:live_autonomous:position:v1'
        )
   GROUP BY weights_profile_id, horizon
)
UPDATE weights.metric_weight_rule rule
   SET weight = rule.weight / totals.total_weight,
       calculation_version = 'strategy_quality_short_hardening_v2'
  FROM totals
 WHERE rule.weights_profile_id = totals.weights_profile_id
   AND rule.horizon = totals.horizon
   AND totals.total_weight > 0;

CREATE OR REPLACE VIEW audit.strategy_quality_readiness_check AS
WITH live_weight_totals AS (
  SELECT weights_profile_id, horizon, ROUND(SUM(ABS(weight))::numeric, 6) AS total_weight
    FROM weights.metric_weight_rule
   WHERE weights_profile_id IN (
          'weights:live_autonomous:intraday:v1',
          'weights:live_autonomous:swing:v1',
          'weights:live_autonomous:position:v1'
        )
   GROUP BY weights_profile_id, horizon
)
SELECT
  'live_weight_normalization:' || weights_profile_id || ':' || horizon AS check_name,
  CASE WHEN ABS(total_weight - 1.000000) <= 0.000500 THEN 'pass' ELSE 'fail' END AS status,
  jsonb_build_object('total_weight', total_weight) AS details
FROM live_weight_totals
UNION ALL
SELECT
  'short_selling_policy_governed' AS check_name,
  CASE WHEN EXISTS (
    SELECT 1 FROM risk.portfolio_limit
     WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1'
       AND limit_name IN ('max_short_position_pct', 'max_total_short_exposure_pct', 'max_single_short_order_value_rub')
  ) THEN 'pass' ELSE 'fail' END AS status,
  jsonb_build_object('policy_id', 'risk_policy:live_autonomous_turnover:v1') AS details;

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Decision Engine Module',
    'info',
    'strategy_quality_short_hardening_applied',
    'Live autonomous scoring now ranks actions on signed post-cost edge and risk policy has explicit short-selling limits.',
    'migration',
    '030_strategy_quality_short_hardening',
    ARRAY['post_cost_edge_first', 'position_effect_required', 'short_selling_governed'],
    jsonb_build_object(
      'calculation_version', 'strategy_quality_short_hardening_v2',
      'edge_to_cost_ratio_weight_added', true,
      'score_level_risk_weights_reduced_without_disabling_hard_gates', true,
      'short_limits_seeded', true
    )
);

COMMIT;
