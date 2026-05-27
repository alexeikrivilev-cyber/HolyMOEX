UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'frequency', '2m',
          'interval_seconds', 120,
          'steady_state_min_interval_seconds', 120,
          'direct_module_only', true,
          'llm_usage', 'none',
          'reactivity_profile', 'fast_news_discovery_v2'
        )
 WHERE schedule_config_id = 'schedule:data_intake:scheduled_external_news_discovery';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'frequency', '2m',
          'interval_seconds', 120,
          'steady_state_min_interval_seconds', 120,
          'direct_module_only', true,
          'fast_model', 'deepseek/deepseek-v4-flash',
          'reasoning_model', 'qwen/qwen3.6-35b-a3b',
          'fast_pass_task_type', 'event_extraction',
          'reasoning_escalation_task_type', 'multi_source_event_synthesis',
          'reasoning_escalation_enabled', true,
          'only_unprocessed_raw_text', true,
          'max_items_per_run_env', 'EVENT_NEWS_MAX_ITEMS_PER_RUN',
          'reactivity_profile', 'fast_news_eventnews_v2',
          'llm_boundary', 'extracts events/features only; no trading decisions or orders'
        )
 WHERE schedule_config_id = 'schedule:event_news:intake';

UPDATE request_logs.provider_config
   SET config_payload = config_payload
     || jsonb_build_object(
          'fast_model_env', 'POLZA_FAST_MODEL',
          'reasoning_model_env', 'POLZA_REASONING_MODEL',
          'default_model_env', 'POLZA_DEFAULT_MODEL',
          'fast_model', 'deepseek/deepseek-v4-flash',
          'reasoning_model', 'qwen/qwen3.6-35b-a3b',
          'default_model', 'qwen/qwen3.6-35b-a3b',
          'legacy_pro_model_blocked', true,
          'fast_news_reactivity_profile', 'fast_news_eventnews_v2'
        )
 WHERE provider = 'polza_ai';

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Event & News Intelligence Module',
    'info',
    'fast_news_llm_reactivity_enabled',
    'News contour now runs fast cheap flash extraction every two minutes and escalates only material/important news to reasoning model.',
    'migration',
    '032_fast_news_llm_reactivity',
    ARRAY['fast_news_reactivity', 'deepseek_flash_fast_pass', 'qwen_material_news_escalation', 'llm_not_decision_maker'],
    jsonb_build_object(
      'data_intake_interval_seconds', 120,
      'event_news_interval_seconds', 120,
      'fast_model', 'deepseek/deepseek-v4-flash',
      'reasoning_model', 'qwen/qwen3.6-35b-a3b',
      'materiality_threshold_env', 'EVENT_NEWS_ESCALATE_MATERIALITY_THRESHOLD',
      'sentiment_threshold_env', 'EVENT_NEWS_ESCALATE_SENTIMENT_ABS_THRESHOLD'
    )
);
