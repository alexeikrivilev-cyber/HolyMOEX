UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'fast_first', true,
          'primary_pass_task_type', 'event_extraction',
          'primary_pass_model', 'deepseek/deepseek-v4-flash',
          'reasoning_escalation_model', 'qwen/qwen3.6-35b-a3b',
          'reasoning_escalation_only_after_fast_material_event', true
        )
 WHERE schedule_config_id = 'schedule:event_news:intake';

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Event & News Intelligence Module',
    'info',
    'event_news_fast_first_guard_enabled',
    'EventNews primary LLM pass is always fast event_extraction; reasoning model is used only as an escalation after a material fast result.',
    'migration',
    '033_event_news_fast_first_guard',
    ARRAY['event_news_fast_first', 'qwen_only_after_material_fast_event', 'llm_cost_control'],
    jsonb_build_object(
      'primary_pass_task_type', 'event_extraction',
      'primary_pass_model', 'deepseek/deepseek-v4-flash',
      'reasoning_escalation_model', 'qwen/qwen3.6-35b-a3b'
    )
);
