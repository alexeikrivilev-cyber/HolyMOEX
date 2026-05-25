UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'production_default', 'disabled_unless_ENABLE_LLM_TEXT_SCHEDULES_true',
          'enable_env', 'ENABLE_LLM_TEXT_SCHEDULES',
          'cost_control_reason', 'prevent_startup_llm_fanout'
        )
 WHERE schedule_config_id IN (
   'schedule:event_news:intake',
   'schedule:earnings_dividend:event',
   'schedule:fundamental_valuation:daily'
 );

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Orchestration Module',
    'info',
    'live_llm_text_schedules_opt_in',
    'Production LLM-heavy text schedules require ENABLE_LLM_TEXT_SCHEDULES=true; data intake/news discovery remains available without LLM fan-out.',
    'migration',
    '023_live_llm_text_schedules_opt_in',
    ARRAY['llm_text_schedules_opt_in', 'startup_llm_fanout_blocked', 'trading_loop_priority'],
    jsonb_build_object(
      'enable_env', 'ENABLE_LLM_TEXT_SCHEDULES',
      'default_enabled', false,
      'affected_schedules', jsonb_build_array(
        'schedule:event_news:intake',
        'schedule:earnings_dividend:event',
        'schedule:fundamental_valuation:daily'
      )
    )
);
