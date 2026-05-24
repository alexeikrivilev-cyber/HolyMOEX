BEGIN;

WITH allowed(ticker) AS (
  VALUES
    ('LKOH'), ('SBER'), ('ROSN'), ('GAZP'), ('VTBR'), ('YDEX'), ('PLZL'), ('T'), ('NVTK'), ('X5'),
    ('GMKN'), ('MGNT'), ('ALRS'), ('AFLT'), ('CHMF'), ('NLMK'), ('MOEX'), ('SNGSP'), ('MTSS'), ('PIKK')
)
UPDATE registry.instrument_profile profile
   SET is_active = true,
       tradable = true,
       execution_enabled = true,
       board_id = 'TQBR',
       class_code = 'TQBR',
       currency = 'RUB',
       arena_go_secid = COALESCE(NULLIF(profile.arena_go_secid, ''), profile.ticker),
       arena_go_quantity_mode = 'shares',
       max_trade_quantity = CASE WHEN COALESCE(profile.max_trade_quantity, 0) <= 0 THEN 100 ELSE profile.max_trade_quantity END,
       metadata = profile.metadata
         || jsonb_build_object(
              'arena_go_sandbox_allowed', true,
              'allowed_universe_version', 'automatic_live_sandbox_v1',
              'arena_go_quantity_mode', 'shares',
              'issuer_ir_url_status', COALESCE(profile.metadata ->> 'issuer_ir_url_status', 'missing_controlled_skip')
            )
  FROM allowed
 WHERE profile.universe_id = 'moex_top20_manual'
   AND profile.ticker = allowed.ticker;

WITH allowed(ticker) AS (
  VALUES
    ('LKOH'), ('SBER'), ('ROSN'), ('GAZP'), ('VTBR'), ('YDEX'), ('PLZL'), ('T'), ('NVTK'), ('X5'),
    ('GMKN'), ('MGNT'), ('ALRS'), ('AFLT'), ('CHMF'), ('NLMK'), ('MOEX'), ('SNGSP'), ('MTSS'), ('PIKK')
)
UPDATE registry.instrument_profile profile
   SET is_active = false,
       tradable = false,
       execution_enabled = false,
       metadata = profile.metadata || jsonb_build_object('outside_arena_go_sandbox_universe', true)
 WHERE profile.universe_id = 'moex_top20_manual'
   AND NOT EXISTS (SELECT 1 FROM allowed WHERE allowed.ticker = profile.ticker);

INSERT INTO portfolio.position_state (
  position_state_id,
  portfolio_id,
  instrument_id,
  as_of_ts,
  quantity,
  average_price,
  market_price,
  market_value,
  unrealized_pnl,
  source_module,
  source_refs,
  payload
)
SELECT
  'initial:arena_go_default:' || instrument_id,
  'arena_go_default',
  instrument_id,
  '2026-05-21T00:00:00Z',
  0,
  NULL,
  NULL,
  0,
  0,
  'Portfolio State Module',
  ARRAY['registry.instrument_profile', 'migration:014_automatic_live_sandbox_runtime'],
  '{"run_mode":"live_trading","system_mode":"automatic_live_trading","seed_version":"2026-05-24","arena_go_quantity_mode":"shares"}'::jsonb
FROM registry.instrument_profile
WHERE universe_id = 'moex_top20_manual'
  AND is_active = true
ON CONFLICT (position_state_id) DO UPDATE SET
  payload = portfolio.position_state.payload || EXCLUDED.payload,
  source_refs = EXCLUDED.source_refs;

INSERT INTO risk.instrument_limit (
  risk_policy_id,
  instrument_id,
  max_position_pct,
  max_order_value_rub,
  max_slippage_bps,
  payload
)
SELECT
  policy.risk_policy_id,
  profile.instrument_id,
  CASE WHEN policy.risk_policy_id = 'risk_policy:live_autonomous_turnover:v1' THEN 0.080000 ELSE 0.050000 END,
  CASE WHEN policy.risk_policy_id = 'risk_policy:live_autonomous_turnover:v1' THEN 100000 ELSE 50000 END,
  50,
  jsonb_build_object(
    'sector', profile.sector,
    'ticker', profile.ticker,
    'max_trade_quantity', profile.max_trade_quantity,
    'execution_enabled', profile.execution_enabled,
    'arena_go_secid', profile.arena_go_secid,
    'arena_go_quantity_mode', 'shares',
    'allowed_arena_go_sandbox_universe', true,
    'min_expected_edge_after_cost_required', true
  )
