from __future__ import annotations

import math

from agent_app.contracts.unified_objects import ModuleJob, TimeRange
from agent_app.modules.decision_engine.repository import (
    DecisionExplanation,
    DecisionRecord,
    FeatureVector,
    InMemoryDecisionEngineRepository,
    MarketStateRecord,
    MetricWeightRule,
    PortfolioSnapshot,
    WeightsProfile,
)
from agent_app.modules.decision_engine.service import DecisionEngineService, DecisionRequest, InstrumentDecision


def _service() -> DecisionEngineService:
    return DecisionEngineService(repository=InMemoryDecisionEngineRepository())


def _competitive_request() -> DecisionRequest:
    return DecisionRequest(
        decision_request_id="decision_request_competitive",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizon="intraday",
        as_of_ts="2026-05-24T09:00:00Z",
        feature_vector_refs=("features.feature_vector:fv_competitive",),
        portfolio_state_ref="portfolio.portfolio_snapshot:portfolio_competitive",
        weights_profile_id="weights_test",
        run_mode="live_trading",
        decision_mode="normal",
    )


def _competitive_instrument_decision(
    *,
    instrument_id: str = "moex:SBER",
    action: str = "hold",
    raw_bps: float = 5.0,
    execution_cost_bps: float = 50.0,
    reason_codes: tuple[str, ...] = ("cost_proxy_used",),
    matched_feature_count: int = 1,
) -> InstrumentDecision:
    record = DecisionRecord(
        decision_record_id=f"record_{instrument_id}",
        decision_set_id="decision_set_competitive",
        instrument_id=instrument_id,
        action=action,
        target_position_pct=0.0,
        target_quantity=0.0,
        confidence_score=1.0,
        expected_edge_score=raw_bps / 100.0,
        risk_score=0.2,
        primary_reason_codes=reason_codes,
        feature_contributions={},
    )
    payload = {
        "instrument_id": instrument_id,
        "action": action,
        "target_position_pct": 0.0,
        "target_quantity": 0.0,
        "confidence_score": 1.0,
        "expected_edge_score": raw_bps / 100.0,
        "risk_score": 0.2,
        "primary_reason_codes": list(reason_codes),
        "reason_codes": list(reason_codes),
        "raw_expected_edge_bps": raw_bps,
        "execution_cost_estimate_bps": execution_cost_bps,
        "expected_edge_after_cost_bps": raw_bps - execution_cost_bps,
        "expected_edge_after_cost_score": (raw_bps - execution_cost_bps) / 100.0,
        "edge_to_cost_ratio": abs(raw_bps) / max(execution_cost_bps, 0.01),
        "cost_model_quality": "proxy",
        "matched_feature_count": matched_feature_count,
        "candidate_target_position_pct": 0.0,
        "candidate_target_quantity": 0.0,
        "latest_price": 250.0,
        "portfolio_equity": 1_000_000.0,
        "max_position_pct": 0.02,
    }
    explanation = DecisionExplanation(
        decision_explanation_id=f"explanation_{instrument_id}",
        decision_record_id=record.decision_record_id,
        explanation={"horizon": "intraday", "reason_codes": list(reason_codes)},
    )
    return InstrumentDecision(record=record, explanation=explanation, decision_payload=payload)


