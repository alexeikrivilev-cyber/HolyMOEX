BEGIN;

CREATE TABLE IF NOT EXISTS risk.trading_mandate (
  trading_mandate_id TEXT PRIMARY KEY,
  mandate_name TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'deprecated', 'archived')),
  run_mode TEXT NOT NULL,
  initial_capital_rub NUMERIC(24, 6) NOT NULL,
  target_gross_turnover_rub NUMERIC(24, 6) NOT NULL,
  target_window_days INTEGER NOT NULL,
  target_turnover_ratio NUMERIC(18, 8) NOT NULL,
  objective_priority TEXT NOT NULL,
  rules JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_by TEXT,
  validation_report_ref TEXT
);

INSERT INTO risk.trading_mandate (
  trading_mandate_id,
  mandate_name,
  version,
  status,
  run_mode,
  initial_capital_rub,
  target_gross_turnover_rub,
  target_window_days,
  target_turnover_ratio,
  objective_priority,
  rules,
  approved_by,
  validation_report_ref
) VALUES (
  'trading_mandate:live:turnover_10m_14d:v1',
  'autonomous_live_turnover_10m_14d',
  '1.0',
  'active',
  'live_trading',
  1000000,
  10000000,
  14,
  10.0,
  'secondary_after_risk_and_positive_expected_edge',
  '{
    "autonomous_live_trading": true,
    "force_turnover_without_edge": false,
    "gross_turnover_formula": "sum(abs(filled_quantity * avg_fill_price))",
    "turnover_source_of_truth": ["orders.fill_report", "orders.execution_result", "ArenaGo get_trades"],
    "turnover_status_values": ["on_track", "behind", "critically_behind", "achieved"]
  }'::jsonb,
  'owner_governance_seed',
  'validation:live_turnover_mandate:bootstrap:v1'
)
ON CONFLICT (trading_mandate_id) DO UPDATE SET
  status = EXCLUDED.status,
  rules = EXCLUDED.rules,
  approved_by = EXCLUDED.approved_by,
  validation_report_ref = EXCLUDED.validation_report_ref;

INSERT INTO weights.weights_profile (
  weights_profile_id, profile_name, version, status, horizon,
  run_mode_allowed, approved_by, validation_report_ref
) VALUES
  ('weights:live_autonomous:intraday:v1', 'live_autonomous_turnover_aware', '1.0', 'active', 'intraday', ARRAY['live_trading'], 'owner_governance_seed', 'validation:live_autonomous:bootstrap:v1'),
  ('weights:live_autonomous:swing:v1', 'live_autonomous_turnover_aware', '1.0', 'active', 'swing', ARRAY['live_trading'], 'owner_governance_seed', 'validation:live_autonomous:bootstrap:v1'),
  ('weights:live_autonomous:position:v1', 'live_autonomous_turnover_aware', '1.0', 'active', 'position', ARRAY['live_trading'], 'owner_governance_seed', 'validation:live_autonomous:bootstrap:v1')
ON CONFLICT (weights_profile_id) DO UPDATE SET
  status = EXCLUDED.status,
  run_mode_allowed = EXCLUDED.run_mode_allowed,
  approved_by = EXCLUDED.approved_by,
  validation_report_ref = EXCLUDED.validation_report_ref;

