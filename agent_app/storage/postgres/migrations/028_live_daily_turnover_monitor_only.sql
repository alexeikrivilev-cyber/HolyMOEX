BEGIN;

-- The autonomous live sandbox mandate tracks daily turnover for observability,
-- but the per-day limit must not block otherwise valid positive-edge orders.
-- Hard gates remain in Risk Control for market state, stale data/portfolio,
-- post-cost edge, losses/drawdown, exposure, slippage, liquidity and broker
-- execution safety.
UPDATE risk.risk_policy
   SET rules = rules || jsonb_build_object(
       'daily_turnover_limit_mode', 'monitor_only',
       'max_daily_turnover_hard_block_enabled', false,
       'turnover_target_is_mandatory_constraint_not_daily_cap', true
     )
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1';

UPDATE risk.portfolio_limit
   SET payload = COALESCE(payload, '{}'::jsonb) || jsonb_build_object(
       'limit_mode', 'monitor_only',
       'hard_block_enabled', false,
       'monitoring_signal', 'daily_turnover_soft_limit'
     )
 WHERE risk_policy_id = 'risk_policy:live_autonomous_turnover:v1'
   AND limit_name IN ('max_daily_turnover_rub', 'daily_turnover_limit_rub');

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Risk Control Module',
    'info',
    'daily_turnover_limit_monitor_only',
    'Daily turnover limit is now monitored as a soft operating signal in live autonomous sandbox mode and no longer blocks positive-edge orders by itself.',
    'migration',
    '028_live_daily_turnover_monitor_only',
    ARRAY['daily_turnover_soft_limit', 'turnover_not_daily_kill_switch', 'risk_gates_preserved'],
    jsonb_build_object(
      'risk_policy_id', 'risk_policy:live_autonomous_turnover:v1',
      'daily_turnover_limit_mode', 'monitor_only',
      'hard_gates_preserved', jsonb_build_array(
        'market_session',
        'readiness',
        'current_cycle_order_intents',
        'post_cost_edge',
        'stale_data',
        'stale_portfolio',
        'daily_loss',
        'drawdown',
        'exposure',
        'slippage',
        'liquidity',
        'kill_switch'
      )
    )
);

COMMIT;