def _run_single_decision(
    *,
    feature_map: dict,
    weight_metric_name: str,
    run_mode: str = "live_trading",
    min_confidence_score: float = 0.0,
) -> dict:
    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_weight_coverage",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                feature_map,
                1.0,
                1.0,
                "test",
            ),
        ),
        weights_profiles=(
            WeightsProfile("weights_test", "test", "1", "active", "intraday", ("analysis_only", "paper_trading", "live_trading")),
        ),
        metric_weight_rules=(
            MetricWeightRule(
                "rule_weight_coverage",
                "weights_test",
                weight_metric_name,
                "price",
                "intraday",
                "all",
                (),
                None,
                1.0,
                "positive",
                "identity",
                min_confidence_score,
                "downweight",
                "test",
            ),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot("portfolio_weight_coverage", "portfolio", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {}),
        ),
        market_state_records=(
            MarketStateRecord("market_weight_coverage", "moex_top20_manual", "2026-05-24T09:00:00Z", "open", None, {}),
        ),
    )
    job = ModuleJob(
        job_id="job_weight_coverage",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_weight_coverage", "portfolio.portfolio_snapshot:portfolio_weight_coverage"),
        config_ref="weights_test",
        run_mode=run_mode,
        idempotency_key="idem_weight_coverage",
    )
    payload = {
        "decision_request": {
            "decision_request_id": "decision_request_weight_coverage",
            "universe_id": "moex_top20_manual",
            "instrument_ids": ["moex:SBER"],
            "horizon": "intraday",
            "as_of_ts": "2026-05-24T09:00:00Z",
            "feature_vector_refs": ["features.feature_vector:fv_weight_coverage"],
            "portfolio_state_ref": "portfolio.portfolio_snapshot:portfolio_weight_coverage",
            "weights_profile_id": "weights_test",
            "run_mode": run_mode,
            "decision_mode": "normal",
        }
    }
    result = DecisionEngineService(repository=repository).process(payload, job)
    return result.decision_set.decisions[0]  # type: ignore[union-attr]


def test_decision_cost_model_positive_edge_low_costs_marks_positive_after_cost() -> None:
    estimate = _service().estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.50,
        planned_order_notional=50_000,
        vector_features={
            "spread_bps": {"raw_value": 2.0},
            "estimated_slippage_bps": {"raw_value": 3.0},
            "commission_bps": {"raw_value": 1.0},
            "requested_notional_to_depth_ratio": {"raw_value": 0.01},
        },
    )

    assert estimate.expected_edge_after_cost_bps > 0
    assert "positive_edge_after_cost" in estimate.reason_codes


def test_decision_cost_model_positive_gross_edge_high_costs_marks_negative_after_cost() -> None:
    estimate = _service().estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.20,
        planned_order_notional=2_000_000,
        vector_features={
            "spread_bps": {"raw_value": 20.0},
            "estimated_slippage_bps": {"raw_value": 20.0},
            "commission_bps": {"raw_value": 5.0},
            "requested_notional_to_depth_ratio": {"raw_value": 0.30},
        },
    )

    assert estimate.expected_edge_after_cost_bps < 0
    assert "negative_edge_after_cost" in estimate.reason_codes
    assert "high_execution_cost" in estimate.reason_codes


def test_decision_cost_model_missing_cost_inputs_uses_conservative_defaults() -> None:
    estimate = _service().estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.20,
        planned_order_notional=None,
        vector_features={},
    )

    assert estimate.execution_cost_estimate_bps == 40.0
    assert estimate.cost_model_quality == "proxy"
    assert "cost_proxy_used" in estimate.reason_codes
    assert "cost_proxy_missing_spread" in estimate.reason_codes
    assert "cost_proxy_missing_slippage" in estimate.reason_codes
    assert "cost_proxy_missing_commission" in estimate.reason_codes
    assert "cost_proxy_missing_impact" in estimate.reason_codes


def test_decision_cost_model_negative_raw_edge_subtracts_costs() -> None:
    estimate = _service().estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=-0.20,
        planned_order_notional=None,
        vector_features={},
    )

    assert estimate.raw_expected_edge_bps == -20.0
    assert estimate.execution_cost_estimate_bps == 40.0
    assert estimate.expected_edge_after_cost_bps == -60.0
    assert "negative_edge_after_cost" in estimate.reason_codes


