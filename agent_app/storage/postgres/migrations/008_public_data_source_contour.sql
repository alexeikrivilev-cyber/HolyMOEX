BEGIN;

UPDATE request_logs.provider_config
   SET auth_header = NULL,
       auth_value_source = NULL,
       enabled = true,
       config_payload = config_payload
         || jsonb_build_object(
              'default_base_url', 'https://www.rbc.ru',
              'open_public_contour', true,
              'source_catalog', jsonb_build_array(
                'rbc_news',
                'tass_news',
                'interfax_news',
                'prime_news',
                'finam_news',
                'smartlab_news'
              )
            )
 WHERE provider = 'news_api';

UPDATE request_logs.provider_config
   SET enabled = true,
       config_payload = config_payload
         || jsonb_build_object(
              'default_base_url', 'https://www.e-disclosure.ru',
              'open_public_contour', true,
              'source_catalog', jsonb_build_array(
                'issuer_disclosure',
                'prime_disclosure',
                'akm_disclosure',
                'corporate_site'
              )
            )
 WHERE provider = 'issuer_disclosure';

UPDATE request_logs.provider_config
   SET auth_header = NULL,
       auth_value_source = NULL,
       enabled = true,
       config_payload = config_payload
         || jsonb_build_object(
              'default_base_url', 'https://www.cbr.ru',
              'open_public_contour', true,
              'source_catalog', jsonb_build_array(
                'cbr_macro',
                'moex_macro',
                'fred_eia_macro',
                'rosstat_macro'
              )
            )
 WHERE provider = 'macro_api';

WITH source_configs (
  source_type,
  provider,
  discovery_frequency,
  max_age_seconds,
  query_fields,
  endpoint,
  policy
) AS (
  VALUES
    (
      'news_api',
      'news_api',
      '15m-60m',
      3600,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      NULL,
      jsonb_build_object('source_layer','fast_news','trust_level','normal')
    ),
    (
      'rbc_news',
      'news_api',
      '15m',
      900,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      'https://rssexport.rbc.ru/rbcnews/news/30/full.rss',
      jsonb_build_object('source_name','rbc','source_layer','fast_news','trust_level','normal_high','fetch_scope','market_wide_once')
    ),
    (
      'tass_news',
      'news_api',
      '15m',
      900,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      'https://tass.ru/rss/v2.xml',
      jsonb_build_object('source_name','tass','source_layer','fast_news','trust_level','normal_high','fetch_scope','market_wide_once')
    ),
    (
      'interfax_news',
      'news_api',
      '15m',
      900,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      'https://www.interfax.ru/rss.asp',
      jsonb_build_object('source_name','interfax','source_layer','fast_news','trust_level','normal_high','fetch_scope','market_wide_once')
    ),
    (
      'prime_news',
      'news_api',
      '15m',
      900,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      'https://1prime.ru/export/rss2/index.xml',
      jsonb_build_object('source_name','prime_news','source_layer','fast_news','trust_level','normal_high','fetch_scope','market_wide_once')
    ),
    (
      'finam_news',
      'news_api',
      '15m',
      900,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      'https://www.finam.ru/analysis/conews/rsspoint/',
      jsonb_build_object('source_name','finam','source_layer','fast_news','trust_level','normal','fetch_scope','market_wide_once')
    ),
    (
      'smartlab_news',
      'news_api',
      '15m',
      900,
      ARRAY['ticker','issuer_name','aliases','related_entities']::text[],
      'https://smart-lab.ru/news/rss/',
      jsonb_build_object('source_name','smartlab','source_layer','fast_news','trust_level','medium_weak','fetch_scope','market_wide_once')
    ),
    (
      'issuer_disclosure',
      'issuer_disclosure',
      '15m-60m',
      3600,
      ARRAY['ticker','issuer_name','aliases']::text[],
      'https://www.e-disclosure.ru/portal/company.aspx',
      jsonb_build_object('source_name','e_disclosure','source_layer','official_disclosure','trust_level','high')
    ),
    (
      'prime_disclosure',
      'issuer_disclosure',
      '15m-60m',
      3600,
      ARRAY['ticker','issuer_name','aliases']::text[],
      'https://disclosure.1prime.ru/',
      jsonb_build_object('source_name','prime_disclosure','source_layer','official_disclosure','trust_level','high')
    ),
    (
      'akm_disclosure',
      'issuer_disclosure',
      '15m-60m',
      3600,
      ARRAY['ticker','issuer_name','aliases']::text[],
      'https://www.disclosure.ru/',
      jsonb_build_object('source_name','akm_disclosure','source_layer','official_disclosure','trust_level','high')
    ),
    (
      'corporate_site',
      'issuer_disclosure',
      '15m-60m',
      3600,
      ARRAY['issuer_name','aliases','related_entities']::text[],
      NULL,
      jsonb_build_object('source_layer','official_issuer_site','trust_level','high','requires_endpoint',true,'endpoint_metadata_key','issuer_ir_url')
    ),
    (
      'regulatory_text',
      'macro_api',
      '4h-8h',
      14400,
      ARRAY['ticker','issuer_name','related_entities']::text[],
      'https://www.cbr.ru/press/event/',
      jsonb_build_object('source_name','cbr_regulatory','source_layer','official_macro','trust_level','high')
    ),
    (
      'macro_text',
      'macro_api',
      '4h-8h',
      14400,
      ARRAY['sector','related_entities']::text[],
      'https://www.cbr.ru/press/event/',
      jsonb_build_object('requires_active_instrument',false,'market_wide_allowed',true,'fetch_scope','market_wide_once','source_layer','official_macro','trust_level','high')
    ),
    (
      'cbr_macro',
      'macro_api',
      '4h-8h',
      14400,
      ARRAY['sector','related_entities']::text[],
      'https://www.cbr.ru/press/event/',
      jsonb_build_object('requires_active_instrument',false,'market_wide_allowed',true,'fetch_scope','market_wide_once','source_name','cbr','source_layer','official_macro','trust_level','high')
    ),
    (
      'moex_macro',
      'macro_api',
      '4h-8h',
      14400,
      ARRAY['sector','related_entities']::text[],
      'https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics.json',
      jsonb_build_object('requires_active_instrument',false,'market_wide_allowed',true,'fetch_scope','market_wide_once','source_name','moex','source_layer','official_market','trust_level','high')
    ),
    (
      'fred_eia_macro',
      'macro_api',
      '1d',
      86400,
      ARRAY['sector','related_entities']::text[],
      'https://fred.stlouisfed.org/',
      jsonb_build_object('requires_active_instrument',false,'market_wide_allowed',true,'fetch_scope','market_wide_once','source_name','fred_eia','source_layer','external_macro','trust_level','normal')
    ),
    (
      'rosstat_macro',
      'macro_api',
      '1d',
      86400,
      ARRAY['sector','related_entities']::text[],
      'https://rosstat.gov.ru/',
      jsonb_build_object('requires_active_instrument',false,'market_wide_allowed',true,'fetch_scope','market_wide_once','source_name','rosstat','source_layer','official_macro_optional','trust_level','normal')
    )
)
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
)
SELECT
  'source:' || source_type || ':text_search',
  source_type,
  provider,
  'text_search',
  true,
  discovery_frequency,
  max_age_seconds,
  jsonb_strip_nulls(
    jsonb_build_object(
      'query_fields', query_fields,
      'response_target', 'Raw Text Store',
      'endpoint', endpoint
    )
  ),
  jsonb_build_object('requires_active_instrument', true, 'gateway_only', true) || policy
