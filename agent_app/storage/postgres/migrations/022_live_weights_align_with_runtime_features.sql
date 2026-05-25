UPDATE weights.metric_weight_rule
   SET metric_name = 'intraday_return',
       calculation_version = 'live_runtime_feature_alignment_v1'
 WHERE weights_profile_id = 'weights:live_autonomous:intraday:v1'
   AND metric_name = 'price_strength_score';

UPDATE weights.metric_weight_rule
   SET metric_name = 'recovery_ratio',
       calculation_version = 'live_runtime_feature_alignment_v1'
 WHERE weights_profile_id = 'weights:live_autonomous:intraday:v1'
   AND metric_name = 'short_term_pressure_score';

UPDATE weights.metric_weight_rule
   SET metric_name = 'market_breadth',
       calculation_version = 'live_runtime_feature_alignment_v1'
 WHERE weights_profile_id = 'weights:live_autonomous:intraday:v1'
   AND metric_name = 'sector_pressure_score';

UPDATE weights.metric_weight_rule
   SET metric_name = 'intraday_return',
       calculation_version = 'live_runtime_feature_alignment_v1'
 WHERE weights_profile_id = 'weights:live_autonomous:swing:v1'
   AND metric_name = 'price_strength_score';

UPDATE weights.metric_weight_rule
   SET metric_name = 'market_breadth',
       calculation_version = 'live_runtime_feature_alignment_v1'
 WHERE weights_profile_id = 'weights:live_autonomous:swing:v1'
   AND metric_name = 'sector_pressure_score';

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Decision Engine Module',
    'info',
    'live_weights_aligned_with_runtime_features',
    'Live autonomous weights were aligned with feature names actually produced by the current market/liquidity/volatility runtime modules.',
    'migration',
    '022_live_weights_align_with_runtime_features',
    ARRAY['weights_runtime_feature_alignment', 'no_weighted_features_fixed', 'post_cost_edge_gate_preserved'],
    jsonb_build_object(
      'intraday_replacements', jsonb_build_object(
        'price_strength_score', 'intraday_return',
        'short_term_pressure_score', 'recovery_ratio',
        'sector_pressure_score', 'market_breadth'
      ),
      'swing_replacements', jsonb_build_object(
        'price_strength_score', 'intraday_return',
        'sector_pressure_score', 'market_breadth'
      )
    )
);
