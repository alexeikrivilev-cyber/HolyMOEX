BEGIN;

INSERT INTO audit.module_dependency_graph (
  graph_id,
  version,
  status,
  graph_payload
) VALUES (
  'strict_default_dependency_graph',
  '1.0',
  'active',
  $json$
  {
    "schema_version": "1.0",
    "policy": {
      "full_recalculation_allowed_contours": ["research_contour"],
      "full_recalculation_allowed_run_modes": ["backtest", "replay"],
      "non_critical_failure_status": "degraded",
      "critical_failure_action": "block_downstream_jobs"
    },
    "edges": [
      {"source": "Selected Instruments DB", "target": "Data Intake & Routing Module", "critical": true},
      {"source": "Selected Instruments DB", "target": "Event & News Intelligence Module", "critical": false},
      {"source": "Selected Instruments DB", "target": "Earnings & Dividend Intelligence Module", "critical": false},
      {"source": "Selected Instruments DB", "target": "Corporate Actions Adjustment Module", "critical": true},
      {"source": "Raw Market Data Store", "target": "Data Quality Module", "critical": true},
      {"source": "Raw Market Data Store", "target": "Market Data Metrics Module", "critical": true},
      {"source": "Raw Market Data Store", "target": "Liquidity & Microstructure Module", "critical": true},
      {"source": "Raw Market Data Store", "target": "Volatility & Risk Metrics Module", "critical": true},
      {"source": "Raw Market Data Store", "target": "Derivatives & Positioning Module", "critical": false},
      {"source": "Raw Macro Data Store", "target": "Market Context Module", "critical": true},
      {"source": "Raw Text Store", "target": "Data Intake & Routing Module", "critical": false},
      {"source": "Raw Text Store", "target": "Event & News Intelligence Module", "critical": false},
      {"source": "Raw Text Store", "target": "Earnings & Dividend Intelligence Module", "critical": false},
      {"source": "Raw Text Store", "target": "Fundamental & Valuation Module", "critical": false},
      {"source": "Event Store", "target": "Event & News Intelligence Module", "critical": false},
      {"source": "Event Store", "target": "Earnings & Dividend Intelligence Module", "critical": false},
      {"source": "Event Store", "target": "Market Context Module", "critical": false},
      {"source": "Event Store", "target": "Corporate Actions Adjustment Module", "critical": true},
      {"source": "Feature Store", "target": "Data Quality Module", "critical": true},
      {"source": "Feature Store", "target": "Normalization & Feature Vector Module", "critical": true},
      {"source": "Feature Store", "target": "Decision Engine Module", "critical": true},
      {"source": "Feature Store", "target": "Risk Control Module", "critical": true},
      {"source": "Feature Store", "target": "Execution Engine Module", "critical": true},
      {"source": "Feature Store", "target": "Portfolio State Module", "critical": false},
      {"source": "Data Quality Module", "target": "Normalization & Feature Vector Module", "critical": true},
      {"source": "Normalization & Feature Vector Module", "target": "Decision Engine Module", "critical": true},
      {"source": "Decision Engine Module", "target": "Risk Control Module", "critical": true},
      {"source": "Risk Control Module", "target": "Execution Engine Module", "critical": true},
      {"source": "Execution Engine Module", "target": "Portfolio State Module", "critical": false},
      {"source": "Portfolio State Module", "target": "Monitoring & Audit Module", "critical": false},
      {"source": "Request Log Store", "target": "Monitoring & Audit Module", "critical": false},
      {"source": "Audit Log Store", "target": "Monitoring & Audit Module", "critical": false},
      {"source": "Order Store", "target": "Portfolio State Module", "critical": true},
      {"source": "Order Store", "target": "Monitoring & Audit Module", "critical": false}
    ]
  }
  $json$::jsonb
)
ON CONFLICT (graph_id) DO UPDATE SET
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  graph_payload = EXCLUDED.graph_payload;

INSERT INTO audit.schedule_config (
  schedule_config_id,
  module_name,
  contour,
  schedule_payload,
  enabled
) VALUES
  (
    'schedule:orchestration:service',
    'Orchestration Module',
    'service_contour',
    '{"trigger":"continuous","policy":"scheduler + event_runner + dependency_runner"}'::jsonb,
    true
  ),
  (
    'schedule:market_data_metrics:intraday',
    'Market Data Metrics Module',
    'realtime_contour',
    '{"frequency":"1m-5m","source":"Raw Market Data Store","run_mode":"paper_trading"}'::jsonb,
    true
  ),
  (
    'schedule:market_context:global',
    'Market Context Module',
    'global_contour',
    '{"frequency":"4h-8h","source":"Raw Macro Data Store","run_mode":"paper_trading"}'::jsonb,
    true
  ),
  (
    'schedule:monitoring:continuous',
    'Monitoring & Audit Module',
    'monitoring_contour',
    '{"trigger":"continuous","source":"Audit Log Store"}'::jsonb,
    true
  )
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
  'Orchestration Module',
  'info',
  'orchestration_seeded',
  'Strict default dependency graph and schedule config seeded',
  'module_dependency_graph',
  'strict_default_dependency_graph',
  ARRAY['module_dependency_graph_respected', 'idempotency_enforced'],
  '{"seed_version":"2026-05-21","source":"modules/01_orchestration_module.md"}'::jsonb
);

COMMIT;
