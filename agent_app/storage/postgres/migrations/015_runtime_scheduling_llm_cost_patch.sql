BEGIN;

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || '{
          "frequency": "30m",
          "interval_seconds": 1800,
          "steady_state_min_interval_seconds": 900,
          "requires_exchange_calendar": true,
          "agent_runtime_phase_allowed": ["trading_session", "off_market", "maintenance"],
          "llm_usage": "none",
          "notes": "Raw Text/Data Intake performs economical discovery; EventNews processes new raw_text refs."
        }'::jsonb
 WHERE schedule_config_id = 'schedule:data_intake:scheduled_external_news_discovery';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || '{
          "trigger": "on_new_raw_text_or_batch",
          "frequency": "30m",
          "interval_seconds": 1800,
          "steady_state_min_interval_seconds": 900,
          "source": "Raw Text Store",
          "llm_provider": "polza_ai",
          "llm_task_routing": true,
          "fast_model": "deepseek/deepseek-v4-flash",
          "reasoning_model": "qwen/qwen3.6-35b-a3b",
          "max_items_per_run_env": "LLM_MAX_ITEMS_PER_RUN",
          "no_full_store_scan_each_minute": true
        }'::jsonb
 WHERE schedule_config_id = 'schedule:event_news:intake';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || '{
          "frequency": "60m",
          "interval_seconds": 3600,
          "llm_task_routing": true,
          "reasoning_model": "qwen/qwen3.6-35b-a3b",
          "run_preference": "after_market_close_or_event_driven"
        }'::jsonb
 WHERE schedule_config_id IN ('schedule:fundamental_valuation:daily', 'schedule:earnings_dividend:event');

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'market_session_required', 'open',
          'skip_when_market_session_status_in', jsonb_build_array('closed', 'premarket', 'postmarket', 'unknown'),
          'agent_runtime_phase_required', 'trading_session'
        )
 WHERE schedule_config_id IN (
   'schedule:live_autonomous:decision:1m',
   'schedule:live_autonomous:risk:on_decision',
   'schedule:live_autonomous:execution:on_approved',
   'schedule:decision_engine:on_feature_update',
   'schedule:risk_control:on_decision_set',
   'schedule:execution:on_approved_order'
 );

UPDATE request_logs.provider_config
   SET config_payload = config_payload
     || '{
          "fast_model_env": "POLZA_FAST_MODEL",
          "reasoning_model_env": "POLZA_REASONING_MODEL",
          "default_model_env": "POLZA_DEFAULT_MODEL",
          "fast_model": "deepseek/deepseek-v4-flash",
          "reasoning_model": "qwen/qwen3.6-35b-a3b",
          "default_model": "qwen/qwen3.6-35b-a3b",
          "task_specific_model_routing": true,
          "llm_cache_key_fields": ["content_hash", "task_type", "prompt_version", "model_id", "event_ontology_version"],
          "llm_cache_key_excludes": ["job_id", "fetched_at", "current_timestamp", "scheduler_tick_id"]
        }'::jsonb
 WHERE provider = 'polza_ai';

INSERT INTO audit.audit_record (
  module_name,
  severity,
  event_type,
  message,
  object_type,
  object_ref,
  reason_codes,
  payload
) VALUES (
  'Orchestration Module',
  'info',
  'runtime_scheduling_llm_cost_patch_applied',
  'Off-market trading loops are gated; Raw Text/EventNews steady-state intervals are throttled; PolzaAI task-specific model routing is enabled.',
  'migration',
  '015_runtime_scheduling_llm_cost_patch',
  ARRAY['off_market_gating', 'llm_cost_control', 'task_specific_model_routing'],
  '{
    "raw_text_interval_seconds": 1800,
    "event_news_interval_seconds": 1800,
    "earnings_fundamental_interval_seconds": 3600,
    "fast_model": "deepseek/deepseek-v4-flash",
    "reasoning_model": "qwen/qwen3.6-35b-a3b"
  }'::jsonb
);

COMMIT;
