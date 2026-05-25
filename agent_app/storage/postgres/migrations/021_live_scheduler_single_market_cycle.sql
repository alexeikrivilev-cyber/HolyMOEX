UPDATE audit.schedule_config
   SET enabled = false,
       schedule_payload = schedule_payload
         || jsonb_build_object(
              'disabled_reason', 'covered_by_schedule:live_autonomous:market_data:1m',
              'disabled_for_run_mode', 'live_trading'
            )
 WHERE schedule_config_id IN (
   'schedule:market_data_metrics:intraday',
   'schedule:liquidity_microstructure:realtime',
   'schedule:volatility_risk:realtime'
 );

INSERT INTO audit.audit_record (
    module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
    'Orchestration Module',
    'info',
    'live_scheduler_single_market_cycle_enabled',
    'Standalone market/liquidity/volatility realtime schedules were disabled because the live autonomous market-data schedule runs the full current-cycle feature-to-execution path.',
    'migration',
    '021_live_scheduler_single_market_cycle',
    ARRAY['duplicate_market_cycles_disabled', 'current_cycle_refs_required', 'live_autonomous_market_cycle_primary'],
    jsonb_build_object(
      'primary_schedule_config_id', 'schedule:live_autonomous:market_data:1m',
      'disabled_schedule_config_ids', jsonb_build_array(
        'schedule:market_data_metrics:intraday',
        'schedule:liquidity_microstructure:realtime',
        'schedule:volatility_risk:realtime'
      )
    )
);