def test_decision_cost_model_slippage_scenarios_select_by_notional() -> None:
    features = {
        "spread_bps": {"raw_value": 1.0},
        "estimated_slippage_100k": {"raw_value": 4.0},
        "estimated_slippage_1m": {"raw_value": 12.0},
        "commission_bps": {"raw_value": 1.0},
        "requested_notional_to_depth_ratio": {"raw_value": 0.01},
    }
    service = _service()

    small = service.estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.50,
        planned_order_notional=50_000,
        vector_features=features,
    )
    large = service.estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.50,
        planned_order_notional=1_500_000,
        vector_features=features,
    )

    assert small.cost_components.estimated_slippage_bps == 4.0
    assert large.cost_components.estimated_slippage_bps == 12.0


def test_decision_planned_order_notional_uses_delta_quantity() -> None:
    service = _service()

    assert service.planned_order_notional(
        target_quantity=100,
        current_quantity=100,
        target_position_pct=0.10,
        latest_price=10,
        portfolio_equity=100_000,
    ) == 0.0
    assert service.planned_order_notional(
        target_quantity=150,
        current_quantity=100,
        target_position_pct=0.15,
        latest_price=10,
        portfolio_equity=100_000,
    ) == 500.0
    assert service.planned_order_notional(
        target_quantity=50,
        current_quantity=100,
        target_position_pct=0.05,
        latest_price=10,
        portfolio_equity=100_000,
    ) == 500.0


def test_decision_cost_model_edge_ratio_and_bps_scale_are_stable() -> None:
    estimate = _service().estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.20,
        planned_order_notional=50_000,
        vector_features={
            "spread_bps": {"raw_value": 1.0},
            "estimated_slippage_bps": {"raw_value": 1.0},
            "commission_bps": {"raw_value": 1.0},
            "requested_notional_to_depth_ratio": {"raw_value": 0.01},
        },
    )

    assert math.isclose(estimate.raw_expected_edge_bps, 20.0)
    assert estimate.edge_to_cost_ratio > 0
    assert math.isfinite(estimate.edge_to_cost_ratio)


def test_decision_cost_model_full_requires_notional_context() -> None:
    service = _service()
    features_without_notional_context = {
        "spread_bps": {"raw_value": 1.0},
        "estimated_slippage_bps": {"raw_value": 1.0},
        "commission_bps": {"raw_value": 1.0},
        "liquidity_risk_score": {"raw_value": 0.1},
    }
    unknown_notional = service.estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.50,
        planned_order_notional=None,
        vector_features=features_without_notional_context,
    )

    assert unknown_notional.cost_model_quality != "full"
    assert "cost_model_notional_unknown" in unknown_notional.reason_codes

    known_notional = service.estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.50,
        planned_order_notional=50_000,
        vector_features=features_without_notional_context,
    )

    assert known_notional.cost_model_quality == "full"


def test_decision_cost_model_unknown_notional_with_defaults_is_not_full() -> None:
    estimate = _service().estimate_decision_cost(
        horizon="intraday",
        expected_edge_score=0.50,
        planned_order_notional=None,
        vector_features={},
    )

    assert estimate.cost_model_quality == "proxy"
    assert "cost_model_full" not in estimate.reason_codes
    assert "cost_model_notional_unknown" in estimate.reason_codes


