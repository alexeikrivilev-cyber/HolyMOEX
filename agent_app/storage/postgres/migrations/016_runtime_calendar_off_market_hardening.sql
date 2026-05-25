BEGIN;

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload || jsonb_build_object('run_mode', 'live_trading')
 WHERE schedule_payload ->> 'run_mode' = 'paper_trading';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'market_session_required', 'open',
          'skip_when_market_session_status_in', jsonb_build_array('closed', 'premarket', 'postmarket', 'unknown'),
          'agent_runtime_phase_required', 'trading_session',
          'off_market_policy', 'skip_scheduled_loop'
        )
 WHERE module_name NOT IN ('Portfolio State Module', 'Monitoring & Audit Module');

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'off_market_policy', 'allowed_for_portfolio_sync_or_monitoring',
          'allowed_agent_runtime_phase', jsonb_build_array('trading_session', 'off_market', 'degraded')
        )
 WHERE module_name IN ('Portfolio State Module', 'Monitoring & Audit Module');

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
active_universe AS (
  SELECT count(*) AS active_count
    FROM registry.instrument_profile
   WHERE universe_id = 'moex_top20_manual'
     AND is_active = true
),
portfolio_seed AS (
  SELECT
    (SELECT count(*) FROM portfolio.portfolio_snapshot) AS snapshot_count,
    (SELECT count(DISTINCT instrument_id) FROM portfolio.position_state) AS distinct_position_count,
    (SELECT count(*) FROM portfolio.position_state) AS historical_position_rows
),
risk_seed AS (
  SELECT
    (SELECT count(*) FROM risk.risk_policy WHERE risk_policy_id = 'risk_policy:paper_trading:v1' AND status = 'active') AS active_policy_count,
    (SELECT count(*) FROM risk.instrument_limit WHERE risk_policy_id = 'risk_policy:paper_trading:v1') AS paper_instrument_limit_count,
    (SELECT count(*) FROM risk.instrument_limit WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1') AS live_instrument_limit_count,
    (SELECT count(*) FROM risk.portfolio_limit WHERE risk_policy_id = 'risk_policy:paper_trading:v1') AS portfolio_limit_count
),
provider_seed AS (
  SELECT
    count(*) AS provider_count,
    count(*) FILTER (WHERE provider IN ('moex_iss', 'internal_cache', 'arena_go', 'polza_ai') AND enabled) AS enabled_core_provider_count,
    count(*) FILTER (WHERE provider = 'arena_go' AND auth_value_source = 'SANDBOX_API_KEY') AS arena_go_sandbox_auth_count
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
),
allowed_universe_seed AS (
  SELECT count(*) FILTER (WHERE status = 'pass') AS passed_checks,
         count(*) AS total_checks
    FROM audit.allowed_universe_readiness_check
)
SELECT
  'required_schemas' AS check_name,
  CASE WHEN COALESCE(array_length(missing_schemas.missing_items, 1), 0) = 0 THEN 'pass' ELSE 'fail' END AS status,
  (13 - COALESCE(array_length(missing_schemas.missing_items, 1), 0))::text AS observed_value,
  '13' AS expected_value,
  jsonb_build_object('missing_schemas', COALESCE(missing_schemas.missing_items, ARRAY[]::text[])) AS details
FROM missing_schemas
UNION ALL
SELECT 'dependency_graph', CASE WHEN dependency_seed.active_graph_count >= 1 THEN 'pass' ELSE 'fail' END, dependency_seed.active_graph_count::text, '>=1 active graph', '{}'::jsonb FROM dependency_seed
UNION ALL
SELECT 'selected_universe', CASE WHEN active_universe.active_count = 20 THEN 'pass' ELSE 'fail' END, active_universe.active_count::text, '20 active instruments in allowed ArenaGo universe', '{}'::jsonb FROM active_universe
UNION ALL
SELECT 'allowed_universe', CASE WHEN allowed_universe_seed.passed_checks = allowed_universe_seed.total_checks AND allowed_universe_seed.total_checks >= 6 THEN 'pass' ELSE 'fail' END, (allowed_universe_seed.passed_checks::text || '/' || allowed_universe_seed.total_checks::text), 'all allowed universe readiness rows pass', '{}'::jsonb FROM allowed_universe_seed
UNION ALL
SELECT 'provider_config', CASE WHEN provider_seed.provider_count >= 9 AND provider_seed.enabled_core_provider_count = 4 AND provider_seed.arena_go_sandbox_auth_count = 1 THEN 'pass' ELSE 'fail' END, provider_seed.provider_count::text, '>=9 providers, core providers enabled, ArenaGo SANDBOX_API_KEY primary', jsonb_build_object('enabled_core_provider_count', provider_seed.enabled_core_provider_count, 'arena_go_sandbox_auth_count', provider_seed.arena_go_sandbox_auth_count) FROM provider_seed
UNION ALL
SELECT 'text_source_config', CASE WHEN text_source_seed.source_count >= 5 THEN 'pass' ELSE 'fail' END, text_source_seed.source_count::text, '>=5 text source configs', '{}'::jsonb FROM text_source_seed
UNION ALL
SELECT
  'portfolio_seed',
  CASE WHEN portfolio_seed.snapshot_count >= 1 AND portfolio_seed.distinct_position_count >= 20 THEN 'pass' ELSE 'fail' END,
  (portfolio_seed.snapshot_count::text || ' snapshots / ' || portfolio_seed.distinct_position_count::text || ' distinct positions / ' || portfolio_seed.historical_position_rows::text || ' historical position rows'),
  '>=1 snapshot and >=20 distinct instrument positions; historical rows may grow after restarts',
  jsonb_build_object('historical_position_rows', portfolio_seed.historical_position_rows)
FROM portfolio_seed
UNION ALL
SELECT
  'risk_policy_seed',
  CASE WHEN risk_seed.active_policy_count = 1 AND risk_seed.paper_instrument_limit_count = 20 AND risk_seed.live_instrument_limit_count = 20 AND risk_seed.portfolio_limit_count >= 7 THEN 'pass' ELSE 'fail' END,
  (risk_seed.active_policy_count::text || ' active policies / ' || risk_seed.paper_instrument_limit_count::text || ' paper limits / ' || risk_seed.live_instrument_limit_count::text || ' live limits / ' || risk_seed.portfolio_limit_count::text || ' portfolio limits'),
  '1 active paper policy, 20 paper instrument limits, 20 live instrument limits, >=7 portfolio limits',
  '{}'::jsonb
FROM risk_seed
UNION ALL
SELECT
  'metric_weights_seed',
  CASE WHEN metric_weight_seed.passed_checks = metric_weight_seed.total_checks AND metric_weight_seed.total_checks >= 5 THEN 'pass' ELSE 'fail' END,
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
  'Orchestration Module',
  'info',
  'runtime_calendar_off_market_hardening_applied',
  'Scheduler runtime is hardened for exchange-calendar gating and persistent portfolio readiness.',
  'migration',
  '016_runtime_calendar_off_market_hardening',
  ARRAY['off_market_gating', 'live_runtime_only', 'persistent_portfolio_readiness'],
  '{
    "off_market_allowed_modules": ["Portfolio State Module", "Monitoring & Audit Module"],
    "legacy_paper_schedule_payloads_normalized": true,
    "portfolio_readiness_uses_distinct_positions": true
  }'::jsonb
);

COMMIT;
