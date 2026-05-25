UPDATE request_logs.provider_config
   SET config_payload = config_payload
     || jsonb_build_object(
          'fast_model_env', 'POLZA_FAST_MODEL',
          'reasoning_model_env', 'POLZA_REASONING_MODEL',
          'default_model_env', 'POLZA_DEFAULT_MODEL',
          'fast_model', 'deepseek/deepseek-v4-flash',
          'reasoning_model', 'qwen/qwen3.6-35b-a3b',
          'default_model', 'qwen/qwen3.6-35b-a3b',
          'legacy_pro_model_blocked', true
        )
 WHERE provider = 'polza_ai';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'direct_module_only', true,
          'interval_seconds', 1800,
          'steady_state_min_interval_seconds', 1800,
          'llm_usage', 'none'
        )
 WHERE schedule_config_id = 'schedule:data_intake:scheduled_external_news_discovery';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'direct_module_only', true,
          'interval_seconds', 1800,
          'steady_state_min_interval_seconds', 1800,
          'fast_model', 'deepseek/deepseek-v4-flash',
          'reasoning_model', 'qwen/qwen3.6-35b-a3b',
          'no_full_store_scan_each_minute', true
        )
 WHERE schedule_config_id = 'schedule:event_news:intake';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'direct_module_only', true,
          'interval_seconds', 3600,
          'steady_state_min_interval_seconds', 3600,
          'reasoning_model', 'qwen/qwen3.6-35b-a3b'
        )
 WHERE schedule_config_id IN ('schedule:earnings_dividend:event', 'schedule:fundamental_valuation:daily');

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload || jsonb_build_object('direct_module_only', true)
 WHERE schedule_config_id IN (
   'schedule:live_autonomous:portfolio_sync:1m',
   'schedule:live_autonomous:monitoring:1m',
   'schedule:monitoring:continuous',
   'schedule:portfolio_state:after_execution'
 );

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Orchestration Module',
    'info',
    'runtime_llm_live_cycle_hardened',
    'Runtime schedules route trading before text/LLM jobs and block legacy expensive Polza defaults.',
    'migration',
    '019_runtime_llm_live_cycle_hardening',
    ARRAY['legacy_polza_pro_model_blocked', 'text_llm_schedules_direct_and_throttled', 'live_cycle_priority_enabled'],
    jsonb_build_object(
      'fast_model', 'deepseek/deepseek-v4-flash',
      'reasoning_model', 'qwen/qwen3.6-35b-a3b',
      'default_model', 'qwen/qwen3.6-35b-a3b',
      'raw_text_interval_seconds', 1800,
      'event_news_interval_seconds', 1800
    )
);
