BEGIN;

WITH totals AS (
  SELECT weights_profile_id,
         sum(weight) AS total_weight
    FROM weights.metric_weight_rule
   WHERE weights_profile_id IN (
          'weights:live_autonomous:intraday:v1',
          'weights:live_autonomous:swing:v1',
          'weights:live_autonomous:position:v1'
        )
   GROUP BY weights_profile_id
)
UPDATE weights.metric_weight_rule rule
   SET weight = rule.weight / totals.total_weight,
       calculation_version = 'live_macro_regime_overlay_normalized_v1'
  FROM totals
 WHERE rule.weights_profile_id = totals.weights_profile_id
   AND totals.total_weight > 0;

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Decision Engine Module',
    'info',
    'live_weights_renormalized',
    'Live autonomous weights were renormalized to sum to 1.0 after macro/regime overlay rebalancing.',
    'migration',
    '027_live_weight_total_renormalization',
    ARRAY['metric_weights_normalized', 'readiness_gate_restored'],
    jsonb_build_object(
      'profiles', jsonb_build_array(
        'weights:live_autonomous:intraday:v1',
        'weights:live_autonomous:swing:v1',
        'weights:live_autonomous:position:v1'
      )
    )
);

COMMIT;
