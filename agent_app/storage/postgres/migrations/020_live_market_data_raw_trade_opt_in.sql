UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'fetch_raw_trades_default', false,
          'raw_trade_fetch_mode', 'env_opt_in',
          'raw_trade_fetch_env', 'MARKET_DATA_FETCH_RAW_TRADES'
        )
 WHERE schedule_config_id = 'schedule:live_autonomous:market_data:1m';

UPDATE audit.schedule_config
   SET schedule_payload = schedule_payload
     || jsonb_build_object(
          'fetch_raw_trades_default', false,
          'raw_trade_fetch_mode', 'env_opt_in',
          'raw_trade_fetch_env', 'LIQUIDITY_FETCH_RAW_TRADES'
        )
 WHERE schedule_config_id = 'schedule:liquidity_microstructure:realtime';

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Market Data Metrics Module',
    'info',
    'live_market_data_raw_trade_fetch_opt_in',
    'Live market/liquidity schedules no longer fetch MOEX raw trades by default; candle/orderbook features remain active and raw trades can be enabled explicitly by env.',
    'migration',
    '020_live_market_data_raw_trade_opt_in',
    ARRAY['raw_trade_fetch_opt_in', 'live_scheduler_unblocked', 'candle_features_primary'],
    jsonb_build_object(
      'market_data_fetch_raw_trades_default', false,
      'liquidity_fetch_raw_trades_default', false,
      'market_data_env', 'MARKET_DATA_FETCH_RAW_TRADES',
      'liquidity_env', 'LIQUIDITY_FETCH_RAW_TRADES'
    )
);
