-- Guarded ArenaGo live smoke preflight report.
-- SELECT-only report: inspect active risk limits, portfolio state, universe scope, and smoke blockers.

-- A) Active risk policies and effective limit surface
WITH active_policy AS (
  SELECT *
    FROM risk.risk_policy
   WHERE status = 'active'
     AND (
       risk_policy_id = 'risk_policy:live_autonomous_turnover:v1'
       OR 'live_trading' = ANY(run_mode_allowed)
     )
),
portfolio_limits AS (
  SELECT
    risk_policy_id,
    max(limit_value) FILTER (WHERE limit_name IN ('daily_soft_loss_pct')) AS daily_soft_loss_pct,
    max(limit_value) FILTER (WHERE limit_name IN ('daily_hard_loss_pct')) AS daily_hard_loss_pct,
    max(limit_value) FILTER (WHERE limit_name IN ('max_daily_loss_pct', 'daily_loss_limit_pct')) AS max_daily_loss_pct,
    max(limit_value) FILTER (WHERE limit_name IN ('max_daily_loss_limit', 'max_daily_loss_rub', 'daily_loss_limit_rub')) AS max_daily_loss_limit_rub,
    max(limit_value) FILTER (WHERE limit_name IN ('max_drawdown_limit')) AS max_drawdown_limit,
    max(limit_value) FILTER (WHERE limit_name IN ('max_portfolio_exposure_pct', 'max_gross_exposure_pct')) AS max_portfolio_exposure_pct,
    max(limit_value) FILTER (WHERE limit_name IN ('max_allowed_slippage_bps', 'max_slippage_bps')) AS max_allowed_slippage_bps,
    max(limit_value) FILTER (WHERE limit_name IN ('max_order_value_rub')) AS portfolio_max_order_value_rub,
    max(limit_value) FILTER (WHERE limit_name IN ('portfolio_snapshot_ttl_seconds', 'portfolio_state_ttl_seconds')) AS portfolio_snapshot_ttl_seconds,
    max(limit_value) FILTER (WHERE limit_name IN ('min_expected_edge_after_cost_score')) AS min_expected_edge_after_cost_score
    FROM risk.portfolio_limit
   GROUP BY risk_policy_id
),
instrument_limits AS (
  SELECT
    risk_policy_id,
    count(*) AS instrument_limit_count,
    min(max_position_pct) AS min_position_pct,
    max(max_position_pct) AS max_position_pct,
    count(*) FILTER (WHERE max_position_pct IS NULL) AS missing_position_limit_count,
    min(max_order_value_rub) AS min_order_value_rub,
    max(max_order_value_rub) AS max_order_value_rub,
    count(*) FILTER (WHERE max_order_value_rub IS NULL) AS missing_order_value_count,
    min(max_slippage_bps) AS min_slippage_bps,
    max(max_slippage_bps) AS max_slippage_bps,
    count(*) FILTER (WHERE max_slippage_bps IS NULL) AS missing_slippage_limit_count
    FROM risk.instrument_limit
   GROUP BY risk_policy_id
)
SELECT
  policy.risk_policy_id,
  policy.policy_name,
  policy.version,
  policy.status,
  policy.run_mode_allowed,
  policy.created_at,
  policy.approved_by,
  policy.rules ->> 'global_kill_switch' AS global_kill_switch,
  policy.rules ->> 'kill_switch' AS kill_switch,
  policy.rules ->> 'execution_kill_switch' AS execution_kill_switch,
  policy.rules ->> 'daily_soft_loss_pct' AS rules_daily_soft_loss_pct,
  policy.rules ->> 'daily_hard_loss_pct' AS rules_daily_hard_loss_pct,
  limits.daily_soft_loss_pct,
  limits.daily_hard_loss_pct,
  limits.max_daily_loss_pct,
  limits.max_daily_loss_limit_rub,
  limits.max_drawdown_limit,
  limits.max_portfolio_exposure_pct,
  limits.max_allowed_slippage_bps,
  limits.portfolio_max_order_value_rub,
  limits.portfolio_snapshot_ttl_seconds,
  limits.min_expected_edge_after_cost_score,
  instruments.instrument_limit_count,
  instruments.min_position_pct,
  instruments.max_position_pct,
  instruments.missing_position_limit_count,
  instruments.min_order_value_rub,
  instruments.max_order_value_rub,
  instruments.missing_order_value_count,
  instruments.min_slippage_bps,
  instruments.max_slippage_bps,
  instruments.missing_slippage_limit_count
  FROM active_policy policy
  LEFT JOIN portfolio_limits limits
    ON limits.risk_policy_id = policy.risk_policy_id
  LEFT JOIN instrument_limits instruments
    ON instruments.risk_policy_id = policy.risk_policy_id
 ORDER BY policy.risk_policy_id;