def test_decision_payload_contains_backward_compatible_post_cost_economics() -> None:
    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_cost_payload",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "expected_edge_score": {"raw_value": 0.50, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "spread_bps": {"raw_value": 2.0},
                    "estimated_slippage_bps": {"raw_value": 3.0},
                    "commission_bps": {"raw_value": 1.0},
                    "requested_notional_to_depth_ratio": {"raw_value": 0.01},
                    "latest_price": {"raw_value": 250.0},
                },
                1.0,
                1.0,
                "test",
            ),
        ),
        weights_profiles=(
            WeightsProfile("weights_test", "test", "1", "active", "intraday", ("analysis_only", "paper_trading", "live_trading")),
        ),
        metric_weight_rules=(
            MetricWeightRule("rule_edge", "weights_test", "expected_edge_score", "price", "intraday", "all", (), None, 1.0, "positive", "identity", 0.0, "downweight", "test"),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot("portfolio_cost_payload", "portfolio", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {}),
        ),
    )
    job = ModuleJob(
        job_id="job_cost_payload",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_cost_payload", "portfolio.portfolio_snapshot:portfolio_cost_payload"),
        config_ref="weights_test",
        run_mode="analysis_only",
        idempotency_key="idem_cost_payload",
    )
    payload = {
        "decision_request": {
            "decision_request_id": "decision_request_cost_payload",
            "universe_id": "moex_top20_manual",
            "instrument_ids": ["moex:SBER"],
            "horizon": "intraday",
            "as_of_ts": "2026-05-24T09:00:00Z",
            "feature_vector_refs": ["features.feature_vector:fv_cost_payload"],
            "portfolio_state_ref": "portfolio.portfolio_snapshot:portfolio_cost_payload",
            "weights_profile_id": "weights_test",
            "run_mode": "analysis_only",
            "decision_mode": "normal",
        }
    }

    result = DecisionEngineService(repository=repository).process(payload, job)
    decision = result.decision_set.decisions[0]  # type: ignore[union-attr]

    assert result.decision_records
    assert "expected_edge_score" in decision
    assert decision["cost_model_version"] == "decision_cost_model_v1"
    assert "expected_edge_after_cost_bps" in decision
    assert "execution_cost_estimate_bps" in decision
    assert "edge_to_cost_ratio" in decision
    assert "cost_components" in decision
    assert "positive_edge_after_cost" in decision["reason_codes"]
    assert "no_weighted_features" not in decision["reason_codes"]
    assert decision["matched_feature_count"] == 1


def test_risk_guardrails_read_decision_expected_edge_after_cost_bps_without_proxy() -> None:
    from agent_app.modules.risk_control.repository import (
        FeatureVector as RiskFeatureVector,
        InMemoryRiskControlRepository,
        PortfolioSnapshot as RiskPortfolioSnapshot,
        RiskPolicy,
    )
    from agent_app.modules.risk_control.service import (
        ProfitabilityGuardrails,
        ProfitabilityGuardrailsContext,
        RiskCheckRequest,
    )

    guardrails = ProfitabilityGuardrails(InMemoryRiskControlRepository())
    context = ProfitabilityGuardrailsContext(
        request=RiskCheckRequest("decision", "portfolio", "risk_policy", "market", "quality", "live_trading"),
        decision_set_id="decision",
        decision={
            "instrument_id": "moex:SBER",
            "action": "buy",
            "expected_edge_score": 0.50,
            "expected_edge_after_cost_score": 0.45,
            "expected_edge_after_cost_bps": 45.0,
            "execution_cost_estimate_bps": 10.0,
            "edge_to_cost_ratio": 4.5,
        },
        portfolio_snapshot=RiskPortfolioSnapshot("portfolio", "portfolio", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {}),
        position=None,
        feature_vector=RiskFeatureVector("fv", "moex:SBER", "intraday", "2026-05-24T09:00:00Z", {}, 1.0, 1.0, "test"),
        position_effect="open_long",
        side="buy",
        expected_edge_score=0.50,
        expected_edge_after_cost_score=0.45,
        risk_policy=RiskPolicy("risk_policy", "risk", "1", "active", ("live_trading",), {}),
        portfolio_limits=(),
        as_of_ts="2026-05-24T09:00:00Z",
    )

    metrics = guardrails.edge_metrics(context, "normal")

    assert metrics["guardrail_expected_edge_after_cost_bps"] == 45.0
    assert "expected_edge_after_cost_from_score" not in metrics


