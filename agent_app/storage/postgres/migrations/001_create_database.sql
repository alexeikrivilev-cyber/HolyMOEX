BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS raw_market;
CREATE SCHEMA IF NOT EXISTS raw_text;
CREATE SCHEMA IF NOT EXISTS raw_macro;
CREATE SCHEMA IF NOT EXISTS events;
CREATE SCHEMA IF NOT EXISTS features;
CREATE SCHEMA IF NOT EXISTS weights;
CREATE SCHEMA IF NOT EXISTS risk;
CREATE SCHEMA IF NOT EXISTS portfolio;
CREATE SCHEMA IF NOT EXISTS decisions;
CREATE SCHEMA IF NOT EXISTS orders;
CREATE SCHEMA IF NOT EXISTS request_logs;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS registry.instrument_universe (
  universe_id TEXT PRIMARY KEY,
  universe_name TEXT NOT NULL,
  max_active_instruments INTEGER NOT NULL DEFAULT 20 CHECK (max_active_instruments > 0),
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS registry.instrument_profile (
  instrument_id TEXT PRIMARY KEY,
  universe_id TEXT NOT NULL REFERENCES registry.instrument_universe(universe_id),
  ticker TEXT NOT NULL,
  figi TEXT,
  isin TEXT,
  class_code TEXT,
  board_id TEXT,
  lot_size INTEGER CHECK (lot_size IS NULL OR lot_size > 0),
  min_price_increment NUMERIC(20, 8),
  currency TEXT NOT NULL DEFAULT 'RUB',
  sector TEXT,
  issuer_name TEXT,
  aliases TEXT[] NOT NULL DEFAULT '{}',
  related_entities TEXT[] NOT NULL DEFAULT '{}',
  is_active BOOLEAN NOT NULL DEFAULT true,
  tradable BOOLEAN NOT NULL DEFAULT true,
  allowed_horizons TEXT[] NOT NULL DEFAULT ARRAY['intraday', 'swing', 'position'],
  arena_go_secid TEXT,
  arena_go_quantity_mode TEXT NOT NULL DEFAULT 'shares'
    CHECK (arena_go_quantity_mode IN ('shares', 'lots')),
  max_trade_quantity INTEGER CHECK (max_trade_quantity IS NULL OR max_trade_quantity >= 0),
  execution_enabled BOOLEAN NOT NULL DEFAULT true,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (universe_id, ticker),
  UNIQUE (universe_id, arena_go_secid)
);

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

CREATE TABLE IF NOT EXISTS registry.instrument_alias (
  instrument_alias_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT NOT NULL REFERENCES registry.instrument_profile(instrument_id),
  alias TEXT NOT NULL,
  alias_type TEXT NOT NULL DEFAULT 'ticker',
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (instrument_id, alias, alias_type)
);

CREATE TABLE IF NOT EXISTS registry.instrument_mapping (
  instrument_mapping_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT NOT NULL REFERENCES registry.instrument_profile(instrument_id),
  provider TEXT NOT NULL,
  provider_symbol TEXT NOT NULL,
  provider_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (instrument_id, provider)
);

CREATE OR REPLACE FUNCTION registry.enforce_active_instrument_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  active_count INTEGER;
  active_limit INTEGER;
BEGIN
  IF NEW.is_active THEN
    SELECT max_active_instruments
      INTO active_limit
      FROM registry.instrument_universe
     WHERE universe_id = NEW.universe_id;

    SELECT count(*)
      INTO active_count
      FROM registry.instrument_profile
     WHERE universe_id = NEW.universe_id
       AND is_active = true
       AND instrument_id <> NEW.instrument_id;

    IF active_count >= active_limit THEN
      RAISE EXCEPTION 'active instrument limit exceeded for universe %', NEW.universe_id;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_active_instrument_limit ON registry.instrument_profile;
CREATE TRIGGER trg_enforce_active_instrument_limit
BEFORE INSERT OR UPDATE OF is_active, universe_id
ON registry.instrument_profile
FOR EACH ROW
EXECUTE FUNCTION registry.enforce_active_instrument_limit();

CREATE TABLE IF NOT EXISTS raw_market.raw_candle (
  raw_candle_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  universe_id TEXT,
  board_id TEXT,
  timeframe TEXT NOT NULL,
  open_ts TIMESTAMPTZ NOT NULL,
  close_ts TIMESTAMPTZ,
  open_price NUMERIC(24, 10),
  high_price NUMERIC(24, 10),
  low_price NUMERIC(24, 10),
  close_price NUMERIC(24, 10),
  volume NUMERIC(24, 6),
  turnover NUMERIC(24, 6),
  provider TEXT NOT NULL,
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_market.raw_trade (
  raw_trade_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  universe_id TEXT,
  trade_ts TIMESTAMPTZ NOT NULL,
  price NUMERIC(24, 10),
  quantity NUMERIC(24, 6),
  side TEXT,
  trade_value NUMERIC(24, 6),
  provider TEXT NOT NULL,
  provider_trade_id TEXT,
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_market.raw_orderbook (
  raw_orderbook_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  universe_id TEXT,
  snapshot_ts TIMESTAMPTZ NOT NULL,
  bids JSONB NOT NULL DEFAULT '[]'::jsonb,
  asks JSONB NOT NULL DEFAULT '[]'::jsonb,
  provider TEXT NOT NULL,
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_market.raw_index_value (
  raw_index_value_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  index_id TEXT NOT NULL,
  value_ts TIMESTAMPTZ NOT NULL,
  value NUMERIC(24, 10),
  provider TEXT NOT NULL,
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_market.execution_constraint (
  execution_constraint_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  universe_id TEXT,
  as_of_ts TIMESTAMPTZ NOT NULL,
  spread_bps NUMERIC(18, 8),
  estimated_slippage_bps NUMERIC(18, 8),
  max_order_value_rub NUMERIC(24, 6),
  constraint_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_text.raw_text_item (
  raw_text_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  universe_id TEXT,
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  source TEXT NOT NULL,
  source_url TEXT,
  title TEXT,
  body TEXT,
  language TEXT,
  published_at TIMESTAMPTZ,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash TEXT,
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (content_hash)
);

CREATE TABLE IF NOT EXISTS raw_text.event_routing_message (
  routing_message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  raw_text_item_id UUID REFERENCES raw_text.raw_text_item(raw_text_item_id),
  target_module TEXT NOT NULL,
  universe_id TEXT,
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  routing_reason TEXT,
  routing_ttl_seconds INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_macro.raw_macro_point (
  raw_macro_point_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  series_id TEXT NOT NULL,
  point_ts TIMESTAMPTZ NOT NULL,
  value NUMERIC(24, 10),
  unit TEXT,
  provider TEXT NOT NULL,
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (series_id, point_ts, provider)
);

CREATE TABLE IF NOT EXISTS events.structured_event (
  event_id TEXT PRIMARY KEY,
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  event_type TEXT NOT NULL,
  event_subtype TEXT,
  event_ts TIMESTAMPTZ NOT NULL,
  detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_refs TEXT[] NOT NULL DEFAULT '{}',
  relevance_score NUMERIC(8, 6),
  materiality_score NUMERIC(8, 6),
  novelty_score NUMERIC(8, 6),
  surprise_score NUMERIC(8, 6),
  sentiment_score NUMERIC(8, 6),
  confidence_score NUMERIC(8, 6),
  evidence TEXT[] NOT NULL DEFAULT '{}',
  reason_codes TEXT[] NOT NULL DEFAULT '{}',
  model_version TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS events.event_cluster (
  event_cluster_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  universe_id TEXT,
  cluster_key TEXT NOT NULL,
  event_ids TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events.event_reaction (
  event_reaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id TEXT REFERENCES events.structured_event(event_id),
  instrument_id TEXT,
  horizon TEXT NOT NULL,
  reaction_return NUMERIC(18, 8),
  market_adjusted_return NUMERIC(18, 8),
  abnormal_volume NUMERIC(18, 8),
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events.earnings_dividend_record (
  earnings_dividend_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  event_id TEXT REFERENCES events.structured_event(event_id),
  record_type TEXT NOT NULL,
  as_of_ts TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events.corporate_action_record (
  corporate_action_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  event_id TEXT REFERENCES events.structured_event(event_id),
  action_type TEXT NOT NULL,
  effective_date DATE,
  adjustment_factor NUMERIC(24, 12),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features.feature_record (
  feature_id TEXT PRIMARY KEY,
  instrument_id TEXT,
  metric_name TEXT NOT NULL,
  metric_group TEXT NOT NULL,
  metric_type TEXT NOT NULL,
  raw_value NUMERIC(24, 10),
  normalized_value NUMERIC(24, 10),
  unit TEXT,
  horizon TEXT NOT NULL,
  contour TEXT NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  ttl_seconds INTEGER,
  confidence_score NUMERIC(8, 6),
  source_module TEXT NOT NULL,
  source_refs TEXT[] NOT NULL DEFAULT '{}',
  calculation_version TEXT NOT NULL,
  quality_flags TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS features.feature_vector (
  feature_vector_id TEXT PRIMARY KEY,
  instrument_id TEXT,
  horizon TEXT NOT NULL,
  as_of_ts TIMESTAMPTZ NOT NULL,
  features JSONB NOT NULL DEFAULT '{}'::jsonb,
  coverage_ratio NUMERIC(8, 6),
  data_quality_score NUMERIC(8, 6),
  build_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS features.data_quality_record (
  data_quality_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  object_type TEXT NOT NULL,
  object_ref TEXT NOT NULL,
  quality_score NUMERIC(8, 6),
  quality_flags TEXT[] NOT NULL DEFAULT '{}',
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS features.fundamental_snapshot (
  fundamental_snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  as_of_date DATE NOT NULL,
  source_refs TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features.market_state_record (
  market_state_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  universe_id TEXT,
  as_of_ts TIMESTAMPTZ NOT NULL,
  market_session_status TEXT,
  market_regime TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_market.derivatives_availability (
  derivatives_availability_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id TEXT,
  as_of_ts TIMESTAMPTZ NOT NULL,
  has_liquid_futures BOOLEAN NOT NULL DEFAULT false,
  has_liquid_options BOOLEAN NOT NULL DEFAULT false,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weights.weights_profile (
  weights_profile_id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'deprecated', 'archived')),
  horizon TEXT NOT NULL,
  run_mode_allowed TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_by TEXT,
  validation_report_ref TEXT,
  UNIQUE (profile_name, version, horizon)
);

CREATE TABLE IF NOT EXISTS weights.metric_weight_rule (
  metric_weight_rule_id TEXT PRIMARY KEY,
  weights_profile_id TEXT NOT NULL REFERENCES weights.weights_profile(weights_profile_id),
  metric_name TEXT NOT NULL,
  metric_group TEXT NOT NULL,
  horizon TEXT NOT NULL,
  instrument_scope TEXT NOT NULL CHECK (instrument_scope IN ('all', 'sector', 'instrument')),
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  sector TEXT,
  weight NUMERIC(18, 10) NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('positive', 'negative', 'nonlinear')),
  transform TEXT NOT NULL,
  min_confidence_score NUMERIC(8, 6),
  stale_policy TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk.risk_policy (
  risk_policy_id TEXT PRIMARY KEY,
  policy_name TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  run_mode_allowed TEXT[] NOT NULL DEFAULT '{}',
  rules JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_by TEXT
);

CREATE TABLE IF NOT EXISTS risk.instrument_limit (
  instrument_limit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  risk_policy_id TEXT NOT NULL REFERENCES risk.risk_policy(risk_policy_id),
  instrument_id TEXT NOT NULL,
  max_position_pct NUMERIC(10, 6),
  max_order_value_rub NUMERIC(24, 6),
  max_slippage_bps NUMERIC(18, 8),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (risk_policy_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS risk.portfolio_limit (
  portfolio_limit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  risk_policy_id TEXT NOT NULL REFERENCES risk.risk_policy(risk_policy_id),
  limit_name TEXT NOT NULL,
  limit_value NUMERIC(24, 10),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (risk_policy_id, limit_name)
);

CREATE TABLE IF NOT EXISTS risk.risk_check_result (
  risk_check_id TEXT PRIMARY KEY,
  decision_set_id TEXT NOT NULL,
  status TEXT NOT NULL,
  approved_order_intents TEXT[] NOT NULL DEFAULT '{}',
  rejected_decisions TEXT[] NOT NULL DEFAULT '{}',
  risk_flags TEXT[] NOT NULL DEFAULT '{}',
  adjustments JSONB NOT NULL DEFAULT '[]'::jsonb,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS risk.risk_context_record (
  risk_context_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  universe_id TEXT,
  instrument_id TEXT,
  as_of_ts TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_module TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio.portfolio_snapshot (
  portfolio_snapshot_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  universe_id TEXT,
  as_of_ts TIMESTAMPTZ NOT NULL,
  initial_capital_rub NUMERIC(24, 6),
  cash NUMERIC(24, 6),
  equity NUMERIC(24, 6),
  gross_exposure NUMERIC(24, 6),
  net_exposure NUMERIC(24, 6),
  realized_pnl NUMERIC(24, 6),
  unrealized_pnl NUMERIC(24, 6),
  source_module TEXT NOT NULL,
  source_refs TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio.position_state (
  position_state_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  as_of_ts TIMESTAMPTZ NOT NULL,
  quantity NUMERIC(24, 6) NOT NULL DEFAULT 0,
  average_price NUMERIC(24, 10),
  market_price NUMERIC(24, 10),
  market_value NUMERIC(24, 6),
  unrealized_pnl NUMERIC(24, 6),
  source_module TEXT NOT NULL,
  source_refs TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (portfolio_id, instrument_id, as_of_ts)
);

CREATE TABLE IF NOT EXISTS decisions.decision_request (
  decision_request_id TEXT PRIMARY KEY,
  universe_id TEXT NOT NULL,
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  horizon TEXT NOT NULL,
  as_of_ts TIMESTAMPTZ NOT NULL,
  feature_vector_refs TEXT[] NOT NULL DEFAULT '{}',
  portfolio_state_ref TEXT,
  weights_profile_id TEXT,
  run_mode TEXT NOT NULL,
  decision_mode TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS decisions.decision_set (
  decision_set_id TEXT PRIMARY KEY,
  decision_request_id TEXT REFERENCES decisions.decision_request(decision_request_id),
  horizon TEXT NOT NULL,
  decisions JSONB NOT NULL DEFAULT '[]'::jsonb,
  calculation_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decisions.decision_record (
  decision_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  decision_set_id TEXT REFERENCES decisions.decision_set(decision_set_id),
  instrument_id TEXT,
  action TEXT NOT NULL,
  target_position_pct NUMERIC(10, 6),
  target_quantity NUMERIC(24, 6),
  confidence_score NUMERIC(8, 6),
  expected_edge_score NUMERIC(18, 10),
  risk_score NUMERIC(18, 10),
  primary_reason_codes TEXT[] NOT NULL DEFAULT '{}',
  feature_contributions JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decisions.decision_explanation (
  decision_explanation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  decision_record_id UUID REFERENCES decisions.decision_record(decision_record_id),
  explanation JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders.order_intent (
  order_intent_id TEXT PRIMARY KEY,
  instrument_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity NUMERIC(24, 6) NOT NULL,
  order_type TEXT NOT NULL,
  limit_price NUMERIC(24, 10),
  time_in_force TEXT NOT NULL,
  max_slippage_bps NUMERIC(18, 8),
  execution_ttl_seconds INTEGER,
  decision_set_id TEXT,
  risk_check_id TEXT,
  run_mode TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS orders.order_status (
  order_status_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_intent_id TEXT NOT NULL REFERENCES orders.order_intent(order_intent_id),
  status TEXT NOT NULL,
  status_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  provider TEXT,
  provider_order_id TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS orders.fill_report (
  fill_report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_intent_id TEXT NOT NULL REFERENCES orders.order_intent(order_intent_id),
  provider_fill_id TEXT,
  fill_ts TIMESTAMPTZ NOT NULL,
  filled_quantity NUMERIC(24, 6),
  fill_price NUMERIC(24, 10),
  fees NUMERIC(24, 6),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS orders.execution_result (
  execution_result_id TEXT PRIMARY KEY,
  order_intent_id TEXT NOT NULL,
  status TEXT NOT NULL,
  broker_order_id TEXT,
  submitted_at TIMESTAMPTZ,
  last_update_at TIMESTAMPTZ,
  filled_quantity NUMERIC(24, 6),
  avg_fill_price NUMERIC(24, 10),
  fees NUMERIC(24, 6),
  slippage_bps NUMERIC(18, 8),
  errors TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS request_logs.external_request (
  request_id TEXT PRIMARY KEY,
  caller_module TEXT NOT NULL,
  provider TEXT NOT NULL,
  request_type TEXT NOT NULL,
  universe_id TEXT,
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  cache_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  timeout_ms INTEGER,
  retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS request_logs.external_response (
  request_id TEXT PRIMARY KEY REFERENCES request_logs.external_request(request_id),
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  data_ref TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  latency_ms INTEGER,
  cost_units NUMERIC(18, 8),
  cache_hit BOOLEAN NOT NULL DEFAULT false,
  warnings TEXT[] NOT NULL DEFAULT '{}',
  errors TEXT[] NOT NULL DEFAULT '{}',
  provider_tracking_id TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS request_logs.external_request_log (
  external_request_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id TEXT,
  caller_module TEXT NOT NULL,
  provider TEXT NOT NULL,
  request_type TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  cost_units NUMERIC(18, 8),
  cache_hit BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS request_logs.provider_config (
  provider TEXT PRIMARY KEY,
  base_url_env TEXT,
  auth_header TEXT,
  auth_value_source TEXT,
  config_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS request_logs.request_cache (
  cache_key TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  request_type TEXT NOT NULL,
  data_ref TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.audit_record (
  audit_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  module_name TEXT,
  job_id TEXT,
  severity TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT,
  object_type TEXT,
  object_ref TEXT,
  reason_codes TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS audit.module_job (
  job_id TEXT PRIMARY KEY,
  module_name TEXT NOT NULL,
  contour TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  universe_id TEXT,
  instrument_ids TEXT[] NOT NULL DEFAULT '{}',
  horizons TEXT[] NOT NULL DEFAULT '{}',
  time_range JSONB NOT NULL DEFAULT '{}'::jsonb,
  input_refs TEXT[] NOT NULL DEFAULT '{}',
  config_ref TEXT,
  run_mode TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'normal',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS audit.module_job_result (
  job_id TEXT PRIMARY KEY,
  module_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  output_refs TEXT[] NOT NULL DEFAULT '{}',
  warnings TEXT[] NOT NULL DEFAULT '{}',
  errors TEXT[] NOT NULL DEFAULT '{}',
  metrics_written INTEGER NOT NULL DEFAULT 0,
  events_written INTEGER NOT NULL DEFAULT 0,
  data_quality_score NUMERIC(8, 6),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS audit.module_dependency_graph (
  graph_id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  graph_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.schedule_config (
  schedule_config_id TEXT PRIMARY KEY,
  module_name TEXT NOT NULL,
  contour TEXT NOT NULL,
  schedule_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.module_run (
  module_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id TEXT,
  module_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS audit.research_report (
  research_report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  report_type TEXT NOT NULL,
  universe_id TEXT,
  horizon TEXT,
  as_of_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.monitoring_record (
  monitoring_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  check_name TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'info',
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_instrument_profile_universe_active
  ON registry.instrument_profile (universe_id, is_active);
CREATE INDEX IF NOT EXISTS idx_universe_snapshot_lookup
  ON registry.universe_snapshot (universe_id, validation_status, created_at);
CREATE INDEX IF NOT EXISTS idx_raw_candle_lookup
  ON raw_market.raw_candle (instrument_id, timeframe, open_ts);
CREATE INDEX IF NOT EXISTS idx_raw_trade_lookup
  ON raw_market.raw_trade (instrument_id, trade_ts);
CREATE INDEX IF NOT EXISTS idx_raw_text_item_published_at
  ON raw_text.raw_text_item (published_at);
CREATE INDEX IF NOT EXISTS idx_structured_event_ts
  ON events.structured_event (event_ts);
CREATE INDEX IF NOT EXISTS idx_feature_record_lookup
  ON features.feature_record (instrument_id, metric_name, horizon, timestamp);
CREATE INDEX IF NOT EXISTS idx_feature_vector_lookup
  ON features.feature_vector (instrument_id, horizon, as_of_ts);
CREATE INDEX IF NOT EXISTS idx_position_state_lookup
  ON portfolio.position_state (portfolio_id, instrument_id, as_of_ts);
CREATE INDEX IF NOT EXISTS idx_external_request_log_provider
  ON request_logs.external_request_log (provider, request_type, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_record_created_at
  ON audit.audit_record (created_at);

COMMIT;
