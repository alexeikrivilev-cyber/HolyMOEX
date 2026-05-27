BEGIN;

-- Recovery timer for the live feature-to-execution path.
--
-- Migration 024 disabled the standalone decision timer after current-cycle
-- refs were introduced. In the current scheduler runtime, pure event-driven
-- schedules are intentionally not launched by timer, so a broken current-cycle
-- fan-out can leave fresh market/features data without any Decision/Risk/
-- Execution pass. Re-enable the 1m Feature Store schedule as a conservative
-- recovery bridge; downstream modules still enforce freshness, risk policy,
-- live-readiness gates, anti-churn, and execution safeguards.
UPDATE audit.schedule_config
   SET enabled = true,
       schedule_payload = jsonb_build_object(
         'trigger', 'on_feature_update_or_timer',
         'source', 'Feature Store',
         'run_mode', 'live_trading',
         'interval_seconds', 60,
         'weights_profiles', jsonb_build_array(
           'weights:live_autonomous:intraday:v1',
           'weights:live_autonomous:swing:v1',
           'weights:live_autonomous:position:v1'
         ),
         'recovery_reason', 'restore_periodic_feature_to_decision_path'
       )
 WHERE schedule_config_id = 'schedule:live_autonomous:decision:1m';

INSERT INTO audit.audit_record (
  module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
  'Orchestration Module',
  'info',
  'live_decision_timer_recovery_enabled',
  'Live Feature Store decision timer restored so fresh features can reach Decision, Risk, and Execution when event fan-out is not available.',
  'migration',
  '037_restore_live_decision_timer_recovery',
  ARRAY['scheduler_recovery', 'feature_to_decision_path', 'live_autonomous_trading_loop'],
  jsonb_build_object(
    'schedule_config_id', 'schedule:live_autonomous:decision:1m',
    'interval_seconds', 60,
    'source', 'Feature Store',
    'risk_execution_gates_unchanged', true
  )
);

COMMIT;
