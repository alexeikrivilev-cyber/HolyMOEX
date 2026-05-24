BEGIN;

ALTER TABLE raw_text.raw_text_item
  ADD COLUMN IF NOT EXISTS trust_level TEXT,
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(8, 6),
  ADD COLUMN IF NOT EXISTS instrument_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS issuer_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS quality_flags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_raw_text_item_trust_level
  ON raw_text.raw_text_item (trust_level, fetched_at);

CREATE INDEX IF NOT EXISTS idx_raw_text_item_quality_flags
  ON raw_text.raw_text_item USING GIN (quality_flags);

CREATE OR REPLACE VIEW audit.public_text_intake_readiness_check AS
WITH required_columns(column_name) AS (
  VALUES
    ('source'),
    ('source_url'),
    ('published_at'),
    ('fetched_at'),
    ('title'),
    ('body'),
    ('language'),
    ('trust_level'),
    ('confidence_score'),
    ('instrument_candidates'),
    ('issuer_candidates'),
    ('quality_flags')
),
column_status AS (
  SELECT required_columns.column_name,
         columns.column_name IS NOT NULL AS present
    FROM required_columns
    LEFT JOIN information_schema.columns
      ON columns.table_schema = 'raw_text'
     AND columns.table_name = 'raw_text_item'
     AND columns.column_name = required_columns.column_name
),
source_status AS (
  SELECT count(*) FILTER (
           WHERE source_type IN (
             'rbc_news',
             'tass_news',
             'interfax_news',
             'prime_news',
             'finam_news',
             'smartlab_news',
             'issuer_disclosure',
             'prime_disclosure',
             'akm_disclosure',
             'corporate_site'
           )
             AND enabled
             AND source_policy ? 'trust_level'
         ) AS configured_source_count
    FROM raw_text.text_source_config
)
SELECT
  'raw_text_product_columns' AS check_name,
  CASE WHEN count(*) = count(*) FILTER (WHERE present) THEN 'pass' ELSE 'fail' END AS status,
  (count(*) FILTER (WHERE present))::text AS observed_value,
  count(*)::text AS expected_value,
  jsonb_build_object(
    'missing_columns',
    COALESCE(array_agg(column_name) FILTER (WHERE NOT present), ARRAY[]::text[])
  ) AS details
FROM column_status
UNION ALL
SELECT
  'trusted_source_policy',
  CASE WHEN configured_source_count >= 10 THEN 'pass' ELSE 'fail' END,
  configured_source_count::text,
  '>=10 enabled news/disclosure/corporate source configs with trust_level',
  '{}'::jsonb
FROM source_status;

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
  'raw_text_product_intake_fields_seeded',
  'Raw text product intake fields added for trust, candidates, confidence and quality flags',
  'raw_text.raw_text_item',
  'raw_text.raw_text_item:product_intake_fields',
  ARRAY['product_level_public_source_intake','official_confirmation_required'],
  jsonb_build_object(
    'columns', jsonb_build_array('trust_level','confidence_score','instrument_candidates','issuer_candidates','quality_flags'),
    'news_status', 'candidate_early_signal',
    'official_status', 'confirmation_layer'
  )
);

COMMIT;
