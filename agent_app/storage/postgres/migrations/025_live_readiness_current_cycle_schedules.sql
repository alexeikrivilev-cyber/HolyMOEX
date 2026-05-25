BEGIN;

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
),
latest_broker_portfolio AS (
  SELECT
    (
      SELECT portfolio_id
        FROM portfolio.portfolio_snapshot
       WHERE source_module = 'Portfolio State Module'
       ORDER BY created_at DESC
       LIMIT 1
    ) AS portfolio_id,
    (
      SELECT created_at
        FROM portfolio.portfolio_snapshot
       WHERE source_module = 'Portfolio State Module'
       ORDER BY created_at DESC
       LIMIT 1
    ) AS created_at
),
resolved_startup_identity AS (
  SELECT payload ->> 'bot_name' AS bot_name
    FROM audit.audit_record
   WHERE event_type = 'automatic_live_trading_startup_preflight'
     AND COALESCE(payload ->> 'bot_name', '') NOT IN ('', 'arena_go_default', 'startup_external_skip')
   ORDER BY created_at DESC
   LIMIT 1
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
SELECT 'live_schedules', CASE WHEN enabled_live_schedules >= 5 THEN 'pass' ELSE 'fail' END, enabled_live_schedules::text, '>=5 enabled live autonomous schedules; standalone decision timer disabled so execution uses current-cycle refs', '{}'::jsonb FROM schedules
UNION ALL
SELECT
  'arena_go_portfolio_identity',
  CASE
    WHEN latest_broker_portfolio.portfolio_id IS NULL AND resolved_startup_identity.bot_name IS NOT NULL THEN 'warn'
    WHEN latest_broker_portfolio.portfolio_id = 'arena_go_default' AND resolved_startup_identity.bot_name IS NOT NULL THEN 'warn'
    WHEN resolved_startup_identity.bot_name IS NOT NULL AND latest_broker_portfolio.portfolio_id <> resolved_startup_identity.bot_name THEN 'warn'
    ELSE 'pass'
  END,
  COALESCE(latest_broker_portfolio.portfolio_id, 'missing'),
  'latest broker-synced portfolio_snapshot.portfolio_id matches exact resolved ArenaGo bots[].name; controlled staging snapshots are ignored',
  jsonb_build_object('latest_snapshot_created_at', latest_broker_portfolio.created_at, 'resolved_bot_name', resolved_startup_identity.bot_name)
FROM latest_broker_portfolio
LEFT JOIN resolved_startup_identity ON true;

INSERT INTO audit.audit_record (module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload)
VALUES (
  'Orchestration Module',
  'info',
  'live_readiness_current_cycle_schedules',
  'Live readiness now expects the current-cycle live schedule set without the standalone decision timer.',
  'migration',
  '025_live_readiness_current_cycle_schedules',
  ARRAY['current_cycle_refs', 'scheduler', 'live_readiness'],
  '{"migration":"025_live_readiness_current_cycle_schedules.sql"}'::jsonb
);

COMMIT;
