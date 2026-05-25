-- Keep live execution driven by the full market-data cycle and event refs.
-- The standalone 1m decision timer can build empty/latest-only vectors between
-- current cycles; that creates false block/reject noise and must not feed live
-- execution.
UPDATE audit.schedule_config
   SET enabled = false
 WHERE schedule_config_id = 'schedule:live_autonomous:decision:1m';
