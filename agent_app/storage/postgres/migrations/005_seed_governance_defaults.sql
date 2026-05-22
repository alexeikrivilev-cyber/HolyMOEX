BEGIN;

INSERT INTO request_logs.provider_config (
  provider,
  base_url_env,
  auth_header,
  auth_value_source,
  config_payload,
  enabled
) VALUES
  (
    'moex_iss',
    'MOEX_ISS_BASE_URL',
    NULL,
    NULL,
    '{
      "default_base_url": "https://iss.moex.com/iss",
      "allowed_request_types": ["market_data", "orderbook", "trades", "instruments"],
      "gateway_only": true,
      "cache_defaults": {"market_data": 60, "instruments": 86400}
    }'::jsonb,
    true
  ),
  (
    'moex_fast',
    'MOEX_FAST_BASE_URL',
    NULL,
    NULL,
    '{
      "enabled_by_default": false,
      "allowed_request_types": ["market_data", "orderbook", "trades"],
      "gateway_only": true,
      "requires_direct_market_data_contract": true
    }'::jsonb,
    false
  ),
  (
    'arena_go',
    'ARENA_GO_BASE_URL',
    'Authorization',
    'ARENA_GO_TOKEN',
    '{
      "default_base_url": "https://arenago.ru/api",
      "portfolio_env": "ARENA_GO_PORTFOLIO",
      "bot_name_env": "ARENA_GO_BOT_NAME",
      "daily_trade_limit_env": "ARENA_GO_DAILY_TRADE_LIMIT",
      "allowed_request_types": ["submit_order", "get_trades", "get_positions", "get_bots"],
      "quantity_mode_source": "registry.instrument_profile.arena_go_quantity_mode",
      "error_mapping": {
        "ERROR: MARKET CLOSED": "market_closed",
        "ERROR: NOT VALID SECID": "invalid_instrument",
        "ERROR: INSUFFICIENT CASH": "insufficient_cash",
        "HAS REACHED DAILY TRADE LIMIT": "daily_trade_limit_reached"
      }
    }'::jsonb,
    true
  ),
  (
    'polza_ai',
    'POLZA_BASE_URL',
    'Authorization',
    'POLZA_API_KEY',
    '{
      "default_base_url": "https://polza.ai/api/v1",
      "auth_scheme": "Bearer",
      "default_model_env": "POLZA_LLM_MODEL",
      "response_format": "json_object",
      "temperature": 0,
      "allowed_request_types": ["llm_completion"],
      "forbidden_uses": ["final_trading_decision", "order_submission", "weights_mutation", "risk_policy_mutation"]
    }'::jsonb,
    true
  ),
  (
    'news_api',
    'NEWS_API_BASE_URL',
    'Authorization',
    'NEWS_API_KEY',
    '{
      "enabled_by_default": false,
      "allowed_request_types": ["text_search", "text_fetch"],
      "gateway_only": true,
      "raw_store": "raw_text.raw_text_item"
    }'::jsonb,
    false
  ),
  (
    'issuer_disclosure',
    'ISSUER_DISCLOSURE_BASE_URL',
    NULL,
    NULL,
    '{
      "enabled_by_default": false,
      "allowed_request_types": ["text_search", "text_fetch"],
      "gateway_only": true,
      "raw_store": "raw_text.raw_text_item"
    }'::jsonb,
    false
  ),
  (
    'macro_api',
    'MACRO_API_BASE_URL',
    'Authorization',
    'MACRO_API_KEY',
    '{
      "enabled_by_default": false,
      "allowed_request_types": ["macro_series", "text_search", "text_fetch"],
      "gateway_only": true,
      "raw_store": "raw_macro.raw_macro_point"
    }'::jsonb,
    false
  ),
  (
    'broker_api',
    'BROKER_API_BASE_URL',
    'Authorization',
    'BROKER_API_TOKEN',
    '{
      "enabled_by_default": false,
      "allowed_request_types": ["orders", "portfolio"],
      "gateway_only": true,
      "preferred_provider_for_execution": "arena_go"
    }'::jsonb,
    false
  ),
  (
    'internal_cache',
    NULL,
    NULL,
    NULL,
    '{
      "allowed_request_types": ["market_data", "orderbook", "trades", "instruments", "text_search", "text_fetch", "macro_series"],
      "gateway_only": true
    }'::jsonb,
    true
  )