def test_competitive_mode_default_off_keeps_old_hold_behavior(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_COMPETITIVE_MODE", raising=False)
    monkeypatch.setenv("DECISION_ACTION_THRESHOLD", "0.10")
    monkeypatch.setenv("DECISION_MIN_POST_COST_EDGE_SCORE", "0.10")
    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_competitive",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "expected_edge_score": {"raw_value": 0.05, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "latest_price": {"raw_value": 250.0},
                },
                1.0,
                1.0,
                "test",
            ),
        ),
        weights_profiles=(
            WeightsProfile("weights_test", "test", "1", "active", "intraday", ("live_trading",)),
        ),
        metric_weight_rules=(
            MetricWeightRule("rule_edge", "weights_test", "expected_edge_score", "price", "intraday", "all", (), None, 1.0, "positive", "identity", 0.0, "downweight", "test"),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot("portfolio_competitive", "portfolio", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {}),
        ),
        market_state_records=(
            MarketStateRecord("market_competitive", "moex_top20_manual", "2026-05-24T09:00:00Z", "open", None, {}),
        ),
    )
    job = ModuleJob(
        job_id="job_competitive_default_off",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_competitive", "portfolio.portfolio_snapshot:portfolio_competitive"),
        config_ref="weights_test",
        run_mode="live_trading",
        idempotency_key="idem_competitive_default_off",
    )
    payload = {"decision_request": _competitive_request().to_record().__dict__}
    payload["decision_request"].pop("payload")

    result = DecisionEngineService(repository=repository).process(payload, job)
    decision = result.decision_set.decisions[0]  # type: ignore[union-attr]

    assert decision["action"] == "hold"
    assert "competitive_expected_edge_after_cost_bps" not in decision


def test_competitive_mode_promotes_best_proxy_hold_candidate_to_buy(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.setenv("DECISION_ACTION_THRESHOLD", "0.10")
    monkeypatch.setenv("DECISION_MIN_POST_COST_EDGE_SCORE", "0.10")
    monkeypatch.setenv("DECISION_COMPETITIVE_COST_PROXY_CAP_BPS", "3")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_RAW_EDGE_BPS", "1")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_AFTER_COST_BPS", "0.5")
    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_competitive",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "expected_edge_score": {"raw_value": 0.05, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "latest_price": {"raw_value": 250.0},
                },
                1.0,
                1.0,
                "test",
            ),
        ),
        weights_profiles=(
            WeightsProfile("weights_test", "test", "1", "active", "intraday", ("live_trading",)),
        ),
        metric_weight_rules=(
            MetricWeightRule("rule_edge", "weights_test", "expected_edge_score", "price", "intraday", "all", (), None, 1.0, "positive", "identity", 0.0, "downweight", "test"),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot("portfolio_competitive", "portfolio", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {}),
        ),
        market_state_records=(
            MarketStateRecord("market_competitive", "moex_top20_manual", "2026-05-24T09:00:00Z", "open", None, {}),
        ),
    )
    job = ModuleJob(
        job_id="job_competitive",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_competitive", "portfolio.portfolio_snapshot:portfolio_competitive"),
        config_ref="weights_test",
        run_mode="live_trading",
        idempotency_key="idem_competitive",
    )
    payload = {"decision_request": _competitive_request().to_record().__dict__}
    payload["decision_request"].pop("payload")

    result = DecisionEngineService(repository=repository).process(payload, job)
    decision = result.decision_set.decisions[0]  # type: ignore[union-attr]

    assert decision["action"] == "buy"
    assert decision["competitive_cost_cap_applied"] is True
    assert decision["competitive_execution_cost_bps"] == 3.0
    assert decision["competitive_expected_edge_after_cost_bps"] == 2.0
    assert decision["expected_edge_after_cost_bps"] == 2.0
    assert "competitive_leaderboard_candidate" in decision["reason_codes"]
    assert "competitive_cost_cap_applied" in decision["reason_codes"]
    assert "competitive_fallback_action" in decision["reason_codes"]
    assert "original_action:hold" in decision["reason_codes"]


def test_competitive_mode_does_not_promote_negative_after_cost_candidate(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    decisions = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(raw_bps=2.0, execution_cost_bps=50.0),
        ),
    )

    assert decisions[0].record.action == "hold"
    assert decisions[0].decision_payload["competitive_expected_edge_after_cost_bps"] == -1.0