FROM registry.instrument_profile profile
CROSS JOIN (VALUES ('risk_policy:paper_trading:v1'), ('risk_policy:live_autonomous_turnover:v1')) AS policy(risk_policy_id)
WHERE profile.universe_id = 'moex_top20_manual'
  AND profile.is_active = true
ON CONFLICT (risk_policy_id, instrument_id) DO UPDATE SET
  max_position_pct = EXCLUDED.max_position_pct,
  max_order_value_rub = EXCLUDED.max_order_value_rub,
  max_slippage_bps = EXCLUDED.max_slippage_bps,
  payload = EXCLUDED.payload;

UPDATE request_logs.provider_config
   SET auth_value_source = 'SANDBOX_API_KEY',
       config_payload = config_payload
         || jsonb_build_object(
              'auth_fallback_value_sources', jsonb_build_array('ARENA_GO_TOKEN'),
              'sandbox_env', 'ARENA_GO_SANDBOX',
              'safe_live_submit_env', 'SAFE_LIVE_SUBMIT',
              'portfolio_source_of_truth', 'bots[].name',
              'quantity_mode', 'shares',
              'quantity_semantics', 'shares_not_lots',
              'masked_token_logging_only', true
            )
 WHERE provider = 'arena_go';

UPDATE risk.trading_mandate
   SET objective_priority = 'mandatory_turnover_constraint_after_positive_expected_return',
       rules = rules
         || '{
              "primary_objective": "maximize_portfolio_value_and_positive_expected_return",
              "turnover_target_role": "mandatory_constraint_not_alpha_objective",
              "competition_total_turnover_rub_field": "competition_total_turnover_rub",
              "total_stage_turnover_rub_field": "total_stage_turnover_rub",
              "rolling_14d_turnover_rub_field": "rolling_14d_turnover_rub",
              "target_turnover_rub": 10000000,
              "turnover_quality_score_required": true,
              "churn_penalty_score_required": true,
              "post_cost_edge_gate_required": true
            }'::jsonb
 WHERE trading_mandate_id = 'trading_mandate:live:turnover_10m_14d:v1';

UPDATE risk.risk_policy
   SET rules = rules
     || '{
          "system_mode": "automatic_live_trading",
          "sandbox_required": true,
          "safe_live_submit_allowed_in_sandbox": true,
          "primary_objective": "maximize_portfolio_value_and_positive_expected_return",
          "turnover_target_role": "mandatory_constraint_not_alpha_objective",
          "force_turnover_without_edge": false,
          "block_turnover_trade_when_post_cost_edge_non_positive": true,
          "arena_go_quantity_mode": "shares"
        }'::jsonb
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1';

CREATE OR REPLACE VIEW audit.allowed_universe_readiness_check AS
WITH allowed(ticker) AS (
  VALUES
    ('LKOH'), ('SBER'), ('ROSN'), ('GAZP'), ('VTBR'), ('YDEX'), ('PLZL'), ('T'), ('NVTK'), ('X5'),
    ('GMKN'), ('MGNT'), ('ALRS'), ('AFLT'), ('CHMF'), ('NLMK'), ('MOEX'), ('SNGSP'), ('MTSS'), ('PIKK')
),
profiles AS (
  SELECT profile.*
    FROM registry.instrument_profile profile
   WHERE profile.universe_id = 'moex_top20_manual'
),
joined AS (
  SELECT allowed.ticker AS required_ticker,
         profile.instrument_id,
         profile.ticker,
         profile.board_id,
         profile.currency,
         profile.lot_size,
         profile.min_price_increment,
         profile.aliases,
         profile.issuer_name,
         profile.arena_go_secid,
         profile.arena_go_quantity_mode,
         profile.is_active,
         profile.tradable,
         profile.execution_enabled
    FROM allowed
    LEFT JOIN profiles profile ON profile.ticker = allowed.ticker
),
outside AS (
  SELECT count(*) AS outside_count
    FROM profiles
   WHERE is_active = true
     AND NOT EXISTS (SELECT 1 FROM allowed WHERE allowed.ticker = profiles.ticker)
)
SELECT 'required_tickers_present' AS check_name,
       CASE WHEN count(*) FILTER (WHERE instrument_id IS NOT NULL) = 20 THEN 'pass' ELSE 'fail' END AS status,
       count(*) FILTER (WHERE instrument_id IS NOT NULL)::text AS observed_value,
       '20 allowed ArenaGo sandbox tickers present' AS expected_value,
       jsonb_build_object('missing_tickers', COALESCE(array_agg(required_ticker ORDER BY required_ticker) FILTER (WHERE instrument_id IS NULL), ARRAY[]::text[])) AS details
  FROM joined