-- B) Portfolio state and staleness
WITH latest_snapshot AS (
  SELECT snapshot.*,
         row_number() OVER (PARTITION BY snapshot.portfolio_id ORDER BY snapshot.as_of_ts DESC, snapshot.created_at DESC) AS row_number
    FROM portfolio.portfolio_snapshot snapshot
),
latest_positions AS (
  SELECT position.*,
         row_number() OVER (PARTITION BY position.portfolio_id, position.instrument_id ORDER BY position.as_of_ts DESC) AS row_number
    FROM portfolio.position_state position
),
open_position_counts AS (
  SELECT portfolio_id,
         count(*) FILTER (WHERE quantity <> 0) AS open_positions_count
    FROM latest_positions
   WHERE row_number = 1
   GROUP BY portfolio_id
)
SELECT
  snapshot.portfolio_id,
  snapshot.payload ->> 'account_id' AS account_id,
  snapshot.payload ->> 'run_mode' AS run_mode,
  snapshot.universe_id,
  snapshot.initial_capital_rub,
  snapshot.cash,
  snapshot.equity,
  snapshot.gross_exposure,
  snapshot.net_exposure,
  CASE
    WHEN snapshot.payload ->> 'daily_pnl' ~ '^-?[0-9]+(\.[0-9]+)?$'
      THEN (snapshot.payload ->> 'daily_pnl')::numeric
    ELSE COALESCE(snapshot.realized_pnl, 0) + COALESCE(snapshot.unrealized_pnl, 0)
  END AS daily_pnl_rub,
  CASE
    WHEN NULLIF(snapshot.equity, 0) IS NULL THEN NULL
    ELSE (
      CASE
        WHEN snapshot.payload ->> 'daily_pnl' ~ '^-?[0-9]+(\.[0-9]+)?$'
          THEN (snapshot.payload ->> 'daily_pnl')::numeric
        ELSE COALESCE(snapshot.realized_pnl, 0) + COALESCE(snapshot.unrealized_pnl, 0)
      END
    ) / NULLIF(snapshot.equity, 0)
  END AS daily_pnl_pct,
  COALESCE(open_positions.open_positions_count, 0) AS open_positions_count,
  snapshot.as_of_ts AS latest_snapshot_ts,
  now() - snapshot.as_of_ts AS snapshot_age,
  (now() - snapshot.as_of_ts) > interval '5 minutes' AS portfolio_snapshot_stale
  FROM latest_snapshot snapshot
  LEFT JOIN open_position_counts open_positions
    ON open_positions.portfolio_id = snapshot.portfolio_id
 WHERE snapshot.row_number = 1
 ORDER BY snapshot.as_of_ts DESC;

-- C) Open positions
WITH latest_snapshot AS (
  SELECT snapshot.*,
         row_number() OVER (PARTITION BY snapshot.portfolio_id ORDER BY snapshot.as_of_ts DESC, snapshot.created_at DESC) AS row_number
    FROM portfolio.portfolio_snapshot snapshot
),
latest_positions AS (
  SELECT position.*,
         row_number() OVER (PARTITION BY position.portfolio_id, position.instrument_id ORDER BY position.as_of_ts DESC) AS row_number
    FROM portfolio.position_state position
)
SELECT
  position.portfolio_id,
  snapshot.payload ->> 'account_id' AS account_id,
  snapshot.payload ->> 'run_mode' AS run_mode,
  position.instrument_id,
  position.quantity,
  position.average_price,
  position.market_price,
  position.market_value,
  position.unrealized_pnl,
  CASE
    WHEN NULLIF(snapshot.equity, 0) IS NULL THEN NULL
    ELSE abs(position.market_value) / NULLIF(snapshot.equity, 0)
  END AS exposure_pct,
  position.as_of_ts AS updated_at,
  position.payload
  FROM latest_positions position
  LEFT JOIN latest_snapshot snapshot
    ON snapshot.portfolio_id = position.portfolio_id
   AND snapshot.row_number = 1
 WHERE position.row_number = 1
   AND position.quantity <> 0
 ORDER BY abs(position.market_value) DESC NULLS LAST, position.instrument_id;

