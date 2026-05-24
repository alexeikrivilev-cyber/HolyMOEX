BEGIN;

ALTER TABLE raw_macro.raw_macro_point
  ADD COLUMN IF NOT EXISTS series_name TEXT,
  ADD COLUMN IF NOT EXISTS source_url TEXT,
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(8, 6) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS quality_flags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_raw_macro_point_series_ts
  ON raw_macro.raw_macro_point (series_id, point_ts);

CREATE INDEX IF NOT EXISTS idx_raw_macro_point_quality_flags
  ON raw_macro.raw_macro_point USING GIN (quality_flags);

UPDATE request_logs.provider_config
   SET enabled = true,
       config_payload = config_payload
         || jsonb_build_object(
              'macro_series_catalog',
              jsonb_build_object(
                'key_rate', jsonb_build_object('provider','macro_api','source','cbr','series_name','CBR key rate','unit','percent','source_url','https://www.cbr.ru/hd_base/KeyRate/','confidence_score',0.95,'quality_flags',jsonb_build_array('cbr_public_html_loader')),
                'ruonia', jsonb_build_object('provider','macro_api','source','cbr','series_name','RUONIA','unit','percent','source_url','https://www.cbr.ru/hd_base/ruonia/','confidence_score',0.95,'quality_flags',jsonb_build_array('cbr_public_html_loader')),
                'ofz_1y', jsonb_build_object('provider','macro_api','source','cbr_zcyc','series_name','CBR ZCYC OFZ 1Y','unit','percent','source_url','https://www.cbr.ru/hd_base/zcyc_params/','confidence_score',0.75,'quality_flags',jsonb_build_array('cbr_zcyc_public_html_loader','maturity_1y')),
                'ofz_2y', jsonb_build_object('provider','macro_api','source','cbr_zcyc','series_name','CBR ZCYC OFZ 2Y','unit','percent','source_url','https://www.cbr.ru/hd_base/zcyc_params/','confidence_score',0.75,'quality_flags',jsonb_build_array('cbr_zcyc_public_html_loader','maturity_2y')),
                'ofz_10y', jsonb_build_object('provider','macro_api','source','cbr_zcyc','series_name','CBR ZCYC OFZ 10Y','unit','percent','source_url','https://www.cbr.ru/hd_base/zcyc_params/','confidence_score',0.75,'quality_flags',jsonb_build_array('cbr_zcyc_public_html_loader','maturity_10y')),
                'currency', jsonb_build_object('provider','macro_api','source','cbr_fx','series_name','USD/RUB official CBR rate','unit','RUB','source_url','https://www.cbr.ru/scripts/XML_dynamic.asp','confidence_score',0.95,'quality_flags',jsonb_build_array('cbr_xml_dynamic_loader')),
                'usd_rub_cbr', jsonb_build_object('provider','macro_api','source','cbr_fx','series_name','USD/RUB official CBR rate','unit','RUB','source_url','https://www.cbr.ru/scripts/XML_dynamic.asp','confidence_score',0.95,'quality_flags',jsonb_build_array('cbr_xml_dynamic_loader')),
                'cny_rub_cbr', jsonb_build_object('provider','macro_api','source','cbr_fx','series_name','CNY/RUB official CBR rate','unit','RUB','source_url','https://www.cbr.ru/scripts/XML_dynamic.asp','confidence_score',0.95,'quality_flags',jsonb_build_array('cbr_xml_dynamic_loader')),
                'oil', jsonb_build_object('provider','macro_api','source','fred','series_name','Brent daily FRED','unit','USD/bbl','source_url','https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU','confidence_score',0.85,'quality_flags',jsonb_build_array('fred_csv_loader')),
                'brent_fred', jsonb_build_object('provider','macro_api','source','fred','series_name','Brent daily FRED','unit','USD/bbl','source_url','https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU','confidence_score',0.85,'quality_flags',jsonb_build_array('fred_csv_loader')),
                'wti_fred', jsonb_build_object('provider','macro_api','source','fred','series_name','WTI daily FRED','unit','USD/bbl','source_url','https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILWTICO','confidence_score',0.85,'quality_flags',jsonb_build_array('fred_csv_loader'))
              )
            )
 WHERE provider = 'macro_api';