UNION ALL
SELECT 'active_tradable_execution_enabled',
       CASE WHEN count(*) FILTER (WHERE is_active AND tradable AND execution_enabled) = 20 THEN 'pass' ELSE 'fail' END,
       count(*) FILTER (WHERE is_active AND tradable AND execution_enabled)::text,
       'all 20 allowed tickers active/tradable/execution_enabled',
       jsonb_build_object('not_ready', COALESCE(array_agg(required_ticker ORDER BY required_ticker) FILTER (WHERE NOT COALESCE(is_active, false) OR NOT COALESCE(tradable, false) OR NOT COALESCE(execution_enabled, false)), ARRAY[]::text[]))
  FROM joined
UNION ALL
SELECT 'no_outside_active_universe',
       CASE WHEN outside_count = 0 THEN 'pass' ELSE 'fail' END,
       outside_count::text,
       'no active instrument outside allowed ArenaGo universe',
       '{}'::jsonb
  FROM outside
UNION ALL
SELECT 'moex_equity_board_and_currency',
       CASE WHEN count(*) FILTER (WHERE board_id = 'TQBR' AND currency = 'RUB') = 20 THEN 'pass' ELSE 'fail' END,
       count(*) FILTER (WHERE board_id = 'TQBR' AND currency = 'RUB')::text,
       'all allowed equities use TQBR/RUB',
       '{}'::jsonb
  FROM joined
UNION ALL
SELECT 'arena_go_mapping_and_quantity_mode',
       CASE WHEN count(*) FILTER (WHERE arena_go_secid IS NOT NULL AND btrim(arena_go_secid) <> '' AND arena_go_quantity_mode = 'shares') = 20 THEN 'pass' ELSE 'fail' END,
       count(*) FILTER (WHERE arena_go_secid IS NOT NULL AND btrim(arena_go_secid) <> '' AND arena_go_quantity_mode = 'shares')::text,
       'all allowed tickers mapped to ArenaGo secid with shares quantity mode',
       '{}'::jsonb
  FROM joined
UNION ALL
SELECT 'registry_news_matching_fields',
       CASE WHEN count(*) FILTER (WHERE aliases IS NOT NULL AND cardinality(aliases) > 0 AND issuer_name IS NOT NULL AND btrim(issuer_name) <> '') = 20 THEN 'pass' ELSE 'fail' END,
       count(*) FILTER (WHERE aliases IS NOT NULL AND cardinality(aliases) > 0 AND issuer_name IS NOT NULL AND btrim(issuer_name) <> '')::text,
       'aliases and issuer_name filled for all allowed tickers',
       '{}'::jsonb
  FROM joined;

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
    (SELECT count(*) FROM portfolio.portfolio_snapshot WHERE portfolio_id = 'arena_go_default') AS snapshot_count,
    (SELECT count(*) FROM portfolio.position_state WHERE portfolio_id = 'arena_go_default') AS position_count
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
SELECT 'portfolio_seed', CASE WHEN portfolio_seed.snapshot_count >= 1 AND portfolio_seed.position_count = 20 THEN 'pass' ELSE 'fail' END, (portfolio_seed.snapshot_count::text || ' snapshots / ' || portfolio_seed.position_count::text || ' positions'), '>=1 snapshot and 20 initial positions', '{}'::jsonb FROM portfolio_seed
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