-- D) Universe check
WITH active_universe AS (
  SELECT
    instrument.*,
    count(*) OVER (PARTITION BY instrument.universe_id) AS active_instruments_count
    FROM registry.instrument_profile instrument
   WHERE instrument.is_active
),
live_stats AS (
  SELECT DISTINCT ON (stats.instrument_id)
         stats.instrument_id,
         stats.status_hint,
         stats.avg_action_aligned_return_30m,
         stats.churn_flip_count,
         stats.executed_count
    FROM analytics.instrument_live_stats stats
   ORDER BY stats.instrument_id, stats.executed_count DESC NULLS LAST
)
SELECT
  instrument.universe_id,
  instrument.instrument_id,
  instrument.ticker,
  instrument.tradable,
  instrument.execution_enabled,
  instrument.arena_go_secid,
  instrument.max_trade_quantity,
  instrument.active_instruments_count,
  instrument.ticker IN ('VTBR', 'AFLT', 'GMKN', 'ROSN') AS toxic_baseline_instrument,
  live_stats.status_hint AS analytics_status_hint,
  live_stats.avg_action_aligned_return_30m,
  live_stats.churn_flip_count,
  (
    instrument.tradable
    AND instrument.execution_enabled
    AND instrument.active_instruments_count <= 4
    AND instrument.ticker NOT IN ('VTBR', 'AFLT', 'GMKN')
    AND COALESCE(live_stats.status_hint, 'normal') NOT IN ('quarantine_candidate')
  ) AS smoke_recommended
  FROM active_universe instrument
  LEFT JOIN live_stats
    ON live_stats.instrument_id = instrument.instrument_id
 ORDER BY instrument.universe_id, toxic_baseline_instrument DESC, instrument.ticker;

