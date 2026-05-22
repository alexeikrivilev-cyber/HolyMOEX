BEGIN;

UPDATE weights.weights_profile
   SET status = 'deprecated',
       validation_report_ref = 'superseded_by:weights:product_baseline:v1'
 WHERE weights_profile_id IN (
   'weights:strict_default:intraday:v1',
   'weights:strict_default:swing:v1',
   'weights:strict_default:position:v1'
 );

INSERT INTO weights.weights_profile (
  weights_profile_id,
  profile_name,
  version,
  status,
  horizon,
  run_mode_allowed,
  approved_by,
  validation_report_ref
) VALUES
  (
    'weights:product_baseline:intraday:v1',
    'product_baseline',
    '1.0',
    'active',
    'intraday',
    ARRAY['analysis_only', 'paper_trading'],
    'owner_governance_request:2026-05-22',
    'expert_analytical_baseline:metric_weights:v1'
  ),
  (
    'weights:product_baseline:swing:v1',
    'product_baseline',
    '1.0',
    'active',
    'swing',
    ARRAY['analysis_only', 'paper_trading'],
    'owner_governance_request:2026-05-22',
    'expert_analytical_baseline:metric_weights:v1'
  ),
  (
    'weights:product_baseline:position:v1',
    'product_baseline',
    '1.0',
    'active',
    'position',
    ARRAY['analysis_only', 'paper_trading'],
    'owner_governance_request:2026-05-22',
    'expert_analytical_baseline:metric_weights:v1'
  )
ON CONFLICT (weights_profile_id) DO UPDATE SET
  profile_name = EXCLUDED.profile_name,
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  horizon = EXCLUDED.horizon,
  run_mode_allowed = EXCLUDED.run_mode_allowed,
  approved_by = EXCLUDED.approved_by,
  validation_report_ref = EXCLUDED.validation_report_ref;

