BEGIN;

-- Make live autonomous scoring less pro-cyclical under weak market-wide
-- conditions.  Turnover remains a mandatory constraint, but it no longer
-- supplies enough score by itself to put the whole portfolio long when
-- macro/regime context is weak.
UPDATE weights.metric_weight_rule
   SET weight = 0.1200000000,
       direction = 'positive',
       calculation_version = 'live_macro_regime_overlay_v1'
 WHERE weights_profile_id IN (
        'weights:live_autonomous:intraday:v1',
        'weights:live_autonomous:swing:v1',
        'weights:live_autonomous:position:v1'
      )
   AND metric_name = 'risk_on_risk_off_score';

UPDATE weights.metric_weight_rule
   SET weight = 0.0800000000,
       direction = 'positive',
       calculation_version = 'live_macro_regime_overlay_v1'
 WHERE weights_profile_id IN (
        'weights:live_autonomous:intraday:v1',
        'weights:live_autonomous:swing:v1',
        'weights:live_autonomous:position:v1'
      )
   AND metric_name = 'market_breadth';

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
  ('weight:live_autonomous:intraday:macro_pressure_score', 'weights:live_autonomous:intraday:v1', 'macro_pressure_score', 'market_context', 'intraday', 'all', ARRAY[]::TEXT[], NULL, 0.1000000000, 'negative', 'identity', 0.550000, 'downweight', 'live_macro_regime_overlay_v1'),
  ('weight:live_autonomous:swing:macro_pressure_score', 'weights:live_autonomous:swing:v1', 'macro_pressure_score', 'market_context', 'swing', 'all', ARRAY[]::TEXT[], NULL, 0.1000000000, 'negative', 'identity', 0.550000, 'downweight', 'live_macro_regime_overlay_v1'),
  ('weight:live_autonomous:position:macro_pressure_score', 'weights:live_autonomous:position:v1', 'macro_pressure_score', 'market_context', 'position', 'all', ARRAY[]::TEXT[], NULL, 0.1200000000, 'negative', 'identity', 0.550000, 'downweight', 'live_macro_regime_overlay_v1')
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weight = EXCLUDED.weight,
  direction = EXCLUDED.direction,
  min_confidence_score = EXCLUDED.min_confidence_score,
  stale_policy = EXCLUDED.stale_policy,
  calculation_version = EXCLUDED.calculation_version;

UPDATE weights.metric_weight_rule
   SET weight = CASE
         WHEN metric_name = 'turnover_deficit_score' THEN 0.0200000000
         WHEN metric_name = 'trade_urgency_score' THEN 0.0200000000
         WHEN metric_name = 'churn_penalty_score' THEN 0.0800000000
         ELSE weight
       END,
       calculation_version = 'live_macro_regime_overlay_v1'
 WHERE weights_profile_id IN ('weights:live_autonomous:intraday:v1', 'weights:live_autonomous:swing:v1')
   AND metric_name IN ('turnover_deficit_score', 'trade_urgency_score', 'churn_penalty_score');

UPDATE risk.risk_policy
   SET rules = rules || jsonb_build_object(
       'macro_regime_overlay_enabled', true,
       'blocked_market_regimes', jsonb_build_array('risk_off', 'stress', 'halt'),
       'turnover_boost_cannot_override_macro_regime_overlay', true,
       'portfolio_exposure_uses_ratio_or_rub_semantics', true,
       'risk_batch_shadow_state_required', true
     )
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1';

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Decision Engine Module',
    'warning',
    'live_macro_regime_overlay_enabled',
    'Live autonomous weights and risk policy now apply a market-wide macro/regime overlay so turnover urgency cannot turn weak broad market context into all-long allocation.',
    'migration',
    '026_live_risk_macro_overlay',
    ARRAY['macro_regime_overlay', 'turnover_not_alpha', 'batch_risk_shadow_state'],
    jsonb_build_object(
      'fast_turnover_weights_reduced', true,
      'macro_pressure_live_weight_added', true,
      'risk_on_risk_off_live_weight', 0.12,
      'churn_penalty_weight', 0.08
    )
);

COMMIT;
