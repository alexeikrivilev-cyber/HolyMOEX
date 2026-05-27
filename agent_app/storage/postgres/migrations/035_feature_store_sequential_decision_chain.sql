BEGIN;

UPDATE audit.module_dependency_graph graph
   SET version = CASE WHEN graph.version IN ('1.0', '1.1') THEN '1.2' ELSE graph.version END,
       graph_payload = jsonb_set(
         graph.graph_payload,
         '{edges}',
         (
           SELECT jsonb_agg(edge_payload)
             FROM jsonb_array_elements(COALESCE(graph.graph_payload -> 'edges', '[]'::jsonb)) AS edge(edge_payload)
            WHERE NOT (
              edge_payload ->> 'source' = 'Feature Store'
              AND edge_payload ->> 'target' IN (
                'Decision Engine Module',
                'Risk Control Module',
                'Execution Engine Module',
                'Portfolio State Module'
              )
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
  'feature_store_sequential_decision_chain_enabled',
  'Feature Store now routes through Data Quality and Normalization before Decision, Risk and Execution, preserving current-cycle order.',
  'migration',
  '035_feature_store_sequential_decision_chain',
  ARRAY['sequential_decision_chain', 'current_cycle_refs', 'risk_after_decision'],
  jsonb_build_object(
    'removed_edges', jsonb_build_array(
      'Feature Store->Decision Engine Module',
      'Feature Store->Risk Control Module',
      'Feature Store->Execution Engine Module',
      'Feature Store->Portfolio State Module'
    )
  )
);

COMMIT;