WITH product_rules(metric_weight_rule_id, weights_profile_id, metric_name, metric_group, horizon, weight, direction, transform, min_confidence_score, stale_policy) AS (
  VALUES
    ('weight:product:intraday:price_strength_score', 'weights:product_baseline:intraday:v1', 'price_strength_score', 'price', 'intraday', 0.1500000000, 'positive', 'identity', 0.600000, 'downweight'),
    ('weight:product:intraday:short_term_pressure_score', 'weights:product_baseline:intraday:v1', 'short_term_pressure_score', 'liquidity', 'intraday', 0.1400000000, 'positive', 'identity', 0.700000, 'downweight'),
    ('weight:product:intraday:event_pressure_score', 'weights:product_baseline:intraday:v1', 'event_pressure_score', 'event', 'intraday', 0.1200000000, 'positive', 'identity', 0.700000, 'downweight'),
    ('weight:product:intraday:risk_on_risk_off_score', 'weights:product_baseline:intraday:v1', 'risk_on_risk_off_score', 'market_context', 'intraday', 0.0800000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:intraday:sector_pressure_score', 'weights:product_baseline:intraday:v1', 'sector_pressure_score', 'market_context', 'intraday', 0.0600000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:intraday:derivatives_pressure_score', 'weights:product_baseline:intraday:v1', 'derivatives_pressure_score', 'derivatives', 'intraday', 0.0500000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:intraday:volatility_risk_score', 'weights:product_baseline:intraday:v1', 'volatility_risk_score', 'risk', 'intraday', 0.1600000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:product:intraday:liquidity_risk_score', 'weights:product_baseline:intraday:v1', 'liquidity_risk_score', 'risk', 'intraday', 0.1400000000, 'negative', 'identity', 0.700000, 'block_decision'),
    ('weight:product:intraday:data_quality_penalty', 'weights:product_baseline:intraday:v1', 'data_quality_penalty', 'risk', 'intraday', 0.0700000000, 'negative', 'identity', 0.800000, 'block_decision'),
    ('weight:product:intraday:portfolio_concentration_risk', 'weights:product_baseline:intraday:v1', 'portfolio_concentration_risk', 'risk', 'intraday', 0.0300000000, 'negative', 'identity', 0.800000, 'downweight'),

    ('weight:product:swing:price_strength_score', 'weights:product_baseline:swing:v1', 'price_strength_score', 'price', 'swing', 0.1600000000, 'positive', 'identity', 0.600000, 'downweight'),
    ('weight:product:swing:event_pressure_score', 'weights:product_baseline:swing:v1', 'event_pressure_score', 'event', 'swing', 0.1000000000, 'positive', 'identity', 0.700000, 'downweight'),
    ('weight:product:swing:earnings_quality_score', 'weights:product_baseline:swing:v1', 'earnings_quality_score', 'earnings', 'swing', 0.0700000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:report_sentiment_score', 'weights:product_baseline:swing:v1', 'report_sentiment_score', 'earnings', 'swing', 0.0600000000, 'positive', 'identity', 0.700000, 'downweight'),
    ('weight:product:swing:fundamental_quality_score', 'weights:product_baseline:swing:v1', 'fundamental_quality_score', 'fundamental', 'swing', 0.1000000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:valuation_attractiveness_score', 'weights:product_baseline:swing:v1', 'valuation_attractiveness_score', 'fundamental', 'swing', 0.0800000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:risk_on_risk_off_score', 'weights:product_baseline:swing:v1', 'risk_on_risk_off_score', 'market_context', 'swing', 0.0600000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:macro_pressure_score', 'weights:product_baseline:swing:v1', 'macro_pressure_score', 'market_context', 'swing', 0.0500000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:dividend_carry_score', 'weights:product_baseline:swing:v1', 'dividend_carry_score', 'dividend', 'swing', 0.0500000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:volatility_risk_score', 'weights:product_baseline:swing:v1', 'volatility_risk_score', 'risk', 'swing', 0.1300000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:product:swing:liquidity_risk_score', 'weights:product_baseline:swing:v1', 'liquidity_risk_score', 'risk', 'swing', 0.0600000000, 'negative', 'identity', 0.650000, 'block_decision'),
    ('weight:product:swing:data_quality_penalty', 'weights:product_baseline:swing:v1', 'data_quality_penalty', 'risk', 'swing', 0.0600000000, 'negative', 'identity', 0.800000, 'block_decision'),
    ('weight:product:swing:portfolio_concentration_risk', 'weights:product_baseline:swing:v1', 'portfolio_concentration_risk', 'risk', 'swing', 0.0200000000, 'negative', 'identity', 0.800000, 'downweight'),

    ('weight:product:position:fundamental_quality_score', 'weights:product_baseline:position:v1', 'fundamental_quality_score', 'fundamental', 'position', 0.1700000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:valuation_attractiveness_score', 'weights:product_baseline:position:v1', 'valuation_attractiveness_score', 'fundamental', 'position', 0.1500000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:dividend_sustainability_score', 'weights:product_baseline:position:v1', 'dividend_sustainability_score', 'dividend', 'position', 0.0700000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:dividend_carry_score', 'weights:product_baseline:position:v1', 'dividend_carry_score', 'dividend', 'position', 0.1000000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:earnings_quality_score', 'weights:product_baseline:position:v1', 'earnings_quality_score', 'earnings', 'position', 0.0600000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:risk_on_risk_off_score', 'weights:product_baseline:position:v1', 'risk_on_risk_off_score', 'market_context', 'position', 0.0600000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:macro_pressure_score', 'weights:product_baseline:position:v1', 'macro_pressure_score', 'market_context', 'position', 0.0700000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:sector_pressure_score', 'weights:product_baseline:position:v1', 'sector_pressure_score', 'market_context', 'position', 0.0400000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:price_strength_score', 'weights:product_baseline:position:v1', 'price_strength_score', 'price', 'position', 0.0400000000, 'positive', 'identity', 0.600000, 'downweight'),
    ('weight:product:position:volatility_risk_score', 'weights:product_baseline:position:v1', 'volatility_risk_score', 'risk', 'position', 0.1100000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:product:position:liquidity_risk_score', 'weights:product_baseline:position:v1', 'liquidity_risk_score', 'risk', 'position', 0.0400000000, 'negative', 'identity', 0.650000, 'block_decision'),
    ('weight:product:position:data_quality_penalty', 'weights:product_baseline:position:v1', 'data_quality_penalty', 'risk', 'position', 0.0700000000, 'negative', 'identity', 0.800000, 'block_decision'),
    ('weight:product:position:portfolio_concentration_risk', 'weights:product_baseline:position:v1', 'portfolio_concentration_risk', 'risk', 'position', 0.0200000000, 'negative', 'identity', 0.800000, 'downweight')
)
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
)
SELECT
  metric_weight_rule_id,
  weights_profile_id,
  metric_name,
  metric_group,
  horizon,
  'all',
  ARRAY[]::TEXT[],
  NULL,
  weight,
  direction,
  transform,
  min_confidence_score,
  stale_policy,
  'metric_weights_product_baseline_v1'
FROM product_rules
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weights_profile_id = EXCLUDED.weights_profile_id,
  metric_name = EXCLUDED.metric_name,
  metric_group = EXCLUDED.metric_group,
  horizon = EXCLUDED.horizon,
  instrument_scope = EXCLUDED.instrument_scope,
  instrument_ids = EXCLUDED.instrument_ids,
  sector = EXCLUDED.sector,
  weight = EXCLUDED.weight,
  direction = EXCLUDED.direction,
  transform = EXCLUDED.transform,
  min_confidence_score = EXCLUDED.min_confidence_score,
  stale_policy = EXCLUDED.stale_policy,
  calculation_version = EXCLUDED.calculation_version;

UPDATE weights.weights_profile
   SET version = '1.1',
       run_mode_allowed = ARRAY['analysis_only', 'paper_trading'],
       validation_report_ref = 'expert_analytical_baseline:liquidity_component_weights:v1'
 WHERE weights_profile_id = 'weights:liquidity_microstructure:intraday:v1';

WITH liquidity_metric_rules(metric_weight_rule_id, metric_name, weight, direction, stale_policy) AS (
  VALUES
    ('weight:liquidity:pressure:order_book_imbalance', 'order_book_imbalance', 0.4000000000, 'positive', 'downweight'),
    ('weight:liquidity:pressure:trade_imbalance', 'trade_imbalance', 0.3500000000, 'positive', 'downweight'),
    ('weight:liquidity:pressure:order_flow_imbalance', 'order_flow_imbalance', 0.2500000000, 'positive', 'downweight'),
    ('weight:liquidity:risk:spread_percentile', 'spread_percentile', 0.2500000000, 'positive', 'downweight'),
    ('weight:liquidity:risk:estimated_slippage_1m', 'estimated_slippage_1m', 0.3200000000, 'positive', 'block_decision'),
    ('weight:liquidity:risk:amihud_illiquidity', 'amihud_illiquidity', 0.1800000000, 'positive', 'downweight'),
    ('weight:liquidity:risk:order_book_depth_30bps_z', 'order_book_depth_30bps_z', 0.2500000000, 'negative', 'downweight')
)
UPDATE weights.metric_weight_rule rule
   SET weight = liquidity_metric_rules.weight,
       direction = liquidity_metric_rules.direction,
       transform = 'identity',
       stale_policy = liquidity_metric_rules.stale_policy,
       min_confidence_score = 0.700000,
       calculation_version = 'liquidity_microstructure_product_baseline_v1'
  FROM liquidity_metric_rules
 WHERE rule.metric_weight_rule_id = liquidity_metric_rules.metric_weight_rule_id
   AND rule.weights_profile_id = 'weights:liquidity_microstructure:intraday:v1';

UPDATE audit.schedule_config
   SET schedule_payload = jsonb_set(
         schedule_payload,
         '{weights_profiles}',
         '[
           "weights:product_baseline:intraday:v1",
           "weights:product_baseline:swing:v1",
           "weights:product_baseline:position:v1"
         ]'::jsonb,
         true
       )
 WHERE schedule_config_id = 'schedule:decision_engine:on_feature_update';

