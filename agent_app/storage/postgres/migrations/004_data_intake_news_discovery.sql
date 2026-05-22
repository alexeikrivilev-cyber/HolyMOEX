BEGIN;

CREATE TABLE IF NOT EXISTS raw_text.text_source_config (
  text_source_config_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  provider TEXT NOT NULL,
  request_type TEXT NOT NULL DEFAULT 'text_search',
  enabled BOOLEAN NOT NULL DEFAULT true,
  discovery_frequency TEXT,
  max_age_seconds INTEGER,
  query_template JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_type, provider, request_type)
);

CREATE TABLE IF NOT EXISTS raw_text.scheduled_external_news_discovery_run (
  discovery_run_id TEXT PRIMARY KEY,
  module_job_id TEXT,
  universe_id TEXT NOT NULL,
  discovery_mode TEXT NOT NULL
    CHECK (discovery_mode IN ('scheduled', 'event_driven', 'replay')),
  per_instrument_discovery BOOLEAN NOT NULL DEFAULT true,
  source_types TEXT[] NOT NULL DEFAULT '{}',
  time_range JSONB NOT NULL DEFAULT '{}'::jsonb,
  active_instruments_total INTEGER NOT NULL DEFAULT 0,
  active_instruments_with_discovery_request INTEGER NOT NULL DEFAULT 0,
  scheduled_discovery_coverage_ratio NUMERIC(8, 6),
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned', 'running', 'completed', 'partial_success', 'failed', 'cancelled')),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  source_module TEXT NOT NULL DEFAULT 'Data Intake & Routing Module',
  calculation_version TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_text.scheduled_external_news_discovery_item (
  discovery_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  discovery_run_id TEXT NOT NULL
    REFERENCES raw_text.scheduled_external_news_discovery_run(discovery_run_id),
  instrument_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  text_source_config_id TEXT REFERENCES raw_text.text_source_config(text_source_config_id),
  query_terms TEXT[] NOT NULL DEFAULT '{}',
  query_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned', 'request_created', 'skipped', 'fetched', 'failed')),
  skip_reason_code TEXT
    CHECK (skip_reason_code IS NULL OR skip_reason_code IN ('source_disabled', 'instrument_not_eligible')),
  external_request_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (discovery_run_id, instrument_id, source_type)
);

CREATE TABLE IF NOT EXISTS raw_text.external_text_search_request (
  external_text_search_request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  discovery_item_id UUID
    REFERENCES raw_text.scheduled_external_news_discovery_item(discovery_item_id),
  request_id TEXT NOT NULL UNIQUE,
  instrument_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  provider TEXT NOT NULL,
  request_type TEXT NOT NULL DEFAULT 'text_search',
  query_terms TEXT[] NOT NULL DEFAULT '{}',
  query_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE raw_text.raw_text_item
  ADD COLUMN IF NOT EXISTS discovery_run_id TEXT,
  ADD COLUMN IF NOT EXISTS discovery_item_id UUID,
  ADD COLUMN IF NOT EXISTS discovery_mode TEXT,
  ADD COLUMN IF NOT EXISTS source_type TEXT,
  ADD COLUMN IF NOT EXISTS external_request_id TEXT;

ALTER TABLE raw_text.event_routing_message
  ADD COLUMN IF NOT EXISTS discovery_mode TEXT,
  ADD COLUMN IF NOT EXISTS discovery_run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_text_source_config_enabled
  ON raw_text.text_source_config (source_type, provider, enabled);
CREATE INDEX IF NOT EXISTS idx_news_discovery_run_lookup
  ON raw_text.scheduled_external_news_discovery_run (universe_id, discovery_mode, created_at);
CREATE INDEX IF NOT EXISTS idx_news_discovery_item_lookup
  ON raw_text.scheduled_external_news_discovery_item (discovery_run_id, instrument_id, source_type, status);
CREATE INDEX IF NOT EXISTS idx_external_text_search_request_request_id
  ON raw_text.external_text_search_request (request_id);
CREATE INDEX IF NOT EXISTS idx_raw_text_item_discovery
  ON raw_text.raw_text_item (discovery_run_id, source_type);

INSERT INTO raw_text.text_source_config (
  text_source_config_id,
  source_type,
  provider,
  request_type,
  enabled,
  discovery_frequency,
  max_age_seconds,
  query_template,
  source_policy
) VALUES
  (
    'source:news_api:text_search',
    'news_api',
    'news_api',
    'text_search',
    true,
    '15m-60m',
    3600,
    '{"query_fields":["ticker","issuer_name","aliases","related_entities"],"response_target":"Raw Text Store"}'::jsonb,
    '{"requires_active_instrument":true,"gateway_only":true}'::jsonb
  ),
  (
    'source:issuer_disclosure:text_search',
    'issuer_disclosure',
    'issuer_disclosure',
    'text_search',
    true,
    '15m-60m',
    3600,
    '{"query_fields":["ticker","issuer_name","aliases"],"response_target":"Raw Text Store"}'::jsonb,
    '{"requires_active_instrument":true,"gateway_only":true}'::jsonb
  ),
  (
    'source:regulatory_text:text_search',
    'regulatory_text',
    'issuer_disclosure',
    'text_search',
    true,
    '15m-60m',
    3600,
    '{"query_fields":["ticker","issuer_name","related_entities"],"response_target":"Raw Text Store"}'::jsonb,
    '{"requires_active_instrument":true,"gateway_only":true}'::jsonb
  ),
  (
    'source:macro_text:text_search',
    'macro_text',
    'macro_api',
    'text_search',
    true,
    '4h-8h',
    14400,
    '{"query_fields":["sector","related_entities"],"response_target":"Raw Text Store"}'::jsonb,
    '{"requires_active_instrument":false,"gateway_only":true,"market_wide_allowed":true}'::jsonb
  ),
  (
    'source:corporate_site:text_search',
    'corporate_site',
    'issuer_disclosure',
    'text_search',
    true,
    '15m-60m',
    3600,
    '{"query_fields":["issuer_name","aliases","related_entities"],"response_target":"Raw Text Store"}'::jsonb,
    '{"requires_active_instrument":true,"gateway_only":true}'::jsonb
  )
ON CONFLICT (text_source_config_id) DO UPDATE SET
  source_type = EXCLUDED.source_type,
  provider = EXCLUDED.provider,
  request_type = EXCLUDED.request_type,
  enabled = EXCLUDED.enabled,
  discovery_frequency = EXCLUDED.discovery_frequency,
  max_age_seconds = EXCLUDED.max_age_seconds,
  query_template = EXCLUDED.query_template,
  source_policy = EXCLUDED.source_policy,
  updated_at = now();

INSERT INTO audit.schedule_config (
  schedule_config_id,
  module_name,
  contour,
  schedule_payload,
  enabled
) VALUES (
  'schedule:data_intake:scheduled_external_news_discovery',
  'Data Intake & Routing Module',
  'intraday_contour',
  '{
    "trigger": "scheduled_external_news_discovery",
    "frequency": "15m-60m",
    "requires_exchange_calendar": true,
    "per_instrument_discovery": true,
    "active_universe_ref": "moex_top20_manual",
    "source_types": ["news_api", "issuer_disclosure", "regulatory_text", "macro_text", "corporate_site"],
    "gateway_request_types": ["text_search", "text_fetch"]
  }'::jsonb,
  true
)
ON CONFLICT (schedule_config_id) DO UPDATE SET
  module_name = EXCLUDED.module_name,
  contour = EXCLUDED.contour,
  schedule_payload = EXCLUDED.schedule_payload,
  enabled = EXCLUDED.enabled;

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
  'Data Intake & Routing Module',
  'info',
  'scheduled_external_news_discovery_schema_seeded',
  'Scheduled external news discovery database support seeded',
  'text_source_config',
  'scheduled_external_news_discovery',
  ARRAY[
    'scheduled_discovery_runs_for_each_active_instrument',
    'all_external_text_requests_via_gateway'
  ],
  '{"seed_version":"2026-05-21","source":"modules/04_data_intake_routing_module.md"}'::jsonb
);

COMMIT;