WITH profile_map(source_profile, target_profile) AS (
  VALUES
    ('weights:product_baseline:intraday:v1', 'weights:live_autonomous:intraday:v1'),
    ('weights:product_baseline:swing:v1', 'weights:live_autonomous:swing:v1'),
    ('weights:product_baseline:position:v1', 'weights:live_autonomous:position:v1')
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
  'weight:live_autonomous:' || source_rule.horizon || ':' || source_rule.metric_name,
  profile_map.target_profile,
  source_rule.metric_name,
  source_rule.metric_group,
  source_rule.horizon,
  source_rule.instrument_scope,
  source_rule.instrument_ids,
  source_rule.sector,
  source_rule.weight,
  source_rule.direction,
  source_rule.transform,
  source_rule.min_confidence_score,
  source_rule.stale_policy,
  'live_autonomous_turnover_baseline_v1'
FROM weights.metric_weight_rule source_rule
JOIN profile_map ON profile_map.source_profile = source_rule.weights_profile_id
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weights_profile_id = EXCLUDED.weights_profile_id,
  weight = EXCLUDED.weight,
  min_confidence_score = EXCLUDED.min_confidence_score,
  stale_policy = EXCLUDED.stale_policy,
  calculation_version = EXCLUDED.calculation_version;

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
  ('weight:live_autonomous:intraday:turnover_deficit_score', 'weights:live_autonomous:intraday:v1', 'turnover_deficit_score', 'portfolio', 'intraday', 'all', ARRAY[]::TEXT[], NULL, 0.0500000000, 'positive', 'identity', 0.800000, 'downweight', 'live_autonomous_turnover_baseline_v1'),
  ('weight:live_autonomous:intraday:trade_urgency_score', 'weights:live_autonomous:intraday:v1', 'trade_urgency_score', 'portfolio', 'intraday', 'all', ARRAY[]::TEXT[], NULL, 0.0500000000, 'positive', 'identity', 0.800000, 'downweight', 'live_autonomous_turnover_baseline_v1'),
  ('weight:live_autonomous:intraday:churn_penalty_score', 'weights:live_autonomous:intraday:v1', 'churn_penalty_score', 'portfolio', 'intraday', 'all', ARRAY[]::TEXT[], NULL, 0.0500000000, 'negative', 'identity', 0.800000, 'downweight', 'live_autonomous_turnover_baseline_v1'),
  ('weight:live_autonomous:swing:turnover_deficit_score', 'weights:live_autonomous:swing:v1', 'turnover_deficit_score', 'portfolio', 'swing', 'all', ARRAY[]::TEXT[], NULL, 0.0300000000, 'positive', 'identity', 0.800000, 'downweight', 'live_autonomous_turnover_baseline_v1'),
  ('weight:live_autonomous:swing:churn_penalty_score', 'weights:live_autonomous:swing:v1', 'churn_penalty_score', 'portfolio', 'swing', 'all', ARRAY[]::TEXT[], NULL, 0.0300000000, 'negative', 'identity', 0.800000, 'downweight', 'live_autonomous_turnover_baseline_v1')
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weights_profile_id = EXCLUDED.weights_profile_id,
  weight = EXCLUDED.weight,
  direction = EXCLUDED.direction,
  calculation_version = EXCLUDED.calculation_version;

INSERT INTO risk.risk_policy (
  risk_policy_id,
  policy_name,
  version,
  status,
  run_mode_allowed,
  rules,
  approved_by
) VALUES (
  'risk_policy:live_autonomous_turnover:v1',
  'live_autonomous_turnover_guardrails',
  '1.0',
  'active',
  ARRAY['live_trading'],
  '{
    "global_kill_switch": false,
    "execution_kill_switch": false,
    "autonomous_live_trading": true,
    "turnover_mandate_id": "trading_mandate:live:turnover_10m_14d:v1",
    "initial_capital_rub": 1000000,
    "target_gross_turnover_rub_14d": 10000000,
    "target_turnover_ratio_14d": 10.0,
    "turnover_objective_priority": "secondary_after_risk_and_positive_expected_edge",
    "max_portfolio_gross_exposure_pct": 0.80,
    "max_portfolio_net_exposure_pct": 0.80,
    "max_instrument_position_pct": 0.08,
    "max_sector_exposure_pct": 0.25,
    "max_order_value_rub": 100000,
    "max_daily_turnover_rub": 1500000,
    "max_daily_loss_pct": 0.02,
    "max_drawdown_pct": 0.05,
    "min_expected_edge_after_cost_score": 0.000001,
    "min_data_quality_score": 0.80,
    "max_spread_bps": 80,
    "max_slippage_bps": 50,
    "stale_portfolio_max_age_seconds": 300,
    "stale_feature_policy": "block_decision",
    "market_session_required": "open",
    "arena_go_daily_trade_limit_env": "ARENA_GO_DAILY_TRADE_LIMIT"
  }'::jsonb,
  'owner_governance_seed'
)
ON CONFLICT (risk_policy_id) DO UPDATE SET
  status = EXCLUDED.status,
  run_mode_allowed = EXCLUDED.run_mode_allowed,
  rules = EXCLUDED.rules,
  approved_by = EXCLUDED.approved_by;

