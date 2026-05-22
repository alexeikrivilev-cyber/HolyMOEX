BEGIN;

ALTER TABLE registry.instrument_profile
  ADD COLUMN IF NOT EXISTS max_trade_quantity INTEGER
    CHECK (max_trade_quantity IS NULL OR max_trade_quantity >= 0),
  ADD COLUMN IF NOT EXISTS execution_enabled BOOLEAN NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS registry.universe_snapshot (
  universe_snapshot_id TEXT PRIMARY KEY,
  universe_id TEXT NOT NULL REFERENCES registry.instrument_universe(universe_id),
  active_instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  instrument_profiles_ref TEXT NOT NULL,
  validation_status TEXT NOT NULL CHECK (validation_status IN ('valid', 'invalid')),
  errors TEXT[] NOT NULL DEFAULT '{}',
  warnings TEXT[] NOT NULL DEFAULT '{}',
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL,
  confidence_score NUMERIC(8, 6) NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_universe_snapshot_lookup
  ON registry.universe_snapshot (universe_id, validation_status, created_at);

DROP TABLE IF EXISTS seed_selected_instruments;
CREATE TEMP TABLE seed_selected_instruments (
  instrument_id TEXT,
  ticker TEXT,
  figi TEXT,
  isin TEXT,
  class_code TEXT,
  board_id TEXT,
  lot_size INTEGER,
  min_price_increment NUMERIC(20, 8),
  currency TEXT,
  sector TEXT,
  issuer_name TEXT,
  aliases TEXT[],
  related_entities TEXT[],
  is_active BOOLEAN,
  tradable BOOLEAN,
  allowed_horizons TEXT[],
  arena_go_secid TEXT,
  arena_go_quantity_mode TEXT,
  max_trade_quantity INTEGER,
  execution_enabled BOOLEAN,
  moex_shortname TEXT,
  moex_latname TEXT,
  moex_regnumber TEXT,
  moex_issuesize NUMERIC(24, 0),
  moex_listlevel INTEGER,
  moex_prevprice NUMERIC(24, 10),
  figi_source TEXT
) ON COMMIT DROP;

INSERT INTO seed_selected_instruments VALUES
  ('moex:LKOH', 'LKOH', 'BBG004731032', 'RU0009024277', 'TQBR', 'TQBR', 1, 0.50000000, 'RUB',
    'energy', 'LUKOIL PJSC',
    ARRAY['LKOH', 'LUKOIL', 'ЛУКОЙЛ', 'НК ЛУКОЙЛ', 'RU0009024277', 'BBG004731032'],
    ARRAY['LUKOIL', 'oil', 'energy', 'sector:energy'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'LKOH', 'shares', 9, true,
    'ЛУКОЙЛ', 'LUKOIL', '1-01-00077-A', 692865762, 1, 5119.0000000000, 'manual_seed'
  ),
  ('moex:SBER', 'SBER', 'BBG004730N88', 'RU0009029540', 'TQBR', 'TQBR', 1, 0.01000000, 'RUB',
    'financials', 'Sberbank of Russia PJSC',
    ARRAY['SBER', 'SBERBANK', 'СБЕР', 'СБЕРБАНК', 'RU0009029540', 'BBG004730N88'],
    ARRAY['Sberbank', 'banking', 'financials', 'sector:financials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'SBER', 'shares', 154, true,
    'Сбербанк', 'Sberbank', '10301481B', 21586948000, 1, 323.2500000000, 'manual_seed'
  ),
  ('moex:ROSN', 'ROSN', 'BBG004731354', 'RU000A0J2Q06', 'TQBR', 'TQBR', 1, 0.05000000, 'RUB',
    'energy', 'Rosneft Oil Company PJSC',
    ARRAY['ROSN', 'ROSNEFT', 'РОСНЕФТЬ', 'НК РОСНЕФТЬ', 'RU000A0J2Q06', 'BBG004731354'],
    ARRAY['Rosneft', 'oil', 'energy', 'sector:energy'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'ROSN', 'shares', 123, true,
    'Роснефть', 'Rosneft', '1-02-00122-A', 10598177817, 1, 403.8500000000, 'manual_seed'
  ),
  ('moex:GAZP', 'GAZP', 'BBG004730RP0', 'RU0007661625', 'TQBR', 'TQBR', 10, 0.01000000, 'RUB',
    'energy', 'Gazprom PJSC',
    ARRAY['GAZP', 'GAZPROM', 'ГАЗПРОМ', 'RU0007661625', 'BBG004730RP0'],
    ARRAY['Gazprom', 'gas', 'energy', 'sector:energy'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'GAZP', 'shares', 420, true,
    'ГАЗПРОМ ао', 'Gazprom', '1-02-00028-A', 23673512900, 1, 119.0100000000, 'manual_seed'
  ),
  ('moex:VTBR', 'VTBR', 'BBG004730ZJ9', 'RU000A0JP5V6', 'TQBR', 'TQBR', 1, 0.00500000, 'RUB',
    'financials', 'VTB Bank PJSC',
    ARRAY['VTBR', 'VTB', 'ВТБ', 'БАНК ВТБ', 'RU000A0JP5V6', 'BBG004730ZJ9'],
    ARRAY['VTB Bank', 'banking', 'financials', 'sector:financials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'VTBR', 'shares', 557, true,
    'ВТБ ао', 'VTB', '10401000B', 12927766416, 1, 89.7550000000, 'manual_seed'
  ),
  ('moex:YDEX', 'YDEX', 'BBG01NSDQL68', 'RU000A107T19', 'TQBR', 'TQBR', 1, 0.50000000, 'RUB',
    'technology', 'MKPAO Yandex',
    ARRAY['YDEX', 'YANDEX', 'ЯНДЕКС', 'МКПАО ЯНДЕКС', 'RU000A107T19', 'BBG01NSDQL68'],
    ARRAY['Yandex', 'technology', 'internet', 'sector:technology'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'YDEX', 'shares', 12, true,
    'ЯНДЕКС', 'YANDEX', '1-01-16777-A', 394967916, 1, 4035.0000000000, 'manual_seed'
  ),
  ('moex:PLZL', 'PLZL', 'BBG000R607Y3', 'RU000A0JNAA8', 'TQBR', 'TQBR', 1, 0.20000000, 'RUB',
    'materials', 'Polyus PJSC',
    ARRAY['PLZL', 'POLYUS', 'ПОЛЮС', 'RU000A0JNAA8', 'BBG000R607Y3'],
    ARRAY['Polyus', 'gold', 'materials', 'sector:materials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'PLZL', 'shares', 24, true,
    'Полюс', 'Polus', '1-01-55192-E', 1360694001, 1, 2071.0000000000, 'manual_seed'
  ),
  ('moex:T', 'T', NULL, 'RU000A107UL4', 'TQBR', 'TQBR', 1, 0.02000000, 'RUB',
    'financials', 'T-Technologies IPJSC',
    ARRAY['T', 'T-TECHNOLOGIES', 'T-ТЕХНОЛОГИИ', 'Т-ТЕХНОЛОГИИ', 'TINKOFF', 'ТИНЬКОФФ', 'TCSG', 'RU000A107UL4'],
    ARRAY['T-Bank', 'Tinkoff', 'banking', 'financials', 'sector:financials'],
    false, false, ARRAY['intraday', 'swing', 'position'], 'T', 'shares', 0, false,
    'Т-Техно ао', 'IPJSC TCS Holding', '1-01-16784-A', 2682747860, 1, 312.4600000000, 'missing_official_figi'
  ),
  ('moex:NVTK', 'NVTK', 'BBG00475KKY8', 'RU000A0DKVS5', 'TQBR', 'TQBR', 1, 0.10000000, 'RUB',
    'energy', 'Novatek PJSC',
    ARRAY['NVTK', 'NOVATEK', 'НОВАТЭК', 'RU000A0DKVS5', 'BBG00475KKY8'],
    ARRAY['Novatek', 'gas', 'lng', 'energy', 'sector:energy'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'NVTK', 'shares', 44, true,
    'Новатэк ао', 'NOVATEK', '1-02-00268-E', 3036306000, 1, 1122.0000000000, 'manual_seed'
  ),
  ('moex:X5', 'X5', NULL, 'RU000A108X38', 'TQBR', 'TQBR', 1, 0.50000000, 'RUB',
    'consumer_staples', 'X5 Corporate Center PJSC',
    ARRAY['X5', 'X5 RETAIL', 'X5 GROUP', 'ИКС 5', 'ПЯТЕРОЧКА', 'PEREKRESTOK', 'RU000A108X38'],
    ARRAY['X5', 'retail', 'consumer staples', 'sector:consumer_staples'],
    false, false, ARRAY['intraday', 'swing', 'position'], 'X5', 'shares', 0, false,
    'КЦ ИКС 5', 'X5 Corporate Center', '1-01-16812-A', 271572872, 1, 2451.0000000000, 'missing_official_figi'
  ),
  ('moex:GMKN', 'GMKN', 'BBG004731489', 'RU0007288411', 'TQBR', 'TQBR', 10, 0.02000000, 'RUB',
    'materials', 'MMC Norilsk Nickel PJSC',
    ARRAY['GMKN', 'NORILSK NICKEL', 'NORNICKEL', 'НОРНИКЕЛЬ', 'ГМК НОРНИКЕЛЬ', 'RU0007288411', 'BBG004731489'],
    ARRAY['Norilsk Nickel', 'nickel', 'metals', 'materials', 'sector:materials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'GMKN', 'shares', 390, true,
    'ГМКНорНик', 'NorNickel GMK', '1-01-40155-F', 15286339700, 1, 127.0600000000, 'manual_seed'
  ),
  ('moex:MGNT', 'MGNT', 'BBG004RVFCY3', 'RU000A0JKQU8', 'TQBR', 'TQBR', 1, 0.50000000, 'RUB',
    'consumer_staples', 'Magnit PJSC',
    ARRAY['MGNT', 'MAGNIT', 'МАГНИТ', 'RU000A0JKQU8', 'BBG004RVFCY3'],
    ARRAY['Magnit', 'retail', 'consumer staples', 'sector:consumer_staples'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'MGNT', 'shares', 20, true,
    'Магнит ао', 'Magnit', '1-01-60525-P', 101911355, 3, 2386.0000000000, 'manual_seed'
  ),
  ('moex:ALRS', 'ALRS', 'BBG004S68B31', 'RU0007252813', 'TQBR', 'TQBR', 10, 0.01000000, 'RUB',
    'materials', 'ALROSA PJSC',
    ARRAY['ALRS', 'ALROSA', 'АЛРОСА', 'RU0007252813', 'BBG004S68B31'],
    ARRAY['ALROSA', 'diamonds', 'materials', 'sector:materials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'ALRS', 'shares', 1830, true,
    'АЛРОСА ао', 'ALROSA ao', '1-03-40046-N', 7364965630, 1, 27.2800000000, 'manual_seed'
  ),
  ('moex:AFLT', 'AFLT', 'BBG004S683W7', 'RU0009062285', 'TQBR', 'TQBR', 10, 0.01000000, 'RUB',
    'industrials', 'Aeroflot PJSC',
    ARRAY['AFLT', 'AEROFLOT', 'АЭРОФЛОТ', 'RU0009062285', 'BBG004S683W7'],
    ARRAY['Aeroflot', 'airlines', 'transport', 'industrials', 'sector:industrials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'AFLT', 'shares', 1060, true,
    'Аэрофлот', 'Aeroflot', '1-01-00010-A', 3975771215, 1, 46.7300000000, 'manual_seed'
  ),
  ('moex:CHMF', 'CHMF', 'BBG00475K6C3', 'RU0009046510', 'TQBR', 'TQBR', 1, 0.20000000, 'RUB',
    'materials', 'Severstal PJSC',
    ARRAY['CHMF', 'SEVERSTAL', 'СЕВЕРСТАЛЬ', 'RU0009046510', 'BBG00475K6C3'],
    ARRAY['Severstal', 'steel', 'materials', 'sector:materials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'CHMF', 'shares', 66, true,
    'СевСт-ао', 'Severstal - ao', '1-02-00143-A', 837718660, 1, 750.6000000000, 'manual_seed'
  ),
  ('moex:NLMK', 'NLMK', 'BBG004S681B4', 'RU0009046452', 'TQBR', 'TQBR', 10, 0.02000000, 'RUB',
    'materials', 'Novolipetsk Steel PJSC',
    ARRAY['NLMK', 'NOVOLIPETSK STEEL', 'НЛМК', 'RU0009046452', 'BBG004S681B4'],
    ARRAY['NLMK', 'steel', 'materials', 'sector:materials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'NLMK', 'shares', 600, true,
    'НЛМК ао', 'NLMK ao', '1-01-00102-A', 5993227240, 1, 83.2400000000, 'manual_seed'
  ),
  ('moex:MOEX', 'MOEX', 'BBG004730JJ5', 'RU000A0JR4A1', 'TQBR', 'TQBR', 10, 0.01000000, 'RUB',
    'financials', 'Moscow Exchange PJSC',
    ARRAY['MOEX', 'MOSCOW EXCHANGE', 'МОСКОВСКАЯ БИРЖА', 'МОСБИРЖА', 'RU000A0JR4A1', 'BBG004730JJ5'],
    ARRAY['Moscow Exchange', 'exchange', 'financials', 'sector:financials'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'MOEX', 'shares', 280, true,
    'МосБиржа', 'MoscowExchange', '1-05-08443-H', 2276401458, 1, 173.1800000000, 'manual_seed'
  ),
  ('moex:SNGSP', 'SNGSP', 'BBG004S681M2', 'RU0009029524', 'TQBR', 'TQBR', 10, 0.00500000, 'RUB',
    'energy', 'Surgutneftegas PJSC preferred',
    ARRAY['SNGSP', 'SURGUTNEFTEGAS PREF', 'СУРГУТНЕФТЕГАЗ ПРЕФ', 'СУРГУТНФГЗ-П', 'RU0009029524', 'BBG004S681M2'],
    ARRAY['Surgutneftegas', 'oil', 'energy', 'preferred_share', 'sector:energy'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'SNGSP', 'shares', 1230, true,
    'Сургнфгз-п', 'Surgut-pref', '2-01-00155-A', 7701998235, 2, 40.4350000000, 'manual_seed'
  ),
  ('moex:MTSS', 'MTSS', 'BBG004S681W1', 'RU0007775219', 'TQBR', 'TQBR', 10, 0.05000000, 'RUB',
    'telecom', 'Mobile TeleSystems PJSC',
    ARRAY['MTSS', 'MTS', 'МТС', 'МОБИЛЬНЫЕ ТЕЛЕСИСТЕМЫ', 'RU0007775219', 'BBG004S681W1'],
    ARRAY['MTS', 'telecom', 'sector:telecom'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'MTSS', 'shares', 220, true,
    'МТС-ао', 'MTS', '1-01-04715-A', 1998381575, 1, 226.6000000000, 'manual_seed'
  ),
  ('moex:PIKK', 'PIKK', 'BBG004S68BH6', 'RU000A0JP7J7', 'TQBR', 'TQBR', 1, 0.10000000, 'RUB',
    'real_estate', 'PIK SZ PJSC',
    ARRAY['PIKK', 'PIK', 'ПИК', 'ПИК СЗ', 'RU000A0JP7J7', 'BBG004S68BH6'],
    ARRAY['PIK', 'homebuilding', 'real estate', 'sector:real_estate'],
    true, true, ARRAY['intraday', 'swing', 'position'], 'PIKK', 'shares', 92, true,
    'ПИК ао', 'PIK SZ', '1-02-01556-A', 660497344, 1, 540.2000000000, 'manual_seed'
  );

INSERT INTO registry.instrument_universe (
  universe_id,
  universe_name,
  max_active_instruments,
  status
) VALUES (
  'moex_top20_manual',
  'Manual MOEX up to 20 trading universe',
  20,
  'active'
)
ON CONFLICT (universe_id) DO UPDATE SET
  universe_name = EXCLUDED.universe_name,
  max_active_instruments = EXCLUDED.max_active_instruments,
  status = EXCLUDED.status,
  updated_at = now();

INSERT INTO registry.instrument_profile (
  instrument_id,
  universe_id,
  ticker,
  figi,
  isin,
  class_code,
  board_id,
  lot_size,
  min_price_increment,
  currency,
  sector,
  issuer_name,
  aliases,
  related_entities,
  is_active,
  tradable,
  allowed_horizons,
  arena_go_secid,
  arena_go_quantity_mode,
  max_trade_quantity,
  execution_enabled,
  metadata
)
SELECT
  instrument_id,
  'moex_top20_manual',
  ticker,
  figi,
  isin,
  class_code,
  board_id,
  lot_size,
  min_price_increment,
  currency,
  sector,
  issuer_name,
  aliases,
  related_entities,
  is_active,
  tradable,
  allowed_horizons,
  arena_go_secid,
  arena_go_quantity_mode,
  max_trade_quantity,
  execution_enabled,
  jsonb_build_object(
    'metadata_source', CASE WHEN figi IS NULL THEN 'moex_iss_tqbr' ELSE 'manual_universe_config+moex_iss_tqbr' END,
    'moex_metadata_as_of', '2026-05-21',
    'seed_version', '2026-05-21',
    'moex_shortname', moex_shortname,
    'moex_latname', moex_latname,
    'moex_regnumber', moex_regnumber,
    'moex_issuesize', moex_issuesize,
    'moex_listlevel', moex_listlevel,
    'moex_prevprice', moex_prevprice,
    'max_order_value_rub_basis', 50000,
    'figi_source', figi_source,
    'execution_enabled', execution_enabled,
    'max_trade_quantity', max_trade_quantity,
    'source_urls', jsonb_build_array(
      'https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json'
    )
  )
FROM seed_selected_instruments
ON CONFLICT (instrument_id) DO UPDATE SET
  ticker = EXCLUDED.ticker,
  universe_id = EXCLUDED.universe_id,
  figi = EXCLUDED.figi,
  isin = EXCLUDED.isin,
  class_code = EXCLUDED.class_code,
  board_id = EXCLUDED.board_id,
  lot_size = EXCLUDED.lot_size,
  min_price_increment = EXCLUDED.min_price_increment,
  currency = EXCLUDED.currency,
  sector = EXCLUDED.sector,
  issuer_name = EXCLUDED.issuer_name,
  aliases = EXCLUDED.aliases,
  related_entities = EXCLUDED.related_entities,
  is_active = EXCLUDED.is_active,
  tradable = EXCLUDED.tradable,
  allowed_horizons = EXCLUDED.allowed_horizons,
  arena_go_secid = EXCLUDED.arena_go_secid,
  arena_go_quantity_mode = EXCLUDED.arena_go_quantity_mode,
  max_trade_quantity = EXCLUDED.max_trade_quantity,
  execution_enabled = EXCLUDED.execution_enabled,
  metadata = EXCLUDED.metadata,
  updated_at = now();

INSERT INTO registry.instrument_alias (instrument_id, alias, alias_type, source)
SELECT
  instrument_id,
  alias,
  CASE
    WHEN alias = ticker THEN 'ticker'
    WHEN alias IN (isin, figi) THEN 'identifier'
    ELSE 'text_alias'
  END,
  'manual_seed'
FROM seed_selected_instruments
CROSS JOIN LATERAL unnest(aliases) AS alias_values(alias)
WHERE alias IS NOT NULL AND btrim(alias) <> ''
ON CONFLICT (instrument_id, alias, alias_type) DO NOTHING;

INSERT INTO registry.instrument_mapping (
  instrument_id,
  provider,
  provider_symbol,
  provider_payload
)
SELECT
  instrument_id,
  'arena_go',
  arena_go_secid,
  jsonb_build_object(
    'quantity_mode', arena_go_quantity_mode,
    'lot_size', lot_size,
    'max_trade_quantity', max_trade_quantity,
    'execution_enabled', execution_enabled
  )
FROM seed_selected_instruments
ON CONFLICT (instrument_id, provider) DO UPDATE SET
  provider_symbol = EXCLUDED.provider_symbol,
  provider_payload = EXCLUDED.provider_payload;

INSERT INTO registry.instrument_mapping (
  instrument_id,
  provider,
  provider_symbol,
  provider_payload
)
SELECT
  instrument_id,
  'moex_iss',
  ticker,
  jsonb_build_object(
    'board_id', board_id,
    'class_code', class_code,
    'isin', isin,
    'moex_shortname', moex_shortname,
    'moex_latname', moex_latname,
    'moex_regnumber', moex_regnumber
  )
FROM seed_selected_instruments
ON CONFLICT (instrument_id, provider) DO UPDATE SET
  provider_symbol = EXCLUDED.provider_symbol,
  provider_payload = EXCLUDED.provider_payload;

INSERT INTO registry.instrument_mapping (
  instrument_id,
  provider,
  provider_symbol,
  provider_payload
)
SELECT
  instrument_id,
  'manual_figi',
  figi,
  jsonb_build_object(
    'figi_source', figi_source,
    'isin', isin,
    'ticker', ticker
  )
FROM seed_selected_instruments
WHERE figi IS NOT NULL
ON CONFLICT (instrument_id, provider) DO UPDATE SET
  provider_symbol = EXCLUDED.provider_symbol,
  provider_payload = EXCLUDED.provider_payload;

DELETE FROM registry.instrument_mapping mapping
USING seed_selected_instruments seed
WHERE mapping.instrument_id = seed.instrument_id
  AND mapping.provider IN ('openfigi', 't_invest_figi');

DELETE FROM registry.instrument_mapping mapping
USING seed_selected_instruments seed
WHERE mapping.instrument_id = seed.instrument_id
  AND mapping.provider = 'manual_figi'
  AND seed.figi IS NULL;

DELETE FROM registry.instrument_alias alias_row
USING seed_selected_instruments seed
WHERE alias_row.instrument_id = seed.instrument_id
  AND seed.figi IS NULL
  AND alias_row.alias_type = 'identifier'
  AND alias_row.alias <> seed.isin;

WITH snapshot_stats AS (
  SELECT
    array_agg(instrument_id ORDER BY instrument_id) FILTER (WHERE is_active) AS active_instrument_ids,
    count(*) FILTER (WHERE is_active) AS active_instrument_count,
    count(*) FILTER (WHERE NOT is_active) AS inactive_instrument_count,
    count(*) FILTER (WHERE is_active AND figi IS NULL) AS missing_figi_count,
    COALESCE(
      array_agg('missing_figi:' || instrument_id ORDER BY instrument_id)
        FILTER (WHERE is_active AND figi IS NULL),
      ARRAY[]::TEXT[]
    ) AS missing_figi_errors,
    count(*) FILTER (WHERE NOT is_active AND figi IS NULL) AS inactive_missing_figi_count,
    COALESCE(
      array_agg('inactive_missing_figi:' || instrument_id ORDER BY instrument_id)
        FILTER (WHERE NOT is_active AND figi IS NULL),
      ARRAY[]::TEXT[]
    ) AS inactive_missing_figi_warnings
  FROM seed_selected_instruments
)
INSERT INTO registry.universe_snapshot (
  universe_snapshot_id,
  universe_id,
  active_instrument_ids,
  instrument_profiles_ref,
  validation_status,
  errors,
  warnings,
  metrics,
  source_module,
  calculation_version,
  confidence_score
)
SELECT
  'initial:moex_top20_manual:2026-05-21',
  'moex_top20_manual',
  active_instrument_ids,
  'registry.instrument_profile:moex_top20_manual',
  CASE WHEN missing_figi_count = 0 THEN 'valid' ELSE 'invalid' END,
  missing_figi_errors,
  inactive_missing_figi_warnings,
  jsonb_build_object(
    'active_instrument_count', active_instrument_count,
    'inactive_instrument_count', inactive_instrument_count,
    'metadata_completeness_score',
      round((((active_instrument_count * 14) - missing_figi_count)::numeric / (active_instrument_count * 14)), 6),
    'missing_figi_count', missing_figi_count,
    'inactive_missing_figi_count', inactive_missing_figi_count,
    'mapping_conflict_count', 0,
    'seed_version', '2026-05-21'
  ),
  'Selected Instruments Registry Module',
  'selected_instruments_registry_v1',
  CASE WHEN missing_figi_count = 0 THEN 1.0 ELSE 0.992857 END
FROM snapshot_stats
ON CONFLICT (universe_snapshot_id) DO UPDATE SET
  active_instrument_ids = EXCLUDED.active_instrument_ids,
  validation_status = EXCLUDED.validation_status,
  errors = EXCLUDED.errors,
  warnings = EXCLUDED.warnings,
  metrics = EXCLUDED.metrics,
  confidence_score = EXCLUDED.confidence_score;

INSERT INTO portfolio.portfolio_snapshot (
  portfolio_snapshot_id,
  portfolio_id,
  universe_id,
  as_of_ts,
  initial_capital_rub,
  cash,
  equity,
  gross_exposure,
  net_exposure,
  realized_pnl,
  unrealized_pnl,
  source_module,
  source_refs,
  payload
) VALUES (
  'initial:arena_go_default:moex_top20_manual',
  'arena_go_default',
  'moex_top20_manual',
  '2026-05-21T00:00:00Z',
  1000000,
  1000000,
  1000000,
  0,
  0,
  0,
  0,
  'Portfolio State Module',
  ARRAY['config/api_keys.local.env:INITIAL_CAPITAL_RUB'],
  '{"run_mode":"paper_trading","seed_version":"2026-05-21"}'::jsonb
)
ON CONFLICT (portfolio_snapshot_id) DO UPDATE SET
  as_of_ts = EXCLUDED.as_of_ts,
  cash = EXCLUDED.cash,
  equity = EXCLUDED.equity,
  initial_capital_rub = EXCLUDED.initial_capital_rub,
  payload = EXCLUDED.payload;

INSERT INTO portfolio.position_state (
  position_state_id,
  portfolio_id,
  instrument_id,
  as_of_ts,
  quantity,
  average_price,
  market_price,
  market_value,
  unrealized_pnl,
  source_module,
  source_refs,
  payload
)
SELECT
  'initial:arena_go_default:' || instrument_id,
  'arena_go_default',
  instrument_id,
  '2026-05-21T00:00:00Z',
  0,
  NULL,
  NULL,
  0,
  0,
  'Portfolio State Module',
  ARRAY['registry.instrument_profile'],
  '{"run_mode":"paper_trading","seed_version":"2026-05-21"}'::jsonb
FROM seed_selected_instruments
WHERE is_active
ON CONFLICT (position_state_id) DO UPDATE SET
  as_of_ts = EXCLUDED.as_of_ts,
  quantity = EXCLUDED.quantity,
  market_value = EXCLUDED.market_value,
  unrealized_pnl = EXCLUDED.unrealized_pnl,
  payload = EXCLUDED.payload;

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
  'Selected Instruments Registry Module',
  'info',
  'manual_universe_seeded',
  'Manual MOEX universe seeded with MOEX ISS metadata; incomplete FIGI entries are inactive and execution-disabled',
  'instrument_universe',
  'moex_top20_manual',
  ARRAY['manual_seed', 'moex_iss_metadata', 'partial_figi_mapping', 'inactive_until_required_ids', 'execution_limits_seeded'],
  '{"instrument_count":20,"active_instrument_count":18,"inactive_instrument_count":2,"inactive_missing_figi":["moex:T","moex:X5"],"seed_version":"2026-05-21"}'::jsonb
);

COMMIT;
