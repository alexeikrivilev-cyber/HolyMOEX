BEGIN;

WITH new_edges(edge_payload) AS (
  VALUES
    ('{"source":"Market Data Metrics Module","target":"Feature Store","critical":true}'::jsonb),
    ('{"source":"Liquidity & Microstructure Module","target":"Feature Store","critical":true}'::jsonb),
    ('{"source":"Volatility & Risk Metrics Module","target":"Feature Store","critical":true}'::jsonb),
    ('{"source":"Market Context Module","target":"Feature Store","critical":true}'::jsonb),
    ('{"source":"Event & News Intelligence Module","target":"Feature Store","critical":false}'::jsonb),
    ('{"source":"Earnings & Dividend Intelligence Module","target":"Feature Store","critical":false}'::jsonb),
    ('{"source":"Fundamental & Valuation Module","target":"Feature Store","critical":false}'::jsonb)
),
graphs AS (
  SELECT graph_id
    FROM audit.module_dependency_graph
   WHERE status = 'active'
)
UPDATE audit.module_dependency_graph graph
   SET version = CASE WHEN graph.version = '1.0' THEN '1.1' ELSE graph.version END,
       graph_payload = jsonb_set(
         graph.graph_payload,
         '{edges}',
         (
           SELECT jsonb_agg(edge_payload)
             FROM (
               SELECT existing.edge_payload
                 FROM jsonb_array_elements(COALESCE(graph.graph_payload -> 'edges', '[]'::jsonb)) AS existing(edge_payload)
                WHERE NOT EXISTS (
                  SELECT 1
                    FROM new_edges candidate
                   WHERE candidate.edge_payload ->> 'source' = existing.edge_payload ->> 'source'
                     AND candidate.edge_payload ->> 'target' = existing.edge_payload ->> 'target'
                )
               UNION ALL
               SELECT edge_payload
                 FROM new_edges
             ) merged_edges
         ),
         true
       )
 WHERE graph.graph_id IN (SELECT graph_id FROM graphs);

INSERT INTO audit.audit_record (
  module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
  'Orchestration Module',
  'info',
  'feature_store_current_cycle_edges_enabled',
  'Feature-writing modules now route through Feature Store so live market/news cycles can continue to normalization, decision, risk and execution with current-cycle refs.',
  'migration',
  '034_feature_store_current_cycle_edges',
  ARRAY['feature_store_edges', 'current_cycle_refs', 'live_autonomous_trading_loop'],
  jsonb_build_object(
    'edges_added', jsonb_build_array(
      'Market Data Metrics Module->Feature Store',
      'Liquidity & Microstructure Module->Feature Store',
      'Volatility & Risk Metrics Module->Feature Store',
      'Market Context Module->Feature Store',
      'Event & News Intelligence Module->Feature Store',
      'Earnings & Dividend Intelligence Module->Feature Store',
      'Fundamental & Valuation Module->Feature Store'
    )
  )
);

COMMIT;