def test_competitive_mode_zero_raw_edge_requires_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.setenv("DECISION_COMPETITIVE_COST_PROXY_CAP_BPS", "0.5")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_RAW_EDGE_BPS", "0")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_AFTER_COST_BPS", "-1")
    monkeypatch.delenv("DECISION_COMPETITIVE_ALLOW_ZERO_RAW_EDGE", raising=False)
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())

    blocked = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(raw_bps=0.0, execution_cost_bps=30.0),
        ),
    )
    assert blocked[0].record.action == "hold"

    monkeypatch.setenv("DECISION_COMPETITIVE_ALLOW_ZERO_RAW_EDGE", "true")
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    promoted = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(raw_bps=0.0, execution_cost_bps=30.0),
        ),
    )
    assert promoted[0].record.action == "buy"
    assert promoted[0].decision_payload["competitive_expected_edge_after_cost_bps"] == -0.5
    assert "competitive_leaderboard_candidate" in promoted[0].record.primary_reason_codes


def test_competitive_mode_skips_fallback_when_regular_buy_exists(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.delenv("DECISION_COMPETITIVE_SELECT_ACTIVE_ACTIONS", raising=False)
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    decisions = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(instrument_id="moex:SBER", action="buy", raw_bps=10.0),
            _competitive_instrument_decision(instrument_id="moex:GAZP", action="hold", raw_bps=10.0),
        ),
    )

    assert [item.record.action for item in decisions] == ["buy", "hold"]
    assert "competitive_expected_edge_after_cost_bps" not in decisions[1].decision_payload


def test_competitive_mode_can_select_one_active_buy_candidate(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.setenv("DECISION_COMPETITIVE_SELECT_ACTIVE_ACTIONS", "true")
    monkeypatch.setenv("DECISION_COMPETITIVE_MAX_CANDIDATES_PER_CYCLE", "1")
    monkeypatch.setenv("DECISION_COMPETITIVE_COST_PROXY_CAP_BPS", "0.5")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_RAW_EDGE_BPS", "0")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_AFTER_COST_BPS", "-1")
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    decisions = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(instrument_id="moex:CHMF", action="buy", raw_bps=2.0),
            _competitive_instrument_decision(instrument_id="moex:NLMK", action="buy", raw_bps=1.5),
            _competitive_instrument_decision(instrument_id="moex:ROSN", action="hold", raw_bps=1.0),
        ),
    )

    assert [item.record.action for item in decisions] == ["buy", "hold", "hold"]
    selected = decisions[0]
    assert selected.decision_payload["expected_edge_after_cost_bps"] == 1.5
    assert selected.decision_payload["competitive_selected"] is True
    assert "competitive_leaderboard_candidate" in selected.record.primary_reason_codes
    assert "competitive_active_action_selected" in selected.record.primary_reason_codes
    assert "competitive_active_candidate_not_selected" in decisions[1].record.primary_reason_codes


def test_competitive_mode_respects_forbidden_reason_codes(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    decisions = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(reason_codes=("cost_proxy_used", "data_quality_block")),
            _competitive_instrument_decision(instrument_id="moex:GAZP", reason_codes=("cost_proxy_used", "kill_switch")),
            _competitive_instrument_decision(instrument_id="moex:LKOH", reason_codes=("cost_proxy_used", "liquidity_block")),
        ),
    )

    assert [item.record.action for item in decisions] == ["hold", "hold", "hold"]


def test_competitive_mode_block_to_buy_requires_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.delenv("DECISION_COMPETITIVE_ALLOW_BLOCK_TO_TRADE", raising=False)
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    blocked = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(action="block", reason_codes=("cost_proxy_used", "risk_score_block")),
        ),
    )
    assert blocked[0].record.action == "block"

    monkeypatch.setenv("DECISION_COMPETITIVE_ALLOW_BLOCK_TO_TRADE", "true")
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    promoted = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(action="block", reason_codes=("cost_proxy_used", "risk_score_block")),
        ),
    )
    assert promoted[0].record.action == "buy"
    assert "original_action:block" in promoted[0].record.primary_reason_codes