CREATE OR REPLACE VIEW audit.live_trading_readiness_check AS
WITH
live_risk AS (
  SELECT count(*) AS active_live_risk_count
    FROM risk.risk_policy
   WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1'
     AND status = 'active'
     AND 'live_trading' = ANY(run_mode_allowed)
     AND COALESCE((rules ->> 'global_kill_switch')::boolean, true) = false
     AND COALESCE((rules ->> 'execution_kill_switch')::boolean, true) = false
     AND COALESCE((rules ->> 'block_turnover_trade_when_post_cost_edge_non_positive')::boolean, false) = true
),
live_weights AS (
  SELECT count(*) AS active_live_weights
    FROM weights.weights_profile
   WHERE weights_profile_id IN ('weights:live_autonomous:intraday:v1', 'weights:live_autonomous:swing:v1', 'weights:live_autonomous:position:v1')
     AND status = 'active'
     AND 'live_trading' = ANY(run_mode_allowed)
),
mandate AS (
  SELECT count(*) AS mandate_count
    FROM risk.trading_mandate
   WHERE trading_mandate_id = 'trading_mandate:live:turnover_10m_14d:v1'
     AND status = 'active'
     AND target_gross_turnover_rub >= 10000000
     AND target_window_days = 14
     AND objective_priority = 'mandatory_turnover_constraint_after_positive_expected_return'
),
providers AS (
  SELECT count(*) FILTER (WHERE provider = 'arena_go' AND enabled AND auth_value_source = 'SANDBOX_API_KEY') AS arena_go_enabled,
         count(*) FILTER (WHERE provider = 'moex_iss' AND enabled) AS moex_enabled
    FROM request_logs.provider_config
),
universe AS (
  SELECT count(*) AS active_instruments,
         count(*) FILTER (WHERE tradable = true AND execution_enabled = true AND arena_go_quantity_mode = 'shares') AS tradable_instruments
    FROM registry.instrument_profile
   WHERE universe_id = 'moex_top20_manual'
     AND is_active = true
),
limits AS (
  SELECT (SELECT count(*) FROM risk.instrument_limit WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1') AS instrument_limits,
         (SELECT count(*) FROM risk.portfolio_limit WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1') AS portfolio_limits
),
schedules AS (
  SELECT count(*) AS enabled_live_schedules
    FROM audit.schedule_config
   WHERE schedule_config_id LIKE 'schedule:live_autonomous:%'
     AND enabled = true
)
SELECT 'active_live_risk_policy' AS check_name,
       CASE WHEN active_live_risk_count = 1 THEN 'pass' ELSE 'fail' END AS status,
       active_live_risk_count::text AS observed_value,
       '1 active live risk policy with kill switches false and post-cost edge gate' AS expected_value,
       '{}'::jsonb AS details
  FROM live_risk
UNION ALL
SELECT 'active_live_weights', CASE WHEN active_live_weights = 3 THEN 'pass' ELSE 'fail' END, active_live_weights::text, '3 active live weights', '{}'::jsonb FROM live_weights
UNION ALL
SELECT 'turnover_mandate', CASE WHEN mandate_count = 1 THEN 'pass' ELSE 'fail' END, mandate_count::text, 'active 10M/14d turnover mandate as mandatory constraint after positive expected return', '{}'::jsonb FROM mandate
UNION ALL
SELECT 'provider_config', CASE WHEN arena_go_enabled = 1 AND moex_enabled = 1 THEN 'pass' ELSE 'fail' END, (arena_go_enabled::text || ' arena_go / ' || moex_enabled::text || ' moex_iss'), 'arena_go sandbox auth and moex_iss enabled', '{}'::jsonb FROM providers
UNION ALL
SELECT 'selected_universe_tradable', CASE WHEN active_instruments = 20 AND tradable_instruments = 20 THEN 'pass' ELSE 'fail' END, (tradable_instruments::text || '/' || active_instruments::text), 'all 20 active instruments tradable/execution_enabled with shares quantity mode', '{}'::jsonb FROM universe
UNION ALL
SELECT 'live_limits', CASE WHEN instrument_limits = 20 AND portfolio_limits >= 8 THEN 'pass' ELSE 'fail' END, (instrument_limits::text || ' instrument / ' || portfolio_limits::text || ' portfolio'), '20 instrument and >=8 portfolio live limits present', '{}'::jsonb FROM limits
UNION ALL
SELECT 'live_schedules', CASE WHEN enabled_live_schedules >= 6 THEN 'pass' ELSE 'fail' END, enabled_live_schedules::text, '>=6 enabled live autonomous schedules', '{}'::jsonb FROM schedules;

INSERT INTO audit.audit_record (module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload)
VALUES (
  'Server Startup',
  'info',
  'automatic_live_sandbox_runtime_applied',
  'Automatic live trading sandbox runtime was made primary: 20 allowed ArenaGo instruments, SANDBOX_API_KEY primary token source, shares quantity mode, post-cost edge turnover mandate and readiness views.',
  'migration',
  '014_automatic_live_sandbox_runtime',
  ARRAY['automatic_live_trading', 'arena_go_sandbox', 'allowed_universe_20', 'post_cost_edge_required'],
  '{"system_mode":"automatic_live_trading","run_mode":"live_trading","target_turnover_rub":10000000,"initial_capital_rub":1000000}'::jsonb
);

COMMIT;
