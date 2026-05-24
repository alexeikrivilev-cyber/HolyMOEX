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

UPDATE request_logs.provider_config
   SET config_payload = config_payload
     || jsonb_build_object(
          'portfolio_source_of_truth', 'bots[].name',
          'quantity_mode', 'shares',
          'allowed_request_types', jsonb_build_array('submit_order', 'get_trades', 'get_positions', 'get_bots')
        )
 WHERE provider = 'arena_go';

UPDATE request_logs.provider_config
   SET config_payload = config_payload
     || jsonb_build_object(
          'allowed_request_types', jsonb_build_array('llm_completion', 'models'),
          'model_healthcheck_mode', 'models_or_strict_json_completion'
        )
 WHERE provider = 'polza_ai';

UPDATE request_logs.provider_config
   SET config_payload = config_payload
     || jsonb_build_object(
          'blocked_sources_are_unhealthy_not_module_failure', true,
          'official_fallback_sources', jsonb_build_array('prime_disclosure', 'akm_disclosure', 'corporate_site', 'public_news_confirmation')
        )
 WHERE provider = 'issuer_disclosure';

CREATE TABLE IF NOT EXISTS audit.scheduler_tick_lock (
  schedule_config_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  locked_until TIMESTAMPTZ NOT NULL,
  last_tick_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tick_count BIGINT NOT NULL DEFAULT 0,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_scheduler_tick_lock_until
  ON audit.scheduler_tick_lock (locked_until);

CREATE OR REPLACE VIEW audit.registry_reconciliation_report AS
SELECT
  instrument_id,
  ticker,
  board_id,
  isin,
  arena_go_secid,
  aliases,
  issuer_name,
  is_active,
  tradable,
  lot_size,
  min_price_increment,
  currency,
  metadata ->> 'issuer_ir_url' AS issuer_ir_url,
  array_remove(ARRAY[
    CASE WHEN isin IS NULL OR btrim(isin) = '' THEN 'missing_isin' END,
    CASE WHEN arena_go_secid IS NULL OR btrim(arena_go_secid) = '' THEN 'missing_arena_go_secid' END,
    CASE WHEN aliases IS NULL OR cardinality(aliases) = 0 THEN 'missing_aliases' END,
    CASE WHEN metadata ->> 'issuer_ir_url' IS NULL OR btrim(metadata ->> 'issuer_ir_url') = '' THEN 'missing_issuer_ir_url' END,
    CASE WHEN NOT is_active THEN 'inactive' END,
    CASE WHEN NOT tradable THEN 'non_tradable' END,
    CASE WHEN board_id IS DISTINCT FROM 'TQBR' THEN 'suspicious_board_id' END,
    CASE WHEN lot_size IS NULL OR lot_size <= 0 THEN 'suspicious_lot_size' END,
    CASE WHEN min_price_increment IS NULL OR min_price_increment <= 0 THEN 'suspicious_min_price_increment' END,
    CASE WHEN currency IS DISTINCT FROM 'RUB' THEN 'suspicious_currency' END
  ], NULL) AS reconciliation_flags,
  metadata AS source_metadata
FROM registry.instrument_profile
WHERE universe_id = 'moex_top20_manual';

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
  CASE WHEN metric_weight_seed.passed_checks = metric_weight_seed.total_checks AND metric_weight_seed.total_checks >= 5 THEN 'pass' ELSE 'fail' END,
  (metric_weight_seed.passed_checks::text || '/' || metric_weight_seed.total_checks::text),
  'all metric weight readiness checks pass',
  '{}'::jsonb
FROM metric_weight_seed;

INSERT INTO audit.audit_record (module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload)
VALUES (
  'Orchestration Module',
  'info',
  'predfinal_runtime_hardening_applied',
  'Predfinal runtime hardening added source_missing_endpoint skip reason, normalized live weights, and aligned database readiness with live weight readiness checks.',
  'migration',
  '013_predfinal_runtime_hardening',
  ARRAY['source_schema_aligned', 'live_weights_normalized', 'readiness_hardened', 'database_readiness_aligned'],
  '{}'::jsonb
);

COMMIT;