def test_competitive_mode_respects_max_candidates_and_preserves_reason_codes(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.setenv("DECISION_COMPETITIVE_MAX_CANDIDATES_PER_CYCLE", "2")
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    decisions = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(instrument_id="moex:A", raw_bps=4.0, reason_codes=("cost_proxy_used", "old_reason_a")),
            _competitive_instrument_decision(instrument_id="moex:B", raw_bps=8.0, reason_codes=("cost_proxy_used", "old_reason_b")),
            _competitive_instrument_decision(instrument_id="moex:C", raw_bps=6.0, reason_codes=("cost_proxy_used", "old_reason_c")),
        ),
    )

    assert [item.record.action for item in decisions] == ["hold", "buy", "buy"]
    selected_reasons = decisions[1].record.primary_reason_codes
    assert "old_reason_b" in selected_reasons
    assert "competitive_leaderboard_candidate" in selected_reasons


def test_decision_feature_weight_exact_match_has_coverage(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", raising=False)
    decision = _run_single_decision(
        feature_map={
            "intraday_return": {"raw_value": 0.03, "confidence_score": 1.0, "ttl_status": "fresh"},
            "latest_price": {"raw_value": 250.0},
        },
        weight_metric_name="intraday_return",
    )

    assert "no_weighted_features" not in decision["reason_codes"]
    assert decision["matched_feature_count"] == 1
    assert decision["feature_weight_coverage_ratio"] == 1.0
    assert decision["expected_edge_score"] != 0.0


def test_decision_feature_weight_alias_match_builds_contribution(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", raising=False)
    decision = _run_single_decision(
        feature_map={
            "distance_to_20d_low": {
                "raw_value": 0.50,
                "normalized_value": 1.0,
                "confidence_score": 1.0,
                "ttl_status": "fresh",
            },
            "latest_price": {"raw_value": 250.0},
        },
        weight_metric_name="recovery_ratio",
    )

    assert "no_weighted_features" not in decision["reason_codes"]
    assert "feature_alias_match:recovery_ratio->distance_to_20d_low" in decision["reason_codes"]
    assert decision["matched_feature_count"] == 1
    assert decision["expected_edge_score"] > 0


def test_decision_feature_vector_empty_does_not_trade(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.delenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", raising=False)
    decision = _run_single_decision(feature_map={}, weight_metric_name="intraday_return")

    assert decision["action"] in {"hold", "block"}
    assert decision["expected_edge_score"] == 0.0
    assert "feature_vector_empty" in decision["reason_codes"]
    assert "no_weighted_features" in decision["reason_codes"]


def test_decision_weight_coverage_fallback_disabled_keeps_old_zero_signal(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", raising=False)
    decision = _run_single_decision(
        feature_map={
            "unmapped_signal": {"raw_value": 0.50, "confidence_score": 1.0, "ttl_status": "fresh"},
            "latest_price": {"raw_value": 250.0},
        },
        weight_metric_name="intraday_return",
    )

    assert decision["expected_edge_score"] == 0.0
    assert "no_weighted_features" in decision["reason_codes"]
    assert decision["matched_feature_count"] == 0


def test_decision_weight_coverage_fallback_enabled_uses_whitelist_signal(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", "true")
    decision = _run_single_decision(
        feature_map={
            "price_return": {"raw_value": 0.50, "confidence_score": 1.0, "ttl_status": "fresh"},
            "latest_price": {"raw_value": 250.0},
        },
        weight_metric_name="missing_weight_name",
    )

    assert decision["expected_edge_score"] > 0.0
    assert "weight_coverage_fallback_used" in decision["reason_codes"]
    assert "no_weighted_features" not in decision["reason_codes"]


def test_decision_weight_coverage_fallback_does_not_use_turnover_urgency_as_alpha(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", "true")
    decision = _run_single_decision(
        feature_map={
            "turnover_urgency_score": {"raw_value": 1.0, "confidence_score": 1.0, "ttl_status": "fresh"},
            "latest_price": {"raw_value": 250.0},
        },
        weight_metric_name="missing_weight_name",
    )

    assert decision["expected_edge_score"] == 0.0
    assert "weight_coverage_fallback_used" not in decision["reason_codes"]
    assert "no_weighted_features" in decision["reason_codes"]


def test_decision_repository_falls_back_from_requested_empty_vector_to_recent_usable_vector(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", raising=False)
    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_empty_latest",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {"_meta": {"quality_flags": ["no_features"]}},
                0.0,
                0.0,
                "test",
            ),
            FeatureVector(
                "fv_recent_usable",
                "moex:SBER",
                "intraday",
                "2026-05-24T08:59:00Z",
                {
                    "intraday_return": {"raw_value": 0.05, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "latest_price": {"raw_value": 250.0},
                },
                1.0,
                1.0,
                "test",
            ),
        ),
        weights_profiles=(
            WeightsProfile("weights_test", "test", "1", "active", "intraday", ("live_trading",)),
        ),
        metric_weight_rules=(
            MetricWeightRule("rule_edge", "weights_test", "intraday_return", "price", "intraday", "all", (), None, 1.0, "positive", "identity", 0.0, "downweight", "test"),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot("portfolio_weight_coverage", "portfolio", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {}),
        ),
        market_state_records=(
            MarketStateRecord("market_weight_coverage", "moex_top20_manual", "2026-05-24T09:00:00Z", "open", None, {}),
        ),
    )
    job = ModuleJob(
        job_id="job_empty_vector_fallback",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_empty_latest", "portfolio.portfolio_snapshot:portfolio_weight_coverage"),
        config_ref="weights_test",
        run_mode="live_trading",
        idempotency_key="idem_empty_vector_fallback",
    )
    payload = {
        "decision_request": {
            "decision_request_id": "decision_request_empty_vector_fallback",
            "universe_id": "moex_top20_manual",
            "instrument_ids": ["moex:SBER"],
            "horizon": "intraday",
            "as_of_ts": "2026-05-24T09:00:00Z",
            "feature_vector_refs": ["features.feature_vector:fv_empty_latest"],
            "portfolio_state_ref": "portfolio.portfolio_snapshot:portfolio_weight_coverage",
            "weights_profile_id": "weights_test",
            "run_mode": "live_trading",
            "decision_mode": "normal",
        }
    }

    result = DecisionEngineService(repository=repository).process(payload, job)
    decision = result.decision_set.decisions[0]  # type: ignore[union-attr]

    assert decision["feature_vector_id"] == "fv_recent_usable"
    assert decision["matched_feature_count"] == 1
    assert decision["expected_edge_score"] != 0.0
    assert "feature_vector_empty" not in decision["reason_codes"]


def test_competitive_mode_does_not_trade_zero_coverage_without_fallback(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_COMPETITIVE_MODE", "true")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_RAW_EDGE_BPS", "0")
    monkeypatch.setenv("DECISION_COMPETITIVE_MIN_AFTER_COST_BPS", "-1")
    monkeypatch.delenv("DECISION_ALLOW_WEIGHT_COVERAGE_FALLBACK", raising=False)
    service = DecisionEngineService(repository=InMemoryDecisionEngineRepository())
    decisions = service.apply_competitive_fallback(
        request=_competitive_request(),
        instrument_decisions=(
            _competitive_instrument_decision(
                raw_bps=0.0,
                execution_cost_bps=0.5,
                reason_codes=("cost_proxy_used", "no_weighted_features"),
                matched_feature_count=0,
            ),
        ),
    )

    assert decisions[0].record.action == "hold"