CREATE OR REPLACE VIEW audit.metric_weights_readiness_check AS
WITH product_profiles(weights_profile_id, horizon) AS (
  VALUES
    ('weights:product_baseline:intraday:v1', 'intraday'),
    ('weights:product_baseline:swing:v1', 'swing'),
    ('weights:product_baseline:position:v1', 'position')
),
profile_status AS (
  SELECT
    count(*) FILTER (
      WHERE profile.status = 'active'
        AND profile.run_mode_allowed = ARRAY['analysis_only', 'paper_trading']
    ) AS active_product_profiles,
    count(*) AS expected_product_profiles
  FROM product_profiles expected
  LEFT JOIN weights.weights_profile profile
    ON profile.weights_profile_id = expected.weights_profile_id
   AND profile.horizon = expected.horizon
),
rule_totals AS (
  SELECT
    expected.weights_profile_id,
    expected.horizon,
    count(rule.metric_weight_rule_id) AS rule_count,
    COALESCE(sum(rule.weight), 0) AS total_weight,
    count(*) FILTER (WHERE rule.metric_name IN ('volatility_risk_score', 'liquidity_risk_score', 'portfolio_concentration_risk', 'data_quality_penalty')) AS risk_rule_count
  FROM product_profiles expected
  LEFT JOIN weights.metric_weight_rule rule
    ON rule.weights_profile_id = expected.weights_profile_id
   AND rule.horizon = expected.horizon
  GROUP BY expected.weights_profile_id, expected.horizon
),
bad_rule_totals AS (
  SELECT array_agg(weights_profile_id ORDER BY weights_profile_id) AS bad_profiles
    FROM rule_totals
   WHERE rule_count = 0
      OR abs(total_weight - 1.0000000000) > 0.000001
      OR risk_rule_count < 4
),
strict_default_state AS (
  SELECT count(*) AS deprecated_count
    FROM weights.weights_profile
   WHERE weights_profile_id IN (
      'weights:strict_default:intraday:v1',
      'weights:strict_default:swing:v1',
      'weights:strict_default:position:v1'
   )
     AND status = 'deprecated'
),
liquidity_component_state AS (
  SELECT
    (SELECT count(*)
       FROM weights.weights_profile
      WHERE weights_profile_id = 'weights:liquidity_microstructure:intraday:v1'
        AND status = 'active'
        AND run_mode_allowed = ARRAY['analysis_only', 'paper_trading']) AS profile_count,
    (SELECT count(*)
       FROM weights.metric_weight_rule
      WHERE weights_profile_id = 'weights:liquidity_microstructure:intraday:v1'
        AND calculation_version = 'liquidity_microstructure_product_baseline_v1') AS rule_count
),
decision_schedule_state AS (
  SELECT count(*) AS schedule_count
    FROM audit.schedule_config
   WHERE schedule_config_id = 'schedule:decision_engine:on_feature_update'
     AND schedule_payload -> 'weights_profiles' ? 'weights:product_baseline:intraday:v1'
     AND schedule_payload -> 'weights_profiles' ? 'weights:product_baseline:swing:v1'
     AND schedule_payload -> 'weights_profiles' ? 'weights:product_baseline:position:v1'
),
live_profile_state AS (
  SELECT count(*) AS active_live_profiles
    FROM weights.weights_profile
   WHERE status = 'active'
     AND 'live_trading' = ANY(run_mode_allowed)
)
SELECT
  'product_profiles_active' AS check_name,
  CASE WHEN profile_status.active_product_profiles = 3 THEN 'pass' ELSE 'fail' END AS status,
  profile_status.active_product_profiles::text AS observed_value,
  '3 active product_baseline profiles for analysis_only/paper_trading' AS expected_value,
  '{}'::jsonb AS details