INSERT INTO risk.portfolio_limit (risk_policy_id, limit_name, limit_value, payload) VALUES
  ('risk_policy:live_autonomous_turnover:v1', 'max_portfolio_gross_exposure_pct', 0.8000000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_portfolio_net_exposure_pct', 0.8000000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_sector_exposure_pct', 0.2500000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_daily_loss_pct', 0.0200000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_drawdown_pct', 0.0500000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'min_data_quality_score', 0.8000000000, '{"unit":"score"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_order_value_rub', 100000, '{"unit":"RUB"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'max_daily_turnover_rub', 1500000, '{"unit":"RUB"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'min_expected_edge_after_cost_score', 0.000001, '{"unit":"score"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'arena_go_daily_trade_limit', 1000, '{"unit":"orders","env":"ARENA_GO_DAILY_TRADE_LIMIT"}'::jsonb),
  ('risk_policy:live_autonomous_turnover:v1', 'portfolio_snapshot_ttl_seconds', 300, '{"unit":"seconds"}'::jsonb)
ON CONFLICT (risk_policy_id, limit_name) DO UPDATE SET
  limit_value = EXCLUDED.limit_value,
  payload = EXCLUDED.payload;

INSERT INTO risk.instrument_limit (
  risk_policy_id,
  instrument_id,
  max_position_pct,
  max_order_value_rub,
  max_slippage_bps,
  payload
)
SELECT
  'risk_policy:live_autonomous_turnover:v1',
  instrument_id,
  0.080000,
  100000,
  50,
  jsonb_build_object(
    'arena_go_secid', COALESCE(ticker, instrument_id),
    'max_spread_bps', 80,
    'min_liquidity_source', 'moex_iss_public_market_data'
  )
FROM registry.instrument_profile
WHERE universe_id = 'moex_top20_manual'
  AND is_active = true
ON CONFLICT (risk_policy_id, instrument_id) DO UPDATE SET
  max_position_pct = EXCLUDED.max_position_pct,
  max_order_value_rub = EXCLUDED.max_order_value_rub,
  max_slippage_bps = EXCLUDED.max_slippage_bps,
  payload = EXCLUDED.payload;

INSERT INTO audit.schedule_config (schedule_config_id, module_name, contour, schedule_payload, enabled) VALUES
  ('schedule:live_autonomous:portfolio_sync:1m', 'Portfolio State Module', 'execution_contour', '{"trigger":"scheduled_portfolio_sync","run_mode":"live_trading","interval_seconds":60,"provider":"arena_go","required_before_decision":true}'::jsonb, true),
  ('schedule:live_autonomous:market_data:1m', 'Market Data Metrics Module', 'realtime_contour', '{"trigger":"scheduled_market_data_refresh","run_mode":"live_trading","interval_seconds":60,"provider":"moex_iss"}'::jsonb, true),
  ('schedule:live_autonomous:decision:1m', 'Decision Engine Module', 'decision_contour', '{"trigger":"on_feature_update_or_timer","run_mode":"live_trading","interval_seconds":60,"weights_profiles":["weights:live_autonomous:intraday:v1","weights:live_autonomous:swing:v1","weights:live_autonomous:position:v1"]}'::jsonb, true),
  ('schedule:live_autonomous:risk:on_decision', 'Risk Control Module', 'decision_contour', '{"trigger":"on_decision_set","run_mode":"live_trading","risk_policy_id":"risk_policy:live_autonomous_turnover:v1"}'::jsonb, true),
  ('schedule:live_autonomous:execution:on_approved', 'Execution Engine Module', 'execution_contour', '{"trigger":"on_approved_order","run_mode":"live_trading","provider":"arena_go"}'::jsonb, true),
  ('schedule:live_autonomous:monitoring:1m', 'Monitoring & Audit Module', 'monitoring_contour', '{"trigger":"scheduled_live_monitoring","run_mode":"live_trading","interval_seconds":60,"checks":["turnover_target","risk","provider_health","portfolio_freshness"]}'::jsonb, true)
ON CONFLICT (schedule_config_id) DO UPDATE SET
  schedule_payload = EXCLUDED.schedule_payload,
  enabled = EXCLUDED.enabled;

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
product_profile_state AS (
  SELECT count(*) AS active_product_profiles
    FROM weights.weights_profile
   WHERE weights_profile_id IN ('weights:product_baseline:intraday:v1', 'weights:product_baseline:swing:v1', 'weights:product_baseline:position:v1')
     AND status = 'active'
)
SELECT 'product_profiles_active' AS check_name,
       CASE WHEN active_product_profiles = 3 THEN 'pass' ELSE 'fail' END AS status,
       active_product_profiles::text AS observed_value,
       '3 active product baseline profiles' AS expected_value,
       '{}'::jsonb AS details
  FROM product_profile_state
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
  FROM live_rule_state;

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
),
providers AS (
  SELECT count(*) FILTER (WHERE provider = 'arena_go' AND enabled) AS arena_go_enabled,
         count(*) FILTER (WHERE provider = 'moex_iss' AND enabled) AS moex_enabled
    FROM request_logs.provider_config
),
universe AS (
  SELECT count(*) AS active_instruments,
         count(*) FILTER (WHERE tradable = true) AS tradable_instruments
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
       '1 active live risk policy with kill switches false' AS expected_value,
       '{}'::jsonb AS details
  FROM live_risk
UNION ALL
SELECT 'active_live_weights', CASE WHEN active_live_weights = 3 THEN 'pass' ELSE 'fail' END, active_live_weights::text, '3 active live weights', '{}'::jsonb FROM live_weights
UNION ALL
SELECT 'turnover_mandate', CASE WHEN mandate_count = 1 THEN 'pass' ELSE 'fail' END, mandate_count::text, 'active 10M/14d turnover mandate', '{}'::jsonb FROM mandate
UNION ALL
SELECT 'provider_config', CASE WHEN arena_go_enabled = 1 AND moex_enabled = 1 THEN 'pass' ELSE 'fail' END, (arena_go_enabled::text || ' arena_go / ' || moex_enabled::text || ' moex_iss'), 'arena_go and moex_iss enabled', '{}'::jsonb FROM providers
UNION ALL
SELECT 'selected_universe_tradable', CASE WHEN active_instruments >= 1 AND tradable_instruments = active_instruments THEN 'pass' ELSE 'fail' END, (tradable_instruments::text || '/' || active_instruments::text), 'all active instruments tradable', '{}'::jsonb FROM universe
UNION ALL
SELECT 'live_limits', CASE WHEN instrument_limits >= 1 AND portfolio_limits >= 8 THEN 'pass' ELSE 'fail' END, (instrument_limits::text || ' instrument / ' || portfolio_limits::text || ' portfolio'), 'instrument and portfolio live limits present', '{}'::jsonb FROM limits
UNION ALL
SELECT 'live_schedules', CASE WHEN enabled_live_schedules >= 6 THEN 'pass' ELSE 'fail' END, enabled_live_schedules::text, '>=6 enabled live autonomous schedules', '{}'::jsonb FROM schedules;

INSERT INTO audit.audit_record (module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload)
VALUES (
  'Risk Control Module',
  'info',
  'autonomous_live_turnover_mandate_seeded',
  'Active autonomous live trading mandate, live weights, live risk policy and live readiness view were seeded.',
  'trading_mandate',
  'trading_mandate:live:turnover_10m_14d:v1',
  ARRAY['autonomous_live_trading_target', 'turnover_10m_14d', 'positive_edge_required'],
  '{"target_gross_turnover_rub_14d":10000000,"initial_capital_rub":1000000,"target_turnover_ratio_14d":10.0}'::jsonb
);

COMMIT;
