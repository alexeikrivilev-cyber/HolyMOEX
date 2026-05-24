from __future__ import annotations

from agent_app.contracts.unified_objects import ModuleJob, TimeRange
from agent_app.modules.orchestration.executor import LocalModuleExecutor
from agent_app.modules.portfolio_state.repository import InMemoryPortfolioStateRepository
from agent_app.modules.portfolio_state.service import PortfolioStateService


def _job(module_name: str = "Portfolio State Module", run_mode: str = "paper_trading") -> ModuleJob:
    return ModuleJob(
        job_id="job_test_turnover",
        module_name=module_name,
        contour="execution_contour" if module_name == "Portfolio State Module" else "decision_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("broker:seed", "price:seed", "orders.fill_report:fill1"),
        config_ref="risk_policy:live_autonomous_turnover:v1",
        run_mode=run_mode,
        idempotency_key="idem_test_turnover",
    )


def test_portfolio_state_writes_turnover_mandate_metrics_from_actual_fills() -> None:
    repository = InMemoryPortfolioStateRepository(
        fill_reports=(
            {
                "fill_report_id": "fill1",
                "order_intent_id": "order1",
                "fill_ts": "2026-05-24T08:59:00Z",
                "filled_quantity": 10,
                "fill_price": 250,
                "fees": 1,
                "payload": {"instrument_id": "moex:SBER", "side": "buy"},
            },
        ),
        order_intents=(
            {"order_intent_id": "order1", "instrument_id": "moex:SBER", "side": "buy"},
        ),
        market_prices=(
            {"instrument_id": "moex:SBER", "price": 252, "price_ts": "2026-05-24T08:59:30Z", "source_ref": "price:seed"},
        ),
    )
    service = PortfolioStateService(repository=repository)
    result = service.process(
        {
            "portfolio_update_request": {
                "portfolio_id": "arena_go_default",
                "fill_report_refs": ["orders.fill_report:fill1"],
                "broker_snapshot_ref": "broker:seed",
                "price_snapshot_ref": "price:seed",
                "run_mode": "paper_trading",
                "as_of_ts": "2026-05-24T09:00:00Z",
            }
        },
        _job(),
    )

    assert result.module_job_result.status == "success"
    assert result.portfolio_snapshot is not None
    payload = result.portfolio_snapshot.payload
    assert payload["turnover_mandate_enabled"] is True
    assert payload["gross_turnover_rub_14d"] == 2500
    assert payload["target_gross_turnover_rub_14d"] == 10_000_000
    assert payload["turnover_target_status"] in {"behind", "critically_behind"}


def test_runtime_executor_injects_postgres_repository_when_database_url_is_configured() -> None:
    executor = LocalModuleExecutor(database_url="postgresql://user:pass@localhost:5432/db", use_postgres=True)
    service = executor._service_for("Decision Engine Module")
    assert service.repository.__class__.__name__ == "PostgresDecisionEngineRepository"


def test_in_memory_executor_does_not_inject_real_gateway() -> None:
    executor = LocalModuleExecutor(use_postgres=False)
    service = executor._service_for("Market Data Metrics Module")
    assert getattr(service, "gateway", None) is None


def test_turnover_migration_contains_calculation_version_for_weight_rules() -> None:
    from pathlib import Path

    migration = Path("agent_app/storage/postgres/migrations/012_autonomous_live_turnover_mandate.sql").read_text()
    assert "live_autonomous_turnover_baseline_v1" in migration
    assert migration.count("live_autonomous_turnover_baseline_v1") >= 5


def test_scheduler_converts_db_schedule_payload_to_due_source_entry() -> None:
    from agent_app.scheduler import schedule_entry_from_payload

    entry = schedule_entry_from_payload(
        schedule_config_id="schedule:live_autonomous:decision:1m",
        module_name="Decision Engine Module",
        payload={"trigger": "on_feature_update_or_timer", "interval_seconds": 60, "run_mode": "live_trading"},
        default_run_mode="paper_trading",
    )

    assert entry is not None
    assert entry.source == "Feature Store"
    assert entry.payload_ref == "schedule:live_autonomous:decision:1m"
    assert entry.interval_seconds == 60
    assert entry.run_mode == "live_trading"


def test_scheduler_skips_event_driven_risk_without_timer() -> None:
    from agent_app.scheduler import schedule_entry_from_payload

    entry = schedule_entry_from_payload(
        schedule_config_id="schedule:live_autonomous:risk:on_decision",
        module_name="Risk Control Module",
        payload={"trigger": "on_decision_set", "run_mode": "live_trading"},
        default_run_mode="live_trading",
    )

    assert entry is None


def test_data_intake_migration_allows_source_missing_endpoint_skip_reason() -> None:
    from pathlib import Path

    migration = Path("agent_app/storage/postgres/migrations/013_predfinal_runtime_hardening.sql").read_text()
    assert "source_missing_endpoint" in migration
    assert "scheduled_external_news_discovery_item_skip_reason_code_check" in migration


def test_risk_daily_loss_pct_is_converted_to_rub() -> None:
    from agent_app.modules.risk_control.repository import PortfolioLimit, PortfolioSnapshot, RiskPolicy
    from agent_app.modules.risk_control.service import RiskControlService

    service = RiskControlService()
    policy = RiskPolicy(
        risk_policy_id="risk_policy:test",
        policy_name="test",
        version="1",
        status="active",
        run_mode_allowed=("live_trading",),
        rules={"max_daily_loss_pct": 0.02},
    )
    snapshot = PortfolioSnapshot(
        portfolio_snapshot_id="snapshot1",
        portfolio_id="arena_go_default",
        universe_id="moex_top20_manual",
        as_of_ts="2026-05-24T09:00:00Z",
        initial_capital_rub=1_000_000,
        cash=900_000,
        equity=1_000_000,
        gross_exposure=100_000,
        net_exposure=100_000,
        realized_pnl=-5000,
        unrealized_pnl=0,
        payload={},
    )

    assert service.max_daily_loss_rub((), policy, snapshot) == 20_000
    explicit = (PortfolioLimit("risk_policy:test", "max_daily_loss_rub", 30_000, {}),)
    assert service.max_daily_loss_rub(explicit, policy, snapshot) == 30_000


def test_decision_payload_contains_post_cost_edge_for_risk_gate() -> None:
    from agent_app.modules.risk_control.repository import FeatureVector
    from agent_app.modules.risk_control.service import RiskControlService

    service = RiskControlService()
    vector = FeatureVector(
        feature_vector_id="fv1",
        instrument_id="moex:SBER",
        horizon="intraday",
        as_of_ts="2026-05-24T09:00:00Z",
        features={
            "spread_bps": {"normalized_value": 10, "raw_value": 10},
            "estimated_slippage_bps": {"normalized_value": 5, "raw_value": 5},
        },
        coverage_ratio=1.0,
        data_quality_score=1.0,
        build_version="test",
    )

    assert service.expected_edge_after_cost_score({"expected_edge_score": 0.01}, vector) == 0.0085
    assert service.expected_edge_after_cost_score({"expected_edge_score": 0.01, "expected_edge_after_cost_score": 0.02}, vector) == 0.02