FROM profile_status
UNION ALL
SELECT
  'product_rule_totals',
  CASE WHEN COALESCE(array_length(bad_rule_totals.bad_profiles, 1), 0) = 0 THEN 'pass' ELSE 'fail' END,
  (3 - COALESCE(array_length(bad_rule_totals.bad_profiles, 1), 0))::text,
  '3 profiles with total weight = 1 and all 4 risk rules',
  jsonb_build_object('bad_profiles', COALESCE(bad_rule_totals.bad_profiles, ARRAY[]::text[]))
FROM bad_rule_totals
UNION ALL
SELECT
  'strict_default_deprecated',
  CASE WHEN strict_default_state.deprecated_count = 3 THEN 'pass' ELSE 'fail' END,
  strict_default_state.deprecated_count::text,
  '3 strict_default profiles deprecated',
  '{}'::jsonb
FROM strict_default_state
UNION ALL
SELECT
  'liquidity_component_product_rules',
  CASE WHEN liquidity_component_state.profile_count = 1 AND liquidity_component_state.rule_count = 7 THEN 'pass' ELSE 'fail' END,
  (liquidity_component_state.profile_count::text || ' profiles / ' || liquidity_component_state.rule_count::text || ' rules'),
  '1 active paper/analysis profile and 7 product component rules',
  '{}'::jsonb
FROM liquidity_component_state
UNION ALL
SELECT
  'decision_schedule_product_profiles',
  CASE WHEN decision_schedule_state.schedule_count = 1 THEN 'pass' ELSE 'fail' END,
  decision_schedule_state.schedule_count::text,
  'decision schedule references product_baseline profile ids',
  '{}'::jsonb
FROM decision_schedule_state
UNION ALL
SELECT
  'no_active_live_weights',
  CASE WHEN live_profile_state.active_live_profiles = 0 THEN 'pass' ELSE 'fail' END,
  live_profile_state.active_live_profiles::text,
  '0 active live_trading weights without separate governance approval',
  '{}'::jsonb
