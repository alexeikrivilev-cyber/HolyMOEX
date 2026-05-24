BEGIN;

-- Keep the scheduled discovery state machine aligned with runtime skip reasons.
-- Public corporate/IR sources can be intentionally skipped until issuer_ir_url or
-- another endpoint is present in the selected instrument metadata.
ALTER TABLE raw_text.scheduled_external_news_discovery_item
  DROP CONSTRAINT IF EXISTS scheduled_external_news_discovery_item_skip_reason_code_check;
ALTER TABLE raw_text.scheduled_external_news_discovery_item
  ADD CONSTRAINT scheduled_external_news_discovery_item_skip_reason_code_check
  CHECK (skip_reason_code IS NULL OR skip_reason_code IN (
    'source_disabled',
    'instrument_not_eligible',
    'source_missing_endpoint'
  ));

-- Normalize live autonomous weights after adding turnover/churn objective terms.
-- Product baseline profiles are left untouched. Live profiles keep their source
-- ranking but remain comparable to the decision threshold by summing to ~1.0.
UPDATE weights.metric_weight_rule
   SET weight = weight * 0.8500000000,
       calculation_version = 'live_autonomous_turnover_normalized_v2'
 WHERE weights_profile_id = 'weights:live_autonomous:intraday:v1'
   AND metric_name NOT IN ('turnover_deficit_score', 'trade_urgency_score', 'churn_penalty_score');

UPDATE weights.metric_weight_rule
   SET calculation_version = 'live_autonomous_turnover_normalized_v2'
 WHERE weights_profile_id = 'weights:live_autonomous:intraday:v1'
   AND metric_name IN ('turnover_deficit_score', 'trade_urgency_score', 'churn_penalty_score');

UPDATE weights.metric_weight_rule
   SET weight = weight * 0.9400000000,
       calculation_version = 'live_autonomous_turnover_normalized_v2'
 WHERE weights_profile_id = 'weights:live_autonomous:swing:v1'
   AND metric_name NOT IN ('turnover_deficit_score', 'trade_urgency_score', 'churn_penalty_score');

UPDATE weights.metric_weight_rule
   SET calculation_version = 'live_autonomous_turnover_normalized_v2'
 WHERE weights_profile_id = 'weights:live_autonomous:swing:v1'
   AND metric_name IN ('turnover_deficit_score', 'trade_urgency_score', 'churn_penalty_score');

UPDATE weights.metric_weight_rule
   SET calculation_version = 'live_autonomous_turnover_normalized_v2'
 WHERE weights_profile_id = 'weights:live_autonomous:position:v1';

CREATE OR REPLACE VIEW audit.metric_weights_readiness_check AS
WITH live_profile_state AS (
  SELECT count(*) AS active_live_profiles
    FROM weights.weights_profile
   WHERE weights_profile_id IN ('weights:live_autonomous:intraday:v1', 'weights:live_autonomous:swing:v1', 'weights:live_autonomous:position:v1')
     AND status = 'active'
     AND 'live_trading' = ANY(run_mode_allowed)
),
live_rule_state AS (
  SELECT weights_profile_id, count(*) AS rule_count
    FROM weights.metric_weight_rule
   WHERE weights_profile_id IN ('weights:live_autonomous:intraday:v1', 'weights:live_autonomous:swing:v1', 'weights:live_autonomous:position:v1')
   GROUP BY weights_profile_id
),
live_weight_totals AS (
  SELECT profile.weights_profile_id,
         profile.horizon,
         count(rule.metric_weight_rule_id) AS rule_count,
         COALESCE(sum(rule.weight), 0.0) AS total_weight
    FROM weights.weights_profile profile
    LEFT JOIN weights.metric_weight_rule rule
      ON rule.weights_profile_id = profile.weights_profile_id
   WHERE profile.weights_profile_id IN ('weights:live_autonomous:intraday:v1', 'weights:live_autonomous:swing:v1', 'weights:live_autonomous:position:v1')
   GROUP BY profile.weights_profile_id, profile.horizon
),
product_profile_state AS (
  SELECT count(*) AS active_product_profiles
    FROM weights.weights_profile
   WHERE weights_profile_id IN ('weights:product_baseline:intraday:v1', 'weights:product_baseline:swing:v1', 'weights:product_baseline:position:v1')
     AND status = 'active'
),
product_weight_totals AS (
  SELECT profile.weights_profile_id,
         count(rule.metric_weight_rule_id) AS rule_count,
         COALESCE(sum(rule.weight), 0.0) AS total_weight
    FROM weights.weights_profile profile
    LEFT JOIN weights.metric_weight_rule rule
      ON rule.weights_profile_id = profile.weights_profile_id
   WHERE profile.weights_profile_id IN ('weights:product_baseline:intraday:v1', 'weights:product_baseline:swing:v1', 'weights:product_baseline:position:v1')
   GROUP BY profile.weights_profile_id
)
SELECT 'product_profiles_active' AS check_name,
       CASE WHEN active_product_profiles = 3 THEN 'pass' ELSE 'fail' END AS status,
       active_product_profiles::text AS observed_value,
       '3 active product baseline profiles' AS expected_value,
       '{}'::jsonb AS details
  FROM product_profile_state
UNION ALL
SELECT 'product_weight_totals_normalized',
       CASE WHEN count(*) = 3 AND count(*) FILTER (WHERE rule_count > 0 AND abs(total_weight - 1.0000000000) <= 0.000001) = 3 THEN 'pass' ELSE 'fail' END,
       jsonb_object_agg(weights_profile_id, total_weight)::text,
       'all product profile weights sum to 1.0',
       jsonb_object_agg(weights_profile_id, jsonb_build_object('rule_count', rule_count, 'total_weight', total_weight))
  FROM product_weight_totals
UNION ALL
SELECT 'live_autonomous_profiles_active',
       CASE WHEN active_live_profiles = 3 THEN 'pass' ELSE 'fail' END,
       active_live_profiles::text,
       '3 active live_autonomous profiles',
       '{}'::jsonb
  FROM live_profile_state
UNION ALL
SELECT 'live_autonomous_rules_present',
       CASE WHEN count(*) FILTER (WHERE rule_count > 0) = 3 THEN 'pass' ELSE 'fail' END,
       count(*) FILTER (WHERE rule_count > 0)::text,
       'rules present for all 3 live profiles',
       jsonb_object_agg(weights_profile_id, rule_count)
  FROM live_rule_state
UNION ALL
SELECT 'live_autonomous_weight_totals_normalized',
       CASE WHEN count(*) = 3 AND count(*) FILTER (WHERE rule_count > 0 AND abs(total_weight - 1.0000000000) <= 0.000001) = 3 THEN 'pass' ELSE 'fail' END,
       jsonb_object_agg(weights_profile_id, total_weight)::text,
       'all live autonomous profile weights sum to 1.0 after turnover terms',
       jsonb_object_agg(weights_profile_id, jsonb_build_object('horizon', horizon, 'rule_count', rule_count, 'total_weight', total_weight))
  FROM live_weight_totals;

INSERT INTO audit.audit_record (module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload)
VALUES (
  'Orchestration Module',
  'info',
  'predfinal_runtime_hardening_applied',
  'Predfinal runtime hardening added source_missing_endpoint skip reason, normalized live weights, and stronger weights readiness checks.',
  'migration',
  '013_predfinal_runtime_hardening',
  ARRAY['source_schema_aligned', 'live_weights_normalized', 'readiness_hardened'],
  '{}'::jsonb
);

COMMIT;