UPDATE request_logs.provider_config
   SET enabled = true,
       config_payload = config_payload
         || jsonb_build_object(
              'public_market_series_catalog',
              jsonb_build_object(
                'imoex', jsonb_build_object('provider','moex_iss','index_id','IMOEX','source_url','https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities/IMOEX/candles.json','quality_flags',jsonb_build_array('moex_iss_index_candles_loader')),
                'rtsi', jsonb_build_object('provider','moex_iss','index_id','RTSI','source_url','https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities/RTSI/candles.json','quality_flags',jsonb_build_array('moex_iss_index_candles_loader')),
                'rgbi', jsonb_build_object('provider','moex_iss','index_id','RGBI','source_url','https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities/RGBI/candles.json','quality_flags',jsonb_build_array('moex_iss_bond_index_candles_loader')),
                'usd_rub_moex', jsonb_build_object('provider','moex_iss','index_id','USD000UTSTOM','source_url','https://iss.moex.com/iss/engines/currency/markets/selt/boards/CETS/securities/USD000UTSTOM/candles.json','quality_flags',jsonb_build_array('moex_iss_fx_candles_loader')),
                'cny_rub_moex', jsonb_build_object('provider','moex_iss','index_id','CNYRUB_TOM','source_url','https://iss.moex.com/iss/engines/currency/markets/selt/boards/CETS/securities/CNYRUB_TOM/candles.json','quality_flags',jsonb_build_array('moex_iss_fx_candles_loader'))
              )
            )
 WHERE provider = 'moex_iss';

CREATE OR REPLACE VIEW audit.public_macro_series_readiness_check AS
WITH required_columns(column_name) AS (
  VALUES
    ('series_id'),
    ('series_name'),
    ('point_ts'),
    ('value'),
    ('unit'),
    ('provider'),
    ('source_url'),
    ('confidence_score'),
    ('quality_flags')
),
column_status AS (
  SELECT required_columns.column_name,
         columns.column_name IS NOT NULL AS present
    FROM required_columns
    LEFT JOIN information_schema.columns
      ON columns.table_schema = 'raw_macro'
     AND columns.table_name = 'raw_macro_point'
     AND columns.column_name = required_columns.column_name
),
provider_status AS (
  SELECT
    bool_or(provider = 'macro_api' AND enabled AND config_payload ? 'macro_series_catalog') AS has_macro_catalog,
    bool_or(provider = 'moex_iss' AND enabled AND config_payload ? 'public_market_series_catalog') AS has_market_catalog
  FROM request_logs.provider_config
  WHERE provider IN ('macro_api', 'moex_iss')
)
SELECT
  'raw_macro_product_columns' AS check_name,
  CASE WHEN count(*) = count(*) FILTER (WHERE present) THEN 'pass' ELSE 'fail' END AS status,
  (count(*) FILTER (WHERE present))::text AS observed_value,
  count(*)::text AS expected_value,
  jsonb_build_object('missing_columns', COALESCE(array_agg(column_name) FILTER (WHERE NOT present), ARRAY[]::text[])) AS details
FROM column_status
UNION ALL
SELECT
  'public_macro_market_provider_catalogs',
  CASE WHEN COALESCE(has_macro_catalog, false) AND COALESCE(has_market_catalog, false) THEN 'pass' ELSE 'fail' END,
  jsonb_build_object('has_macro_catalog', COALESCE(has_macro_catalog, false), 'has_market_catalog', COALESCE(has_market_catalog, false))::text,
  'enabled macro_series_catalog and public_market_series_catalog present',
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
  'Market Context Module',
  'info',
  'public_macro_series_loaders_seeded',
  'Public macro series catalog and raw macro product fields seeded',
  'raw_macro.raw_macro_point',
  'raw_macro.raw_macro_point:public_macro_series',
  ARRAY['cbr_macro','moex_market_data','fred_eia_optional','raw_macro_product_fields'],
  jsonb_build_object(
    'required_series', jsonb_build_array('key_rate','currency','ruonia','ofz_1y','ofz_2y','ofz_10y','oil'),
    'optional_series', jsonb_build_array('wti_fred','rosstat_macro')
  )
);

COMMIT;