FROM live_profile_state;

CREATE OR REPLACE VIEW audit.database_readiness_check AS
WITH
required_schemas(schema_name) AS (
  VALUES
    ('registry'), ('raw_market'), ('raw_text'), ('raw_macro'), ('events'), ('features'),
    ('weights'), ('risk'), ('portfolio'), ('decisions'), ('orders'), ('request_logs'), ('audit')
),
missing_schemas AS (
  SELECT array_agg(required_schemas.schema_name ORDER BY required_schemas.schema_name) AS missing_items
    FROM required_schemas
    LEFT JOIN information_schema.schemata existing_schema
      ON existing_schema.schema_name = required_schemas.schema_name
   WHERE existing_schema.schema_name IS NULL
),
required_modules(module_name, must_be_enabled) AS (
  VALUES
    ('Orchestration Module', true),
    ('External Request Gateway Module', true),
    ('Selected Instruments Registry Module', true),
    ('Data Intake & Routing Module', true),
    ('Data Quality Module', true),
    ('Market Data Metrics Module', true),
    ('Liquidity & Microstructure Module', true),
    ('Volatility & Risk Metrics Module', true),
    ('Market Context Module', true),
    ('Fundamental & Valuation Module', true),
    ('Event & News Intelligence Module', true),
    ('Earnings & Dividend Intelligence Module', true),
    ('Corporate Actions Adjustment Module', true),
    ('Derivatives & Positioning Module', true),
    ('Normalization & Feature Vector Module', true),
    ('Feature Validation & Research Module', false),
    ('Decision Engine Module', true),
    ('Risk Control Module', true),
    ('Execution Engine Module', true),
    ('Portfolio State Module', true),
    ('Backtesting & Paper Trading Module', false),
    ('Monitoring & Audit Module', true)
),
schedule_status AS (
  SELECT
    array_agg(required_modules.module_name ORDER BY required_modules.module_name)
      FILTER (WHERE schedule_config.module_name IS NULL) AS missing_modules,
    array_agg(required_modules.module_name ORDER BY required_modules.module_name)
      FILTER (WHERE required_modules.must_be_enabled AND NOT COALESCE(schedule_config.enabled, false)) AS disabled_required_modules
  FROM required_modules
  LEFT JOIN audit.schedule_config schedule_config
    ON schedule_config.module_name = required_modules.module_name
),
active_universe AS (
  SELECT count(*) AS active_count
    FROM registry.instrument_profile
   WHERE universe_id = 'moex_top20_manual'
     AND is_active = true
),
portfolio_seed AS (
  SELECT
    (SELECT count(*) FROM portfolio.portfolio_snapshot WHERE portfolio_id = 'arena_go_default') AS snapshot_count,
    (SELECT count(*) FROM portfolio.position_state WHERE portfolio_id = 'arena_go_default') AS position_count
),
risk_seed AS (
  SELECT
    (SELECT count(*) FROM risk.risk_policy WHERE risk_policy_id = 'risk_policy:paper_trading:v1' AND status = 'active') AS active_policy_count,
    (SELECT count(*) FROM risk.instrument_limit WHERE risk_policy_id = 'risk_policy:paper_trading:v1') AS instrument_limit_count,
    (SELECT count(*) FROM risk.portfolio_limit WHERE risk_policy_id = 'risk_policy:paper_trading:v1') AS portfolio_limit_count
),
provider_seed AS (
  SELECT
    count(*) AS provider_count,
    count(*) FILTER (WHERE provider IN ('moex_iss', 'internal_cache', 'arena_go', 'polza_ai') AND enabled) AS enabled_core_provider_count
  FROM request_logs.provider_config
),
text_source_seed AS (
  SELECT count(*) AS source_count
    FROM raw_text.text_source_config
),
dependency_seed AS (
  SELECT count(*) AS active_graph_count
    FROM audit.module_dependency_graph
   WHERE status = 'active'
),
metric_weight_seed AS (
  SELECT count(*) FILTER (WHERE status = 'pass') AS passed_checks,
         count(*) AS total_checks
    FROM audit.metric_weights_readiness_check
)
SELECT
  'required_schemas' AS check_name,
  CASE WHEN COALESCE(array_length(missing_schemas.missing_items, 1), 0) = 0 THEN 'pass' ELSE 'fail' END AS status,
  (13 - COALESCE(array_length(missing_schemas.missing_items, 1), 0))::text AS observed_value,
  '13' AS expected_value,
  jsonb_build_object('missing_schemas', COALESCE(missing_schemas.missing_items, ARRAY[]::text[])) AS details
