BEGIN;

UPDATE audit.module_dependency_graph graph
   SET version = CASE WHEN graph.version IN ('1.0', '1.1', '1.2') THEN '1.3' ELSE graph.version END,
       graph_payload = jsonb_set(
         graph.graph_payload,
         '{edges}',
         (
           SELECT jsonb_agg(edge_payload)
             FROM jsonb_array_elements(COALESCE(graph.graph_payload -> 'edges', '[]'::jsonb)) AS edge(edge_payload)
            WHERE NOT (
              edge_payload ->> 'source' = 'Raw Market Data Store'
              AND edge_payload ->> 'target' = 'Derivatives & Positioning Module'
            )
         ),
         true
       )
 WHERE graph.status = 'active';

INSERT INTO audit.audit_record (
  module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
) VALUES (
  'Orchestration Module',
  'info',
  'derivatives_removed_from_live_market_cycle',
  'Derivatives & Positioning is no longer in the live Raw Market Data cycle so autonomous trading is not delayed by optional derivatives enrichment.',
  'migration',
  '036_remove_derivatives_from_live_market_cycle',
  ARRAY['live_market_cycle', 'non_critical_derivatives', 'latency_reduction'],
  '{"removed_edge":"Raw Market Data Store->Derivatives & Positioning Module"}'::jsonb
);

COMMIT;