-- E) Limit risk warnings for guarded live smoke
WITH active_policy AS (
  SELECT *
    FROM risk.risk_policy
   WHERE status = 'active'
     AND (
       risk_policy_id = 'risk_policy:live_autonomous_turnover:v1'
       OR 'live_trading' = ANY(run_mode_allowed)
     )
   ORDER BY CASE WHEN risk_policy_id = 'risk_policy:live_autonomous_turnover:v1' THEN 0 ELSE 1 END, created_at DESC
   LIMIT 1
),
portfolio_limits AS (
  SELECT
    active_policy.risk_policy_id,
    max(limit_value) FILTER (WHERE limit_name IN ('daily_hard_loss_pct')) AS daily_hard_loss_pct,
    max(limit_value) FILTER (WHERE limit_name IN ('max_daily_loss_pct', 'daily_loss_limit_pct')) AS max_daily_loss_pct,
    max(limit_value) FILTER (WHERE limit_name IN ('max_daily_loss_limit', 'max_daily_loss_rub', 'daily_loss_limit_rub')) AS max_daily_loss_limit_rub,
    max(limit_value) FILTER (WHERE limit_name IN ('max_allowed_slippage_bps', 'max_slippage_bps')) AS max_allowed_slippage_bps,
    max(limit_value) FILTER (WHERE limit_name IN ('max_order_value_rub')) AS portfolio_max_order_value_rub,
    max(limit_value) FILTER (WHERE limit_name IN ('portfolio_snapshot_ttl_seconds', 'portfolio_state_ttl_seconds')) AS portfolio_snapshot_ttl_seconds
    FROM active_policy
    LEFT JOIN risk.portfolio_limit limits
      ON limits.risk_policy_id = active_policy.risk_policy_id
   GROUP BY active_policy.risk_policy_id
),
instrument_limits AS (
  SELECT
    active_policy.risk_policy_id,
    count(*) AS instrument_limit_count,
    max(max_position_pct) AS max_position_pct,
    count(*) FILTER (WHERE max_position_pct IS NULL) AS missing_position_limit_count,
    count(*) FILTER (WHERE max_slippage_bps IS NULL) AS missing_slippage_limit_count,
    count(*) FILTER (WHERE max_order_value_rub IS NULL) AS missing_order_value_count
    FROM active_policy
    LEFT JOIN risk.instrument_limit limits
      ON limits.risk_policy_id = active_policy.risk_policy_id
   GROUP BY active_policy.risk_policy_id
),
active_universe AS (
  SELECT
    count(*) AS active_instruments_count,
    count(*) FILTER (WHERE ticker IN ('VTBR', 'AFLT', 'GMKN', 'ROSN')) AS toxic_instruments_count,
    string_agg(ticker, ', ' ORDER BY ticker) FILTER (WHERE ticker IN ('VTBR', 'AFLT', 'GMKN', 'ROSN')) AS toxic_instruments
    FROM registry.instrument_profile
   WHERE is_active
),
latest_snapshot AS (
  SELECT snapshot.*,
         row_number() OVER (PARTITION BY snapshot.portfolio_id ORDER BY snapshot.as_of_ts DESC, snapshot.created_at DESC) AS row_number
    FROM portfolio.portfolio_snapshot snapshot
),
latest_positions AS (
  SELECT position.*,
         row_number() OVER (PARTITION BY position.portfolio_id, position.instrument_id ORDER BY position.as_of_ts DESC) AS row_number
    FROM portfolio.position_state position
),
portfolio_state AS (
  SELECT
    snapshot.portfolio_id,
    snapshot.as_of_ts,
    now() - snapshot.as_of_ts AS snapshot_age,
    COALESCE(limits.portfolio_snapshot_ttl_seconds, 300) AS ttl_seconds,
    count(position.instrument_id) FILTER (WHERE position.quantity <> 0) AS open_positions_count
    FROM latest_snapshot snapshot
    CROSS JOIN portfolio_limits limits
    LEFT JOIN latest_positions position
      ON position.portfolio_id = snapshot.portfolio_id
     AND position.row_number = 1
   WHERE snapshot.row_number = 1
   GROUP BY snapshot.portfolio_id, snapshot.as_of_ts, limits.portfolio_snapshot_ttl_seconds
),
summary AS (
  SELECT
    active_policy.risk_policy_id,
    active_policy.rules,
    portfolio_limits.daily_hard_loss_pct,
    portfolio_limits.max_daily_loss_pct,
    portfolio_limits.max_daily_loss_limit_rub,
    portfolio_limits.max_allowed_slippage_bps,
    portfolio_limits.portfolio_max_order_value_rub,
    instrument_limits.max_position_pct,
    instrument_limits.missing_position_limit_count,
    instrument_limits.missing_slippage_limit_count,
    instrument_limits.missing_order_value_count,
    active_universe.active_instruments_count,
    active_universe.toxic_instruments_count,
    active_universe.toxic_instruments,
    COALESCE((SELECT sum(open_positions_count) FROM portfolio_state), 0) AS open_positions_count,
    COALESCE((SELECT bool_or(snapshot_age > make_interval(secs => ttl_seconds::integer)) FROM portfolio_state), true) AS portfolio_snapshot_stale
    FROM active_policy
    LEFT JOIN portfolio_limits
      ON portfolio_limits.risk_policy_id = active_policy.risk_policy_id
    LEFT JOIN instrument_limits
      ON instrument_limits.risk_policy_id = active_policy.risk_policy_id
    CROSS JOIN active_universe
)
SELECT warning_code, severity, observed_value, expected_value, recommendation
  FROM (
    SELECT
      'daily_hard_loss_missing' AS warning_code,
      'HIGH' AS severity,
      'max_daily_loss_pct/max_daily_loss_limit not found' AS observed_value,
      'daily hard loss set and smoke hard stop <= 0.10%' AS expected_value,
      'Do not start live smoke until a governed hard daily loss limit is confirmed.' AS recommendation
      FROM summary
     WHERE daily_hard_loss_pct IS NULL
       AND max_daily_loss_pct IS NULL
       AND max_daily_loss_limit_rub IS NULL
    UNION ALL
    SELECT
      'daily_hard_loss_too_wide',
      'HIGH',
      'max_daily_loss_pct=' || max_daily_loss_pct::text,
      '<= 0.001 ratio for -0.10% smoke hard stop',
      'Use paper/shadow or a governed smoke risk policy before live submit.'
      FROM summary
     WHERE max_daily_loss_pct IS NOT NULL
       AND abs(max_daily_loss_pct) > 0.001
    UNION ALL
    SELECT
      'max_position_pct_missing',
      'HIGH',
      missing_position_limit_count::text || ' instrument limits missing max_position_pct',
      'all smoke instruments have max_position_pct <= 0.02',
      'Do not rely on default position sizing for guarded live smoke.'
      FROM summary
     WHERE missing_position_limit_count > 0
    UNION ALL
    SELECT
      'max_position_pct_too_wide',
      'HIGH',
      'max_position_pct=' || max_position_pct::text,
      '<= 0.02 for guarded smoke',
      'Use a smaller governed smoke universe/policy or stay in paper mode.'
      FROM summary
     WHERE max_position_pct IS NOT NULL
       AND max_position_pct > 0.02
    UNION ALL
    SELECT
      'universe_too_large',
      'WARNING',
      active_instruments_count::text || ' active instruments',
      '2-4 manually confirmed smoke instruments',
      'Confirm each instrument manually or prepare a governed smoke universe before live.'
      FROM summary
     WHERE active_instruments_count > 4
    UNION ALL
    SELECT
      'toxic_instrument_in_universe',
      'WARNING',
      COALESCE(toxic_instruments, 'none'),
      'VTBR/AFLT/GMKN/ROSN excluded or explicitly watchlisted/quarantined',
      'Avoid known toxic baseline names during the first guarded smoke unless intentionally testing quarantine.'
      FROM summary
     WHERE toxic_instruments_count > 0
    UNION ALL
    SELECT
      'open_positions_exist',
      'WARNING',
      open_positions_count::text || ' open positions',
      'open positions are zero or manually understood',
      'Confirm ArenaGo positions before starting the scheduler.'
      FROM summary
     WHERE open_positions_count > 0
    UNION ALL
    SELECT
      'portfolio_snapshot_stale',
      'HIGH',
      'latest portfolio snapshot is missing or older than configured TTL',
      'fresh portfolio snapshot before live submit',
      'Run portfolio sync and confirm Portfolio State Module success.'
      FROM summary
     WHERE portfolio_snapshot_stale
    UNION ALL
    SELECT
      'kill_switch_status_unknown',
      'HIGH',
      'no explicit kill switch keys in active risk_policy.rules',
      'global_kill_switch/execution_kill_switch explicitly present and monitored',
      'Do not start live smoke until the stop path and policy kill-switch state are confirmed.'
      FROM summary
     WHERE NOT (
       rules ? 'global_kill_switch'
       OR rules ? 'kill_switch'
       OR rules ? 'execution_kill_switch'
     )
    UNION ALL
    SELECT
      'slippage_limit_missing',
      'WARNING',
      'portfolio or instrument slippage limit missing',
      'max_allowed_slippage_bps or per-instrument max_slippage_bps present',
      'Confirm slippage caps before live submit.'
      FROM summary
     WHERE max_allowed_slippage_bps IS NULL
       AND missing_slippage_limit_count > 0
    UNION ALL
    SELECT
      'liquidity_limit_missing',
      'WARNING',
      'max_order_value_rub missing in portfolio or instrument limits',
      'small max_order_value_rub for smoke',
      'Confirm order notional caps before live submit.'
      FROM summary
     WHERE portfolio_max_order_value_rub IS NULL
       AND missing_order_value_count > 0
  ) warnings
 ORDER BY CASE severity WHEN 'HIGH' THEN 0 ELSE 1 END, warning_code;

-- F) Guardrails effective defaults currently used by Risk Control when not overridden by governed config
SELECT *
  FROM (
    VALUES
      ('opposite_action_cooldown_seconds', '900', 'Risk Control Profitability Guardrails default'),
      ('reentry_after_close_cooldown_seconds', '1200', 'Risk Control Profitability Guardrails default'),
      ('base_min_edge_after_cost_bps', '30', 'Risk Control Profitability Guardrails default'),
      ('reversal_min_edge_after_cost_bps', '50', 'Risk Control Profitability Guardrails default'),
      ('quarantine_min_edge_after_cost_bps', '70', 'Risk Control Profitability Guardrails default'),
      ('max_trades_per_instrument_per_hour', '2', 'Risk Control Profitability Guardrails default'),
      ('max_trades_per_instrument_per_day', '6', 'Risk Control Profitability Guardrails default')
  ) AS defaults(parameter_name, effective_value, source);