FROM source_configs
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

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'source_types',
          jsonb_build_array(
            'rbc_news',
            'tass_news',
            'interfax_news',
            'prime_news',
            'finam_news',
            'smartlab_news',
            'issuer_disclosure',
            'prime_disclosure',
            'akm_disclosure',
            'corporate_site',
            'regulatory_text',
            'macro_text',
            'cbr_macro',
            'moex_macro'
          ),
          'source_policy',
          jsonb_build_object(
            'official_confirmation_required_for_corporate_events', true,
            'weak_sources_cannot_trigger_trade', true,
            'rss_sources_fetch_scope', 'market_wide_once'
          )
        )
 WHERE schedule_config_id = 'schedule:data_intake:scheduled_external_news_discovery';

CREATE OR REPLACE VIEW audit.public_data_source_readiness_check AS
WITH required_sources(source_type, provider) AS (
  VALUES
    ('rbc_news', 'news_api'),
    ('tass_news', 'news_api'),
    ('interfax_news', 'news_api'),
    ('prime_news', 'news_api'),
    ('finam_news', 'news_api'),
    ('smartlab_news', 'news_api'),
    ('issuer_disclosure', 'issuer_disclosure'),
    ('prime_disclosure', 'issuer_disclosure'),
    ('akm_disclosure', 'issuer_disclosure'),
    ('corporate_site', 'issuer_disclosure'),
    ('regulatory_text', 'macro_api'),
    ('macro_text', 'macro_api'),
    ('cbr_macro', 'macro_api'),
    ('moex_macro', 'macro_api')
),
source_status AS (
  SELECT
    required_sources.source_type,
    required_sources.provider,
    text_source_config.enabled AS source_enabled,
    text_source_config.query_template ? 'endpoint'
      OR COALESCE((text_source_config.source_policy ->> 'requires_endpoint')::boolean, false) AS has_endpoint_policy
  FROM required_sources
  LEFT JOIN raw_text.text_source_config
    ON text_source_config.source_type = required_sources.source_type
   AND text_source_config.provider = required_sources.provider
),
provider_status AS (
  SELECT
    count(*) FILTER (WHERE provider IN ('news_api', 'issuer_disclosure', 'macro_api') AND enabled) AS enabled_public_provider_count
  FROM request_logs.provider_config
)
SELECT
  'public_text_sources' AS check_name,
  CASE
    WHEN count(*) = count(*) FILTER (WHERE source_enabled AND has_endpoint_policy)
    THEN 'pass'
    ELSE 'fail'
  END AS status,
  (count(*) FILTER (WHERE source_enabled AND has_endpoint_policy))::text AS observed_value,
  count(*)::text AS expected_value,
  jsonb_build_object(
    'missing_or_disabled',
    COALESCE(array_agg(source_type) FILTER (WHERE NOT COALESCE(source_enabled, false) OR NOT has_endpoint_policy), ARRAY[]::text[])
  ) AS details
FROM source_status
UNION ALL
SELECT
  'public_providers_enabled',
  CASE WHEN enabled_public_provider_count = 3 THEN 'pass' ELSE 'fail' END,
  enabled_public_provider_count::text,
  '3',
  '{}'::jsonb
FROM provider_status;

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
  'External Request Gateway Module',
  'info',
  'public_data_source_contour_seeded',
  'Open public MOEX/CBR/news/disclosure data source contour seeded',
  'provider_config',
  'request_logs.provider_config:public_data_sources',
  ARRAY['open_public_sources','gateway_only','official_confirmation_required'],
  jsonb_build_object(
    'news_sources', jsonb_build_array('rbc_news','tass_news','interfax_news','prime_news','finam_news','smartlab_news'),
    'official_sources', jsonb_build_array('issuer_disclosure','prime_disclosure','akm_disclosure','corporate_site','cbr_macro','moex_macro'),
    'optional_sources', jsonb_build_array('fred_eia_macro','rosstat_macro')
  )
);

COMMIT;