ON CONFLICT (provider) DO UPDATE SET
  base_url_env = EXCLUDED.base_url_env,
  auth_header = EXCLUDED.auth_header,
  auth_value_source = EXCLUDED.auth_value_source,
  config_payload = EXCLUDED.config_payload,
  enabled = EXCLUDED.enabled,
  updated_at = now();

INSERT INTO weights.weights_profile (
  weights_profile_id,
  profile_name,
  version,
  status,
  horizon,
  run_mode_allowed,
  approved_by,
  validation_report_ref
) VALUES
  (
    'weights:strict_default:intraday:v1',
    'strict_default',
    '1.0',
    'active',
    'intraday',
    ARRAY['analysis_only', 'paper_trading'],
    'manual_governance_seed',
    'manual_validation:strict_default_weights:v1'
  ),
  (
    'weights:strict_default:swing:v1',
    'strict_default',
    '1.0',
    'active',
    'swing',
    ARRAY['analysis_only', 'paper_trading'],
    'manual_governance_seed',
    'manual_validation:strict_default_weights:v1'
  ),
  (
    'weights:strict_default:position:v1',
    'strict_default',
    '1.0',
    'active',
    'position',
    ARRAY['analysis_only', 'paper_trading'],
    'manual_governance_seed',
    'manual_validation:strict_default_weights:v1'
  ),
  (
    'weights:live_trading:draft:v1',
    'live_trading_guarded',
    '1.0',
    'draft',
    'intraday',
    ARRAY['live_trading'],
    NULL,
    'manual_validation_required_before_activation'
  )
ON CONFLICT (weights_profile_id) DO UPDATE SET
  profile_name = EXCLUDED.profile_name,
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  horizon = EXCLUDED.horizon,
  run_mode_allowed = EXCLUDED.run_mode_allowed,
  approved_by = EXCLUDED.approved_by,
  validation_report_ref = EXCLUDED.validation_report_ref;

INSERT INTO weights.weights_profile (
  weights_profile_id,
  profile_name,
  version,
  status,
  horizon,
  run_mode_allowed,
  approved_by,
  validation_report_ref
) VALUES
  (
    'weights:liquidity_microstructure:intraday:v1',
    'liquidity_microstructure_component_weights',
    '1.0',
    'active',
    'intraday',
    ARRAY['analysis_only', 'paper_trading', 'live_trading'],
    'manual_governance_seed',
    'manual_validation:liquidity_microstructure_components:v1'
  )
ON CONFLICT (weights_profile_id) DO UPDATE SET
  profile_name = EXCLUDED.profile_name,
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  horizon = EXCLUDED.horizon,
  run_mode_allowed = EXCLUDED.run_mode_allowed,
  approved_by = EXCLUDED.approved_by,
  validation_report_ref = EXCLUDED.validation_report_ref;

