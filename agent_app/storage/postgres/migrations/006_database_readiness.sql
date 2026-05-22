BEGIN;

UPDATE weights.weights_profile
   SET run_mode_allowed = ARRAY['analysis_only', 'paper_trading']
 WHERE weights_profile_id = 'weights:liquidity_microstructure:intraday:v1'
   AND status = 'active';

CREATE OR REPLACE VIEW audit.database_readiness_check AS
WITH
required_schemas(schema_name) AS (
  VALUES
    ('registry'),
    ('raw_market'),
    ('raw_text'),
    ('raw_macro'),
    ('events'),
    ('features'),
    ('weights'),
    ('risk'),
    ('portfolio'),
    ('decisions'),
    ('orders'),
    ('request_logs'),
    ('audit')
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
strict_weight_totals AS (
  SELECT
    profile.weights_profile_id,
    profile.horizon,
    count(rule.metric_weight_rule_id) AS rule_count,
    sum(rule.weight) AS total_weight
  FROM weights.weights_profile profile
  LEFT JOIN weights.metric_weight_rule rule
    ON rule.weights_profile_id = profile.weights_profile_id
  WHERE profile.weights_profile_id IN (
    'weights:strict_default:intraday:v1',
    'weights:strict_default:swing:v1',
    'weights:strict_default:position:v1'
  )
  GROUP BY profile.weights_profile_id, profile.horizon
),
bad_strict_weight_totals AS (
  SELECT array_agg(weights_profile_id ORDER BY weights_profile_id) AS bad_profiles
    FROM strict_weight_totals
   WHERE rule_count = 0
      OR abs(total_weight - 1.0000000000) > 0.000001
),
liquidity_component_profile AS (
  SELECT count(*) AS profile_count
    FROM weights.weights_profile
   WHERE weights_profile_id = 'weights:liquidity_microstructure:intraday:v1'
     AND status = 'active'
     AND run_mode_allowed = ARRAY['analysis_only', 'paper_trading']
),
liquidity_component_rules AS (
  SELECT count(*) AS rule_count
    FROM weights.metric_weight_rule
   WHERE weights_profile_id = 'weights:liquidity_microstructure:intraday:v1'
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
SELECT
  'dependency_graph',
  CASE WHEN dependency_seed.active_graph_count >= 1 THEN 'pass' ELSE 'fail' END,
  dependency_seed.active_graph_count::text,
  '>=1 active graph',
  '{}'::jsonb
FROM dependency_seed
UNION ALL
SELECT
  'selected_universe',
  CASE WHEN active_universe.active_count = 18 THEN 'pass' ELSE 'fail' END,
  active_universe.active_count::text,
  '18 active instruments in moex_top20_manual',
  '{}'::jsonb
FROM active_universe
UNION ALL
SELECT
  'provider_config',
  CASE WHEN provider_seed.provider_count >= 9 AND provider_seed.enabled_core_provider_count = 4 THEN 'pass' ELSE 'fail' END,
  provider_seed.provider_count::text,
  '>=9 providers and enabled moex_iss/internal_cache/arena_go/polza_ai',
  jsonb_build_object('enabled_core_provider_count', provider_seed.enabled_core_provider_count)
FROM provider_seed
UNION ALL
SELECT
  'text_source_config',
  CASE WHEN text_source_seed.source_count >= 5 THEN 'pass' ELSE 'fail' END,
  text_source_seed.source_count::text,
  '>=5 text source configs',
  '{}'::jsonb
FROM text_source_seed
UNION ALL
SELECT
  'portfolio_seed',
  CASE WHEN portfolio_seed.snapshot_count >= 1 AND portfolio_seed.position_count = 18 THEN 'pass' ELSE 'fail' END,
  (portfolio_seed.snapshot_count::text || ' snapshots / ' || portfolio_seed.position_count::text || ' positions'),
  '>=1 snapshot and 18 initial positions',
  '{}'::jsonb
FROM portfolio_seed
UNION ALL
SELECT
  'risk_policy_seed',
  CASE
    WHEN risk_seed.active_policy_count = 1
     AND risk_seed.instrument_limit_count = 18
     AND risk_seed.portfolio_limit_count >= 7
    THEN 'pass'
    ELSE 'fail'
  END,
  (
    risk_seed.active_policy_count::text || ' active policies / ' ||
    risk_seed.instrument_limit_count::text || ' instrument limits / ' ||
    risk_seed.portfolio_limit_count::text || ' portfolio limits'
  ),
  '1 active paper policy, 18 instrument limits, >=7 portfolio limits',
  '{}'::jsonb
FROM risk_seed
UNION ALL
SELECT
  'decision_weights_seed',
  CASE WHEN COALESCE(array_length(bad_strict_weight_totals.bad_profiles, 1), 0) = 0 THEN 'pass' ELSE 'fail' END,
  (3 - COALESCE(array_length(bad_strict_weight_totals.bad_profiles, 1), 0))::text,
  '3 active strict_default profiles with total decision weight = 1',
  jsonb_build_object('bad_profiles', COALESCE(bad_strict_weight_totals.bad_profiles, ARRAY[]::text[]))
FROM bad_strict_weight_totals
UNION ALL
SELECT
  'liquidity_component_weights_seed',
  CASE
    WHEN liquidity_component_profile.profile_count = 1
     AND liquidity_component_rules.rule_count = 7
    THEN 'pass'
    ELSE 'fail'
  END,
  (liquidity_component_profile.profile_count::text || ' profiles / ' || liquidity_component_rules.rule_count::text || ' rules'),
  '1 active paper/analysis profile and 7 component rules',
  '{}'::jsonb
FROM liquidity_component_profile
CROSS JOIN liquidity_component_rules;

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
  'Monitoring & Audit Module',
  'info',
  'database_readiness_view_seeded',
  'Database readiness view and paper-first liquidity component profile boundary seeded',
  'database_migration',
  '006_database_readiness',
  ARRAY['database_readiness_check_available', 'paper_first_weights_boundary'],
  '{"seed_version":"2026-05-22","view":"audit.database_readiness_check"}'::jsonb
);

COMMIT;
