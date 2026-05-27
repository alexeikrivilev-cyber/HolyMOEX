-- Controlled paper/shadow validation report.
-- SELECT-only report for analytics views. Run after a bounded analysis_only or paper_trading window.

-- A) Live dashboard
SELECT *
  FROM analytics.live_dashboard_summary;

-- B) Churn diagnostics
SELECT *
  FROM analytics.churn_round_trips
 ORDER BY minutes_between ASC, second_ts DESC
 LIMIT 100;

-- C) Toxic instruments
SELECT *
  FROM analytics.instrument_live_stats
 ORDER BY avg_action_aligned_return_30m ASC NULLS LAST,
          churn_flip_count DESC
 LIMIT 50;

-- D) Guardrail rejections
SELECT *
  FROM analytics.guardrail_rejections
 ORDER BY rejected_at DESC
 LIMIT 200;

-- E) Guardrail rejection summary
SELECT
  rejection_reason_code,
  count(*) AS rejection_count,
  count(DISTINCT instrument_id) AS instruments_count,
  avg(expected_edge_after_cost_bps) AS avg_expected_edge_after_cost_bps,
  avg(edge_to_cost_ratio) AS avg_edge_to_cost_ratio,
  count(*) FILTER (WHERE was_turnover_mandate) AS turnover_mandate_count,
  count(*) FILTER (WHERE was_quarantine_related) AS quarantine_related_count,
  count(*) FILTER (WHERE was_churn_related) AS churn_related_count,
  count(*) FILTER (WHERE was_edge_related) AS edge_related_count
  FROM analytics.guardrail_rejections
 GROUP BY rejection_reason_code
 ORDER BY rejection_count DESC, rejection_reason_code;

-- F) Edge calibration
SELECT *
  FROM analytics.edge_calibration
 ORDER BY abs(calibration_error_30m_bps) DESC NULLS LAST
 LIMIT 100;

-- G) Edge bucket summary
SELECT
  calibration.edge_bucket,
  count(*) AS decisions_count,
  count(*) FILTER (WHERE outcome.was_executed) AS executed_count,
  avg(calibration.expected_edge_after_cost_bps) AS avg_expected_edge_after_cost_bps,
  avg(calibration.realized_action_aligned_return_30m_bps) AS avg_realized_action_aligned_return_30m_bps,
  avg(calibration.calibration_error_30m_bps) AS avg_calibration_error_30m_bps,
  avg(
    CASE
      WHEN calibration.realized_action_aligned_return_30m_bps > 0 THEN 1.0
      WHEN calibration.realized_action_aligned_return_30m_bps IS NOT NULL THEN 0.0
    END
  ) AS win_rate_30m
  FROM analytics.edge_calibration calibration
  LEFT JOIN analytics.decision_outcome_30m outcome
    ON outcome.decision_id = calibration.decision_id
 GROUP BY calibration.edge_bucket
 ORDER BY calibration.edge_bucket;

-- H) PnL by reason code
-- This view expands one decision into several rows when it has several reason codes.
-- Use it for attribution by reason code, not as a total PnL source.
SELECT *
  FROM analytics.pnl_by_reason_code
 ORDER BY avg_action_aligned_return_30m_bps ASC NULLS LAST;

-- I) Execution cost realized
SELECT *
  FROM analytics.execution_cost_realized
 ORDER BY abs(cost_error_bps) DESC NULLS LAST
 LIMIT 100;

-- J) Feature outcome attribution
SELECT *
  FROM analytics.feature_outcome_attribution
 ORDER BY avg_action_aligned_return_30m_bps ASC NULLS LAST
 LIMIT 100;
