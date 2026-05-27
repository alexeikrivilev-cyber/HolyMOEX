BEGIN;

UPDATE request_logs.provider_config
   SET auth_value_source = 'SANDBOX_API_KEY',
       config_payload = jsonb_set(
         COALESCE(config_payload, '{}'::jsonb),
         '{auth_fallback_value_sources}',
         jsonb_build_array('ARENA_GO_API_KEY', 'ARENA_GO_TOKEN'),
         true
       )
 WHERE provider = 'arena_go';

INSERT INTO audit.audit_record (
  module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
  'External Request Gateway Module',
  'info',
  'arena_go_auth_contract_refreshed',
  'ArenaGo auth uses SANDBOX_API_KEY first; ARENA_GO_API_KEY/ARENA_GO_TOKEN are local/dev fallback sources only.',
  'migration',
  '038_arena_go_sandbox_api_key_fallback_contract',
  ARRAY['sandbox_api_key_primary', 'local_token_fallback_only', 'secrets_not_logged'],
  jsonb_build_object(
    'primary_source', 'SANDBOX_API_KEY',
    'fallback_sources', jsonb_build_array('ARENA_GO_API_KEY', 'ARENA_GO_TOKEN')
  )
);

COMMIT;
