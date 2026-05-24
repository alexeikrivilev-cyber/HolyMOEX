BEGIN;

WITH ranked AS (
  SELECT raw_candle_id,
         row_number() OVER (
           PARTITION BY provider, instrument_id, universe_id, board_id, timeframe, open_ts
           ORDER BY received_at DESC, raw_candle_id DESC
         ) AS rn
    FROM raw_market.raw_candle
)
DELETE FROM raw_market.raw_candle candle
 USING ranked
 WHERE candle.raw_candle_id = ranked.raw_candle_id
   AND ranked.rn > 1;

WITH ranked AS (
  SELECT raw_trade_id,
         row_number() OVER (
           PARTITION BY provider, instrument_id, universe_id, trade_ts, price, quantity, side
           ORDER BY received_at DESC, raw_trade_id DESC
         ) AS rn
    FROM raw_market.raw_trade
)
DELETE FROM raw_market.raw_trade trd
 USING ranked
 WHERE trd.raw_trade_id = ranked.raw_trade_id
   AND ranked.rn > 1;

WITH ranked AS (
  SELECT raw_orderbook_id,
         row_number() OVER (
           PARTITION BY provider, instrument_id, universe_id, snapshot_ts
           ORDER BY received_at DESC, raw_orderbook_id DESC
         ) AS rn
    FROM raw_market.raw_orderbook
)
DELETE FROM raw_market.raw_orderbook book
 USING ranked
 WHERE book.raw_orderbook_id = ranked.raw_orderbook_id
   AND ranked.rn > 1;

WITH ranked AS (
  SELECT raw_index_value_id,
         row_number() OVER (
           PARTITION BY provider, index_id, value_ts
           ORDER BY received_at DESC, raw_index_value_id DESC
         ) AS rn
    FROM raw_market.raw_index_value
)
DELETE FROM raw_market.raw_index_value idx
 USING ranked
 WHERE idx.raw_index_value_id = ranked.raw_index_value_id
   AND ranked.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_candle_provider_instrument_board_tf_open
  ON raw_market.raw_candle (
    provider,
    instrument_id,
    universe_id,
    board_id,
    timeframe,
    open_ts
  ) NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_trade_provider_instrument_ts_price_qty_side
  ON raw_market.raw_trade (
    provider,
    instrument_id,
    universe_id,
    trade_ts,
    price,
    quantity,
    side
  ) NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_orderbook_provider_instrument_snapshot
  ON raw_market.raw_orderbook (
    provider,
    instrument_id,
    universe_id,
    snapshot_ts
  ) NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_index_value_provider_index_ts
  ON raw_market.raw_index_value (
    provider,
    index_id,
    value_ts
  ) NULLS NOT DISTINCT;

CREATE OR REPLACE VIEW audit.moex_market_data_idempotency_check AS
WITH indexes(index_name) AS (
  VALUES
    ('uq_raw_candle_provider_instrument_board_tf_open'),
    ('uq_raw_trade_provider_instrument_ts_price_qty_side'),
    ('uq_raw_orderbook_provider_instrument_snapshot'),
    ('uq_raw_index_value_provider_index_ts')
),
index_status AS (
  SELECT indexes.index_name,
         pg_indexes.indexname IS NOT NULL AS present
    FROM indexes
    LEFT JOIN pg_indexes
      ON pg_indexes.schemaname = 'raw_market'
     AND pg_indexes.indexname = indexes.index_name
)
SELECT
  'raw_market_idempotency_indexes' AS check_name,
  CASE WHEN count(*) = count(*) FILTER (WHERE present) THEN 'pass' ELSE 'fail' END AS status,
  (count(*) FILTER (WHERE present))::text AS observed_value,
  count(*)::text AS expected_value,
  jsonb_build_object('missing_indexes', COALESCE(array_agg(index_name) FILTER (WHERE NOT present), ARRAY[]::text[])) AS details
FROM index_status;

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
  'moex_market_data_idempotency_enabled',
  'Raw market natural keys for MOEX ISS per-instrument ingestion enabled',
  'raw_market',
  'raw_market:moex_market_data_idempotency',
  ARRAY['moex_per_instrument_ingestion','raw_market_dedup','natural_keys'],
  jsonb_build_object(
    'dedup_keys',
    jsonb_build_array(
      'provider+instrument_id+universe_id+board_id+timeframe+open_ts',
      'provider+instrument_id+universe_id+trade_ts+price+quantity+side',
      'provider+instrument_id+universe_id+snapshot_ts',
      'provider+index_id+value_ts'
    )
  )
);

COMMIT;