WITH metric_rules(metric_weight_rule_id, weights_profile_id, metric_name, metric_group, horizon, weight, direction, transform, min_confidence_score, stale_policy) AS (
  VALUES
    ('weight:intraday:price_strength_score', 'weights:strict_default:intraday:v1', 'price_strength_score', 'price', 'intraday', 0.2200000000, 'positive', 'identity', 0.600000, 'downweight'),
    ('weight:intraday:liquidity_risk_score', 'weights:strict_default:intraday:v1', 'liquidity_risk_score', 'liquidity', 'intraday', 0.1800000000, 'negative', 'identity', 0.700000, 'block_decision'),
    ('weight:intraday:volatility_risk_score', 'weights:strict_default:intraday:v1', 'volatility_risk_score', 'volatility', 'intraday', 0.1400000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:intraday:event_materiality_score', 'weights:strict_default:intraday:v1', 'event_materiality_score', 'event', 'intraday', 0.1200000000, 'nonlinear', 'identity', 0.700000, 'downweight'),
    ('weight:intraday:event_sentiment_score', 'weights:strict_default:intraday:v1', 'event_sentiment_score', 'event', 'intraday', 0.1200000000, 'positive', 'identity', 0.700000, 'downweight'),
    ('weight:intraday:data_quality_score', 'weights:strict_default:intraday:v1', 'data_quality_score', 'data_quality', 'intraday', 0.1200000000, 'positive', 'identity', 0.800000, 'block_decision'),
    ('weight:intraday:risk_on_risk_off_score', 'weights:strict_default:intraday:v1', 'risk_on_risk_off_score', 'market_context', 'intraday', 0.1000000000, 'positive', 'identity', 0.650000, 'downweight'),

    ('weight:swing:price_strength_score', 'weights:strict_default:swing:v1', 'price_strength_score', 'price', 'swing', 0.2000000000, 'positive', 'identity', 0.600000, 'downweight'),
    ('weight:swing:volatility_risk_score', 'weights:strict_default:swing:v1', 'volatility_risk_score', 'volatility', 'swing', 0.1600000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:swing:liquidity_risk_score', 'weights:strict_default:swing:v1', 'liquidity_risk_score', 'liquidity', 'swing', 0.1200000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:swing:event_materiality_score', 'weights:strict_default:swing:v1', 'event_materiality_score', 'event', 'swing', 0.1200000000, 'nonlinear', 'identity', 0.700000, 'downweight'),
    ('weight:swing:event_sentiment_score', 'weights:strict_default:swing:v1', 'event_sentiment_score', 'event', 'swing', 0.1200000000, 'positive', 'identity', 0.700000, 'downweight'),
    ('weight:swing:fundamental_quality_score', 'weights:strict_default:swing:v1', 'fundamental_quality_score', 'fundamental', 'swing', 0.1000000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:swing:valuation_attractiveness_score', 'weights:strict_default:swing:v1', 'valuation_attractiveness_score', 'fundamental', 'swing', 0.0800000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:swing:macro_pressure_score', 'weights:strict_default:swing:v1', 'macro_pressure_score', 'market_context', 'swing', 0.0600000000, 'nonlinear', 'identity', 0.650000, 'downweight'),
    ('weight:swing:data_quality_score', 'weights:strict_default:swing:v1', 'data_quality_score', 'data_quality', 'swing', 0.0400000000, 'positive', 'identity', 0.800000, 'block_decision'),

    ('weight:position:fundamental_quality_score', 'weights:strict_default:position:v1', 'fundamental_quality_score', 'fundamental', 'position', 0.2200000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:position:valuation_attractiveness_score', 'weights:strict_default:position:v1', 'valuation_attractiveness_score', 'fundamental', 'position', 0.2000000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:position:dividend_carry_score', 'weights:strict_default:position:v1', 'dividend_carry_score', 'dividend', 'position', 0.1400000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:position:macro_pressure_score', 'weights:strict_default:position:v1', 'macro_pressure_score', 'market_context', 'position', 0.1200000000, 'nonlinear', 'identity', 0.650000, 'downweight'),
    ('weight:position:volatility_risk_score', 'weights:strict_default:position:v1', 'volatility_risk_score', 'volatility', 'position', 0.1000000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:position:liquidity_risk_score', 'weights:strict_default:position:v1', 'liquidity_risk_score', 'liquidity', 'position', 0.0800000000, 'negative', 'identity', 0.650000, 'downweight'),
    ('weight:position:risk_on_risk_off_score', 'weights:strict_default:position:v1', 'risk_on_risk_off_score', 'market_context', 'position', 0.0800000000, 'positive', 'identity', 0.650000, 'downweight'),
    ('weight:position:data_quality_score', 'weights:strict_default:position:v1', 'data_quality_score', 'data_quality', 'position', 0.0600000000, 'positive', 'identity', 0.800000, 'block_decision')
)
INSERT INTO weights.metric_weight_rule (
  metric_weight_rule_id,
  weights_profile_id,
  metric_name,
  metric_group,
  horizon,
  instrument_scope,
  instrument_ids,
  sector,
  weight,
  direction,
  transform,
  min_confidence_score,
  stale_policy,
  calculation_version
)
SELECT
  metric_weight_rule_id,
  weights_profile_id,
  metric_name,
  metric_group,
  horizon,
  'all',
  ARRAY[]::TEXT[],
  NULL,
  weight,
  direction,
  transform,
  min_confidence_score,
  stale_policy,
  'metric_weights_seed_v1'
FROM metric_rules
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weights_profile_id = EXCLUDED.weights_profile_id,
  metric_name = EXCLUDED.metric_name,
  metric_group = EXCLUDED.metric_group,
  horizon = EXCLUDED.horizon,
  instrument_scope = EXCLUDED.instrument_scope,
  instrument_ids = EXCLUDED.instrument_ids,
  sector = EXCLUDED.sector,
  weight = EXCLUDED.weight,
  direction = EXCLUDED.direction,
  transform = EXCLUDED.transform,
  min_confidence_score = EXCLUDED.min_confidence_score,
  stale_policy = EXCLUDED.stale_policy,
  calculation_version = EXCLUDED.calculation_version;

WITH liquidity_metric_rules(metric_weight_rule_id, metric_name, weight, direction, transform, stale_policy) AS (
  VALUES
    ('weight:liquidity:pressure:order_book_imbalance', 'order_book_imbalance', 0.3400000000, 'positive', 'identity', 'downweight'),
    ('weight:liquidity:pressure:trade_imbalance', 'trade_imbalance', 0.3300000000, 'positive', 'identity', 'downweight'),
    ('weight:liquidity:pressure:order_flow_imbalance', 'order_flow_imbalance', 0.3300000000, 'positive', 'identity', 'downweight'),
    ('weight:liquidity:risk:spread_percentile', 'spread_percentile', 0.2500000000, 'positive', 'identity', 'downweight'),
    ('weight:liquidity:risk:estimated_slippage_1m', 'estimated_slippage_1m', 0.2500000000, 'positive', 'identity', 'block_decision'),
    ('weight:liquidity:risk:amihud_illiquidity', 'amihud_illiquidity', 0.2500000000, 'positive', 'identity', 'downweight'),
    ('weight:liquidity:risk:order_book_depth_30bps_z', 'order_book_depth_30bps_z', 0.2500000000, 'negative', 'identity', 'downweight')
)
INSERT INTO weights.metric_weight_rule (
  metric_weight_rule_id,
  weights_profile_id,
  metric_name,
  metric_group,
  horizon,
  instrument_scope,
  instrument_ids,
  sector,
  weight,
  direction,
  transform,
  min_confidence_score,
  stale_policy,
  calculation_version
)
SELECT
  metric_weight_rule_id,
  'weights:liquidity_microstructure:intraday:v1',
  metric_name,
  'liquidity',
  'intraday',
  'all',
  ARRAY[]::TEXT[],
  NULL,
  weight,
  direction,
  transform,
  0.700000,
  stale_policy,
  'liquidity_microstructure_v1'
FROM liquidity_metric_rules
ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
  weights_profile_id = EXCLUDED.weights_profile_id,
  metric_name = EXCLUDED.metric_name,
  metric_group = EXCLUDED.metric_group,
  horizon = EXCLUDED.horizon,
  instrument_scope = EXCLUDED.instrument_scope,
  instrument_ids = EXCLUDED.instrument_ids,
  sector = EXCLUDED.sector,
  weight = EXCLUDED.weight,
  direction = EXCLUDED.direction,
  transform = EXCLUDED.transform,
  min_confidence_score = EXCLUDED.min_confidence_score,
  stale_policy = EXCLUDED.stale_policy,
  calculation_version = EXCLUDED.calculation_version;

INSERT INTO risk.risk_policy (
  risk_policy_id,
  policy_name,
  version,
  status,
  run_mode_allowed,
  rules,
  approved_by
) VALUES
  (
    'risk_policy:paper_trading:v1',
    'paper_trading_guardrails',
    '1.0',
    'active',
    ARRAY['analysis_only', 'paper_trading'],
    '{
      "global_kill_switch": false,
      "execution_kill_switch": false,
      "max_portfolio_gross_exposure_pct": 0.80,
      "max_portfolio_net_exposure_pct": 0.80,
      "max_instrument_position_pct": 0.05,
      "max_sector_exposure_pct": 0.25,
      "max_order_value_rub": 50000,
      "max_daily_loss_pct": 0.02,
      "max_drawdown_pct": 0.05,
      "min_data_quality_score": 0.80,
      "max_spread_bps": 80,
      "max_slippage_bps": 50,
      "stale_portfolio_max_age_seconds": 300,
      "stale_feature_policy": "block_decision",
      "market_session_required": "open",
      "arena_go_daily_trade_limit_env": "ARENA_GO_DAILY_TRADE_LIMIT"
    }'::jsonb,
    'manual_governance_seed'
  ),
  (
    'risk_policy:live_trading:draft:v1',
    'live_trading_guardrails',
    '1.0',
    'draft',
    ARRAY['live_trading'],
    '{
      "global_kill_switch": true,
      "execution_kill_switch": true,
      "max_portfolio_gross_exposure_pct": 0.30,
      "max_portfolio_net_exposure_pct": 0.30,
      "max_instrument_position_pct": 0.03,
      "max_sector_exposure_pct": 0.15,
      "max_order_value_rub": 25000,
      "max_daily_loss_pct": 0.01,
      "max_drawdown_pct": 0.03,
      "min_data_quality_score": 0.90,
      "max_spread_bps": 50,
      "max_slippage_bps": 30,
      "requires_manual_activation": true
    }'::jsonb,
    NULL
  )
ON CONFLICT (risk_policy_id) DO UPDATE SET
  policy_name = EXCLUDED.policy_name,
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  run_mode_allowed = EXCLUDED.run_mode_allowed,
  rules = EXCLUDED.rules,
  approved_by = EXCLUDED.approved_by;

INSERT INTO risk.portfolio_limit (
  risk_policy_id,
  limit_name,
  limit_value,
  payload
) VALUES
  ('risk_policy:paper_trading:v1', 'max_portfolio_gross_exposure_pct', 0.8000000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:paper_trading:v1', 'max_portfolio_net_exposure_pct', 0.8000000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:paper_trading:v1', 'max_sector_exposure_pct', 0.2500000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:paper_trading:v1', 'max_daily_loss_pct', 0.0200000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:paper_trading:v1', 'max_drawdown_pct', 0.0500000000, '{"unit":"ratio"}'::jsonb),
  ('risk_policy:paper_trading:v1', 'min_data_quality_score', 0.8000000000, '{"unit":"score"}'::jsonb),
  ('risk_policy:paper_trading:v1', 'arena_go_daily_trade_limit', 1000, '{"unit":"orders","env":"ARENA_GO_DAILY_TRADE_LIMIT"}'::jsonb)
ON CONFLICT (risk_policy_id, limit_name) DO UPDATE SET
  limit_value = EXCLUDED.limit_value,
  payload = EXCLUDED.payload;

INSERT INTO risk.instrument_limit (
  risk_policy_id,
  instrument_id,
  max_position_pct,
  max_order_value_rub,
  max_slippage_bps,
  payload
)
SELECT
  'risk_policy:paper_trading:v1',
  instrument_id,
  0.050000,
  50000,
  50,
  jsonb_build_object(
    'sector', sector,
    'ticker', ticker,
    'max_trade_quantity', max_trade_quantity,
    'execution_enabled', execution_enabled,
    'arena_go_secid', arena_go_secid
  )
FROM registry.instrument_profile
WHERE universe_id = 'moex_top20_manual'
  AND is_active = true
ON CONFLICT (risk_policy_id, instrument_id) DO UPDATE SET
  max_position_pct = EXCLUDED.max_position_pct,
  max_order_value_rub = EXCLUDED.max_order_value_rub,
  max_slippage_bps = EXCLUDED.max_slippage_bps,
  payload = EXCLUDED.payload;

INSERT INTO audit.schedule_config (
  schedule_config_id,
  module_name,
  contour,
  schedule_payload,
  enabled
) VALUES
  ('schedule:external_gateway:service', 'External Request Gateway Module', 'service_contour', '{"trigger":"continuous","policy":"gateway request processing + cache maintenance"}'::jsonb, true),
  ('schedule:selected_instruments:metadata_refresh', 'Selected Instruments Registry Module', 'daily_contour', '{"trigger":"scheduled","frequency":"daily_after_market_close","request_type":"instruments","provider":"moex_iss"}'::jsonb, true),
  ('schedule:data_quality:dependency', 'Data Quality Module', 'service_contour', '{"trigger":"dependency","sources":["Raw Market Data Store","Raw Text Store","Feature Store","Portfolio State Store"]}'::jsonb, true),
  ('schedule:liquidity_microstructure:realtime', 'Liquidity & Microstructure Module', 'realtime_contour', '{"frequency":"1m-5m","source":"Raw Market Data Store"}'::jsonb, true),
  ('schedule:volatility_risk:realtime', 'Volatility & Risk Metrics Module', 'realtime_contour', '{"frequency":"1m-5m","source":"Raw Market Data Store"}'::jsonb, true),
  ('schedule:fundamental_valuation:daily', 'Fundamental & Valuation Module', 'daily_contour', '{"frequency":"after_market_close","sources":["Raw Text Store","Event Store","Raw Market Data Store"]}'::jsonb, true),
  ('schedule:event_news:intake', 'Event & News Intelligence Module', 'intraday_contour', '{"frequency":"15m-60m","source":"Raw Text Store","llm_provider":"polza_ai"}'::jsonb, true),
  ('schedule:earnings_dividend:event', 'Earnings & Dividend Intelligence Module', 'event_contour', '{"trigger":"on_event","sources":["Raw Text Store","Event Store"]}'::jsonb, true),
  ('schedule:corporate_actions:event', 'Corporate Actions Adjustment Module', 'event_contour', '{"trigger":"on_event","source":"Event Store"}'::jsonb, true),
  ('schedule:derivatives_positioning:daily', 'Derivatives & Positioning Module', 'daily_contour', '{"frequency":"after_market_close","source":"Raw Market Data Store"}'::jsonb, true),
  ('schedule:normalization:decision', 'Normalization & Feature Vector Module', 'decision_contour', '{"trigger":"on_feature_update","source":"Feature Store"}'::jsonb, true),
  ('schedule:decision_engine:on_feature_update', 'Decision Engine Module', 'decision_contour', '{"trigger":"on_feature_vector_update","weights_profiles":["weights:strict_default:intraday:v1","weights:strict_default:swing:v1","weights:strict_default:position:v1"]}'::jsonb, true),
  ('schedule:risk_control:on_decision_set', 'Risk Control Module', 'decision_contour', '{"trigger":"on_decision_set","risk_policy_id":"risk_policy:paper_trading:v1"}'::jsonb, true),
  ('schedule:execution:on_approved_order', 'Execution Engine Module', 'execution_contour', '{"trigger":"on_approved_order","provider":"arena_go","run_modes":["paper_trading","live_trading"]}'::jsonb, true),
  ('schedule:portfolio_state:after_execution', 'Portfolio State Module', 'execution_contour', '{"trigger":"after_execution_or_scheduled_refresh","provider":"arena_go"}'::jsonb, true),
  ('schedule:feature_validation:research', 'Feature Validation & Research Module', 'research_contour', '{"trigger":"manual_or_batch","auto_activate_weights":false}'::jsonb, false),
  ('schedule:backtesting:research', 'Backtesting & Paper Trading Module', 'research_contour', '{"trigger":"manual_or_batch","writes_live_orders":false}'::jsonb, false)
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
  'Monitoring & Audit Module',
  'info',
  'governance_defaults_seeded',
  'Provider configs, active paper-trading metric weights, risk policy, limits and schedules seeded',
  'database_seed',
  '005_seed_governance_defaults',
  ARRAY['weights_loaded_from_db', 'risk_policy_seeded', 'gateway_provider_config_seeded', 'schedule_config_seeded'],
  '{"seed_version":"2026-05-21","run_mode":"paper_trading"}'::jsonb
);

COMMIT;