FROM missing_schemas
UNION ALL
SELECT
  'module_schedules',
  CASE
    WHEN COALESCE(array_length(schedule_status.missing_modules, 1), 0) = 0
     AND COALESCE(array_length(schedule_status.disabled_required_modules, 1), 0) = 0
    THEN 'pass'
    ELSE 'fail'
  END,
  (22 - COALESCE(array_length(schedule_status.missing_modules, 1), 0))::text,
  '22 modules; research/backtest may stay disabled',
  jsonb_build_object(
    'missing_modules', COALESCE(schedule_status.missing_modules, ARRAY[]::text[]),
    'disabled_required_modules', COALESCE(schedule_status.disabled_required_modules, ARRAY[]::text[])
  )
FROM schedule_status
UNION ALL
SELECT 'dependency_graph', CASE WHEN dependency_seed.active_graph_count >= 1 THEN 'pass' ELSE 'fail' END, dependency_seed.active_graph_count::text, '>=1 active graph', '{}'::jsonb FROM dependency_seed
UNION ALL
SELECT 'selected_universe', CASE WHEN active_universe.active_count = 18 THEN 'pass' ELSE 'fail' END, active_universe.active_count::text, '18 active instruments in moex_top20_manual', '{}'::jsonb FROM active_universe
UNION ALL
SELECT 'provider_config', CASE WHEN provider_seed.provider_count >= 9 AND provider_seed.enabled_core_provider_count = 4 THEN 'pass' ELSE 'fail' END, provider_seed.provider_count::text, '>=9 providers and enabled moex_iss/internal_cache/arena_go/polza_ai', jsonb_build_object('enabled_core_provider_count', provider_seed.enabled_core_provider_count) FROM provider_seed
UNION ALL
SELECT 'text_source_config', CASE WHEN text_source_seed.source_count >= 5 THEN 'pass' ELSE 'fail' END, text_source_seed.source_count::text, '>=5 text source configs', '{}'::jsonb FROM text_source_seed
UNION ALL
SELECT 'portfolio_seed', CASE WHEN portfolio_seed.snapshot_count >= 1 AND portfolio_seed.position_count = 18 THEN 'pass' ELSE 'fail' END, (portfolio_seed.snapshot_count::text || ' snapshots / ' || portfolio_seed.position_count::text || ' positions'), '>=1 snapshot and 18 initial positions', '{}'::jsonb FROM portfolio_seed
UNION ALL
SELECT
  'risk_policy_seed',
  CASE WHEN risk_seed.active_policy_count = 1 AND risk_seed.instrument_limit_count = 18 AND risk_seed.portfolio_limit_count >= 7 THEN 'pass' ELSE 'fail' END,
  (risk_seed.active_policy_count::text || ' active policies / ' || risk_seed.instrument_limit_count::text || ' instrument limits / ' || risk_seed.portfolio_limit_count::text || ' portfolio limits'),
  '1 active paper policy, 18 instrument limits, >=7 portfolio limits',
  '{}'::jsonb
FROM risk_seed
UNION ALL
SELECT
  'metric_weights_seed',
  CASE WHEN metric_weight_seed.passed_checks = metric_weight_seed.total_checks AND metric_weight_seed.total_checks >= 6 THEN 'pass' ELSE 'fail' END,
  (metric_weight_seed.passed_checks::text || '/' || metric_weight_seed.total_checks::text),
  'all metric weight readiness checks pass',
  '{}'::jsonb
FROM metric_weight_seed;

INSERT INTO audit.audit_record (
  module_name,
  severity,
  event_type,
  message,
  object_type,
  object_ref,
  reason_codes,
  payload
) VALUES (
  'Decision Engine Module',
  'info',
  'metric_weights_product_baseline_seeded',
  'Product baseline metric weights seeded for intraday, swing and position horizons',
  'weights_profile',
  'weights:product_baseline:v1',
  ARRAY['product_baseline_weights_active', 'strict_default_weights_deprecated', 'live_trading_weights_not_active'],
  '{"seed_version":"2026-05-22","run_modes":["analysis_only","paper_trading"],"requires_empirical_followup":true}'::jsonb
);

COMMIT;
