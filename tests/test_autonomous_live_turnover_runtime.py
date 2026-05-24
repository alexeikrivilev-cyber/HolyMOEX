from __future__ import annotations

from types import SimpleNamespace

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


def test_decision_feature_numeric_prefers_normalized_value() -> None:
    from agent_app.modules.decision_engine.service import _feature_numeric

    features = {"expected_edge_score": {"normalized_value": 0.75, "raw_value": 0.12}}

    assert _feature_numeric(features, "expected_edge_score") == 0.75


def test_decision_feature_numeric_falls_back_to_raw_value() -> None:
    from agent_app.modules.decision_engine.service import _feature_numeric

    features = {"spread_bps": {"raw_value": 4.5}}

    assert _feature_numeric(features, "spread_bps") == 4.5
    assert _feature_numeric(features, "spread_bps", value_field="raw_value") == 4.5


def test_decision_feature_numeric_missing_metric_returns_default() -> None:
    from agent_app.modules.decision_engine.service import _feature_numeric

    assert _feature_numeric({}, "missing_metric", default=-1.0) == -1.0


def test_decision_feature_numeric_supports_object_like_entries() -> None:
    from agent_app.modules.decision_engine.service import _feature_numeric

    features = SimpleNamespace(
        features={
            "expected_edge_score": SimpleNamespace(normalized_value=0.61, raw_value=0.20),
            "spread_bps": SimpleNamespace(raw_value=3.0),
        }
    )

    assert _feature_numeric(features, "expected_edge_score") == 0.61
    assert _feature_numeric(features, "spread_bps") == 3.0


def test_decision_feature_numeric_rejects_non_finite_and_non_numeric_values() -> None:
    from agent_app.modules.decision_engine.service import _feature_numeric

    features = {
        "nan_metric": {"normalized_value": float("nan"), "raw_value": "bad"},
        "inf_metric": {"normalized_value": float("inf")},
        "text_metric": {"normalized_value": "not-a-number"},
        "bool_metric": {"normalized_value": True},
    }

    assert _feature_numeric(features, "nan_metric", default=9.0) == 9.0
    assert _feature_numeric(features, "inf_metric", default=9.0) == 9.0
    assert _feature_numeric(features, "text_metric", default=9.0) == 9.0
    assert _feature_numeric(features, "bool_metric", default=9.0) == 9.0


def test_decision_engine_minimal_feature_vector_does_not_raise_name_error() -> None:
    from agent_app.modules.decision_engine.repository import (
        FeatureVector,
        InMemoryDecisionEngineRepository,
        MetricWeightRule,
        PortfolioSnapshot,
        WeightsProfile,
    )
    from agent_app.modules.decision_engine.service import DecisionEngineService

    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_decision_smoke",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "expected_edge_score": {"raw_value": 0.02, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "spread_bps": {"normalized_value": 0.1, "raw_value": 5, "ttl_status": "fresh"},
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
            PortfolioSnapshot(
                "snapshot_decision_smoke",
                "arena_go_default",
                "moex_top20_manual",
                "2026-05-24T09:00:00Z",
                1_000_000,
                1_000_000,
                1_000_000,
                0,
                0,
                0,
                0,
                {"market_session_status": "open"},
            ),
        ),
    )
    job = ModuleJob(
        job_id="job_decision_smoke",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_decision_smoke", "portfolio.portfolio_snapshot:snapshot_decision_smoke"),
        config_ref="weights_test",
        run_mode="analysis_only",
        idempotency_key="idem_decision_smoke",
    )
    payload = {
        "decision_request": {
            "decision_request_id": "decision_request_smoke",
            "universe_id": "moex_top20_manual",
            "instrument_ids": ["moex:SBER"],
            "horizon": "intraday",
            "as_of_ts": "2026-05-24T09:00:00Z",
            "feature_vector_refs": ["features.feature_vector:fv_decision_smoke"],
            "portfolio_state_ref": "portfolio.portfolio_snapshot:snapshot_decision_smoke",
            "weights_profile_id": "weights_test",
            "run_mode": "analysis_only",
            "decision_mode": "normal",
        }
    }

    result = DecisionEngineService(repository=repository).process(payload, job)

    assert result.module_job_result.status in {"success", "partial_success"}
    assert not any("NameError" in error or "_feature_numeric" in error for error in result.module_job_result.errors)
    assert result.decision_records


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


def test_scheduler_off_market_blocks_heavy_jobs_but_allows_portfolio_and_monitoring(monkeypatch) -> None:
    from agent_app.scheduler import AutonomousScheduler, ScheduleEntry, SchedulerConfig

    monkeypatch.setenv("MARKET_SESSION_STATUS", "closed")
    scheduler = AutonomousScheduler(SchedulerConfig(single_scheduler_instance=True))
    decision = ScheduleEntry(
        schedule_config_id="schedule:live_autonomous:decision:1m",
        module_name="Decision Engine Module",
        source="Feature Store",
        payload_ref="schedule:live_autonomous:decision:1m",
        interval_seconds=60,
        run_mode="live_trading",
    )
    portfolio = ScheduleEntry(
        schedule_config_id="schedule:live_autonomous:portfolio_sync:1m",
        module_name="Portfolio State Module",
        source="Order Store",
        payload_ref="schedule:live_autonomous:portfolio_sync:1m",
        interval_seconds=60,
        run_mode="live_trading",
    )
    monitoring = ScheduleEntry(
        schedule_config_id="schedule:live_autonomous:monitoring:1m",
        module_name="Monitoring & Audit Module",
        source="Audit Log Store",
        payload_ref="schedule:live_autonomous:monitoring:1m",
        interval_seconds=60,
        run_mode="live_trading",
    )

    assert scheduler.market_session_status() == "closed"
    assert scheduler.skip_reason(decision, market_status="closed", runtime_phase="off_market", db_schedule=True) == "off_market_heavy_trading_loop_blocked"
    assert scheduler.skip_reason(portfolio, market_status="closed", runtime_phase="off_market", db_schedule=True) == ""
    assert scheduler.skip_reason(monitoring, market_status="closed", runtime_phase="off_market", db_schedule=True) == ""


def test_scheduler_market_unknown_blocks_live_trading_jobs(monkeypatch) -> None:
    from agent_app.scheduler import AutonomousScheduler, ScheduleEntry, SchedulerConfig

    monkeypatch.delenv("MARKET_SESSION_STATUS", raising=False)
    scheduler = AutonomousScheduler(SchedulerConfig(single_scheduler_instance=True))
    execution = ScheduleEntry(
        schedule_config_id="schedule:live_autonomous:execution:on_approved",
        module_name="Execution Engine Module",
        source="Risk Control Module",
        payload_ref="schedule:live_autonomous:execution:on_approved",
        interval_seconds=60,
        run_mode="live_trading",
    )

    assert scheduler.market_session_status() == "unknown"
    assert scheduler.skip_reason(execution, market_status="unknown", runtime_phase="degraded", db_schedule=True) == "market_session_unknown_live_loop_blocked"


def test_raw_text_fallback_disabled_in_production_and_throttled_when_enabled(monkeypatch) -> None:
    from agent_app.scheduler import AutonomousScheduler, ScheduleEntry, SchedulerConfig

    raw_text = ScheduleEntry(
        schedule_config_id="fallback:Raw Text Store",
        module_name="Data Intake & Routing Module",
        source="Raw Text Store",
        payload_ref="",
        interval_seconds=1800,
        run_mode="live_trading",
    )
    monkeypatch.setenv("APP_ENV", "production")
    scheduler = AutonomousScheduler(SchedulerConfig(single_scheduler_instance=True, allow_llm_fallback=False))
    assert scheduler.skip_reason(raw_text, market_status="open", runtime_phase="trading_session", db_schedule=False) == "raw_text_fallback_disabled_in_production"

    scheduler = AutonomousScheduler(
        SchedulerConfig(single_scheduler_instance=True, allow_llm_fallback=True, raw_text_fallback_interval_seconds=60)
    )
    assert scheduler.skip_reason(raw_text, market_status="open", runtime_phase="trading_session", db_schedule=False) == "raw_text_fallback_interval_too_low"

    scheduler = AutonomousScheduler(
        SchedulerConfig(single_scheduler_instance=True, allow_llm_fallback=True, raw_text_fallback_interval_seconds=1800)
    )
    assert scheduler.skip_reason(raw_text, market_status="open", runtime_phase="trading_session", db_schedule=False) == ""


def test_scheduled_payload_ref_does_not_replace_autonomous_cycle_refs() -> None:
    from agent_app.modules.orchestration.repository import InMemoryOrchestrationRepository
    from agent_app.modules.orchestration.service import (
        IncomingTrigger,
        OrchestrationInput,
        OrchestrationService,
        PipelineContext,
    )

    service = OrchestrationService(InMemoryOrchestrationRepository())
    request = OrchestrationInput(
        schedule_config_ref=None,
        dependency_graph_ref=None,
        incoming_trigger=IncomingTrigger(
            trigger_type="scheduled",
            source_module="Raw Market Data Store",
            payload_ref="schedule:derivatives_positioning:daily",
        ),
        system_mode="paper_trading",
    )

    refs = service._input_refs(request, PipelineContext(universe_id="moex_top20_manual"))

    assert "schedule:derivatives_positioning:daily" in refs
    assert "raw_market.raw_candle:scheduled" in refs
    assert "features.feature_vector:latest" in refs


def test_manual_store_trigger_without_payload_ref_gets_autonomous_cycle_refs() -> None:
    from agent_app.modules.orchestration.repository import InMemoryOrchestrationRepository
    from agent_app.modules.orchestration.service import (
        IncomingTrigger,
        OrchestrationInput,
        OrchestrationService,
        PipelineContext,
    )

    service = OrchestrationService(InMemoryOrchestrationRepository())
    request = OrchestrationInput(
        schedule_config_ref=None,
        dependency_graph_ref=None,
        incoming_trigger=IncomingTrigger(
            trigger_type="manual",
            source_module="Raw Market Data Store",
            payload_ref="",
        ),
        system_mode="paper_trading",
    )

    refs = service._input_refs(request, PipelineContext(universe_id="moex_top20_manual"))

    assert "raw_market.raw_candle:scheduled" in refs
    assert "features.feature_vector:latest" in refs


def test_analysis_only_pipeline_skips_execution_engine() -> None:
    from agent_app.modules.orchestration.repository import InMemoryOrchestrationRepository
    from agent_app.modules.orchestration.service import (
        IncomingTrigger,
        OrchestrationInput,
        OrchestrationService,
        PipelineContext,
    )

    service = OrchestrationService(InMemoryOrchestrationRepository(), execute_jobs=False)
    request = OrchestrationInput(
        schedule_config_ref=None,
        dependency_graph_ref=None,
        incoming_trigger=IncomingTrigger(
            trigger_type="manual",
            source_module="Raw Market Data Store",
            payload_ref="",
        ),
        system_mode="analysis_only",
    )
    context = PipelineContext(
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
    )

    run = service.run_pipeline(request, context)

    assert "Execution Engine Module" in run.skipped_modules
    assert "Execution Engine Module" not in run.critical_path
    assert all(job.module_name != "Execution Engine Module" for job in run.created_jobs)


def test_autonomous_default_payloads_cover_text_and_fundamental_modules() -> None:
    from agent_app.contracts.unified_objects import ModuleJob, TimeRange
    from agent_app.modules.orchestration.repository import InMemoryOrchestrationRepository
    from agent_app.modules.orchestration.service import OrchestrationService, PipelineContext

    service = OrchestrationService(InMemoryOrchestrationRepository())
    context = PipelineContext(universe_id="moex_top20_manual")
    base = {
        "contour": "event_contour",
        "trigger_type": "scheduled",
        "universe_id": "moex_top20_manual",
        "instrument_ids": ("moex:SBER",),
        "horizons": ("swing",),
        "time_range": TimeRange.instant(),
        "input_refs": service._autonomous_cycle_refs(context),
        "config_ref": "runtime_config:paper_trading:v1",
        "run_mode": "paper_trading",
        "idempotency_key": "idem",
    }

    for module_name, expected_key in (
        ("Data Intake & Routing Module", "intake_request"),
        ("Event & News Intelligence Module", "event_news_input"),
        ("Earnings & Dividend Intelligence Module", "earnings_dividend_input"),
        ("Fundamental & Valuation Module", "fundamental_input"),
    ):
        job = ModuleJob(job_id=f"job_{module_name}", module_name=module_name, **base)
        payload = service._autonomous_default_payload(job, context)
        assert expected_key in payload


def test_postgres_orchestration_dependency_graph_query_handles_default_ref(monkeypatch) -> None:
    from agent_app.modules.orchestration.repository import PostgresOrchestrationRepository

    executed: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str, params: tuple[str, ...]) -> None:
            executed["query"] = query
            executed["params"] = params

        def fetchone(self) -> None:
            return None

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> FakeCursor:
            return FakeCursor()

    repository = PostgresOrchestrationRepository("postgresql://example")
    monkeypatch.setattr(repository, "_connect", lambda: FakeConnection())

    repository.load_dependency_graph(None)

    assert executed["params"] == ()
    assert "%s IS NULL" not in str(executed["query"])


def test_postgres_quality_repository_treats_scheduled_raw_refs_as_missing(monkeypatch) -> None:
    from agent_app.modules.data_quality.repository import PostgresDataQualityRepository

    repository = PostgresDataQualityRepository("postgresql://example")
    monkeypatch.setattr(repository, "_connect", lambda: (_ for _ in ()).throw(AssertionError("unexpected db call")))

    assert repository.load_quality_object("raw_market.raw_candle:scheduled") is None
    assert repository.load_quality_object("raw_market.raw_index_value:IMOEX") is None
    assert repository.load_quality_object("raw_text.raw_text_item:scheduled") is None


def test_postgres_data_intake_repository_treats_scheduled_text_ref_as_missing(monkeypatch) -> None:
    from agent_app.modules.data_intake_routing.repository import PostgresDataIntakeRoutingRepository

    repository = PostgresDataIntakeRoutingRepository("postgresql://example")
    monkeypatch.setattr(repository, "_connect", lambda: (_ for _ in ()).throw(AssertionError("unexpected db call")))

    assert repository.load_raw_text_item("raw_text.raw_text_item:scheduled") is None


def test_data_intake_postgres_timestamp_accepts_rss_pubdate() -> None:
    from agent_app.modules.data_intake_routing.repository import _optional_timestamp

    parsed = _optional_timestamp("Sun, 24 May 2026 14:06:32 +0300")

    assert parsed is not None
    assert parsed.isoformat().startswith("2026-05-24T11:06:32")


def test_data_intake_migration_allows_source_missing_endpoint_skip_reason() -> None:
    from pathlib import Path

    migration = Path("agent_app/storage/postgres/migrations/013_predfinal_runtime_hardening.sql").read_text()
    assert "source_missing_endpoint" in migration
    assert "scheduled_external_news_discovery_item_skip_reason_code_check" in migration


def test_database_readiness_accepts_final_metric_weight_check_count() -> None:
    from pathlib import Path

    migration = Path("agent_app/storage/postgres/migrations/013_predfinal_runtime_hardening.sql").read_text()
    assert "CREATE OR REPLACE VIEW audit.database_readiness_check" in migration
    assert "metric_weight_seed.total_checks >= 5" in migration


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


def test_arena_go_positions_use_exact_bot_name_and_url_encoding() -> None:
    from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, RetryPolicy
    from agent_app.modules.external_request_gateway.providers import ProviderRequestNormalizer
    from agent_app.modules.external_request_gateway.repository import default_provider_configs

    normalizer = ProviderRequestNormalizer(env={"ARENA_GO_BASE_URL": "https://arenago.ru/api", "ARENA_GO_PORTFOLIO": "arena_go_default", "ARENA_GO_BOT_NAME": "ROMASHKA exact"})
    request = ExternalRequest(
        request_id="req",
        caller_module="Portfolio State Module",
        provider="arena_go",
        request_type="get_positions",
        payload={"portfolio": "arena_go_default", "bot": "ROMASHKA exact"},
        cache_policy=CachePolicy(use_cache=False),
        retry_policy=RetryPolicy(),
        idempotency_key="idem",
    )

    http_request = normalizer.normalize(request, default_provider_configs()["arena_go"])

    assert http_request.method == "GET"
    assert http_request.url.endswith("/positions/ROMASHKA%20exact")


def test_portfolio_sync_resolves_arena_go_portfolio_from_bots_name() -> None:
    from agent_app.contracts.unified_objects import ExternalResponse
    from agent_app.modules.portfolio_state.repository import InMemoryPortfolioStateRepository
    from agent_app.modules.portfolio_state.service import PortfolioStateService

    seen: list[tuple[str, str]] = []

    class Gateway:
        def process(self, external_request):
            seen.append((external_request.request_type, external_request.payload.get("portfolio", "")))
            data = {
                "get_bots": {"bots": [{"name": "ROMASHKA_misis_guap_udgu_izhgtu", "cash_balance": 1_000_000}]},
                "get_positions": {"positions": []},
                "get_trades": {"trades": []},
            }[external_request.request_type]
            return ExternalResponse(
                request_id=external_request.request_id,
                    provider="arena_go",
                    status="success",
                    data_ref=f"request_logs.external_response:{external_request.request_id}",
                    received_at="2026-05-24T09:00:00Z",
                    latency_ms=1,
                    data=data,
                )

    service = PortfolioStateService(
        repository=InMemoryPortfolioStateRepository(),
        gateway=Gateway(),
        config={"arena_go_portfolio": "old_placeholder", "arena_go_bot_name": "ROMASHKA_misis_guap_udgu_izhgtu"},
    )
    result = service.process(
        {
            "portfolio_update_request": {
                "portfolio_id": "ROMASHKA_misis_guap_udgu_izhgtu",
                "fill_report_refs": [],
                "broker_snapshot_ref": "broker:seed",
                "price_snapshot_ref": "price:seed",
                "run_mode": "live_trading",
                "as_of_ts": "2026-05-24T09:00:00Z",
            }
        },
        _job(run_mode="live_trading"),
    )

    assert result.module_job_result.status == "success"
    assert ("get_positions", "ROMASHKA_misis_guap_udgu_izhgtu") in seen
    assert ("get_trades", "ROMASHKA_misis_guap_udgu_izhgtu") in seen
    assert result.portfolio_snapshot is not None
    assert result.portfolio_snapshot.cash == 1_000_000


def test_polza_models_response_normalizes_when_models_endpoint_exists() -> None:
    from agent_app.contracts.unified_objects import ExternalRequest
    from agent_app.modules.external_request_gateway.providers import ProviderHttpResponse, normalize_provider_response

    request = ExternalRequest(
        request_id="req_models",
        caller_module="External Request Gateway Module",
        provider="polza_ai",
        request_type="models",
        payload={},
        idempotency_key="idem_models",
    )

    status, data, warnings, errors = normalize_provider_response(
        request,
        ProviderHttpResponse(status_code=200, body={"data": [{"id": "deepseek/deepseek-v4-pro"}]}),
    )

    assert status == "success"
    assert data["models"][0]["id"] == "deepseek/deepseek-v4-pro"
    assert warnings == ()
    assert errors == ()


def test_polza_task_model_routing_and_llm_cache_key(monkeypatch) -> None:
    from agent_app.modules.event_news_intelligence.repository import RawTextItem
    from agent_app.modules.event_news_intelligence.service import EventNewsInput, EventNewsIntelligenceService, polza_model_for_task

    monkeypatch.delenv("POLZA_LLM_MODEL", raising=False)
    assert polza_model_for_task("event_extraction") == "deepseek/deepseek-v4-flash"
    assert polza_model_for_task("report_extraction") == "qwen/qwen3.6-35b-a3b"

    service = EventNewsIntelligenceService()
    event_input = EventNewsInput(
        routing_message_refs=(),
        raw_text_refs=("raw_text.raw_text_item:news1",),
        instrument_ids=("moex:SBER",),
        event_ontology_version="event_ontology:v1",
        llm_prompt_version="prompt:v2",
        market_reaction_window=("1h",),
    )
    job = _job(module_name="Event & News Intelligence Module", run_mode="live_trading")

    news = RawTextItem(
        raw_text_item_id="news1",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source="rbc_news",
        source_type="news_api",
        title="SBER announces operational update",
        body="Short news text",
        fetched_at="2026-05-24T09:00:00Z",
        content_hash="hash_news",
    )
    report = RawTextItem(
        raw_text_item_id="report1",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source="issuer_report",
        source_type="issuer_disclosure",
        title="SBER IFRS report",
        body="Long annual financial report",
        fetched_at="2026-05-24T09:05:00Z",
        content_hash="hash_report",
        source_payload={"llm_task_type": "report_extraction"},
    )

    news_request = service.create_llm_request(news, event_input, job)
    report_request = service.create_llm_request(report, event_input, job)

    assert news_request.payload["model"] == "deepseek/deepseek-v4-flash"
    assert report_request.payload["model"] == "qwen/qwen3.6-35b-a3b"
    assert "No buy/sell advice" in news_request.payload["messages"][0]["content"]
    assert news_request.payload["prompt_version"] == "prompt:v2"
    assert news_request.payload["content_hash"] == "hash_news"

    same_content_new_job = service.create_llm_request(
        RawTextItem(
            raw_text_item_id="news2",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:SBER",),
            source="rbc_news",
            source_type="news_api",
            title="SBER announces operational update",
            body="Short news text",
            fetched_at="2026-05-24T10:00:00Z",
            content_hash="hash_news",
        ),
        event_input,
        ModuleJob(
            job_id="job_other",
            module_name="Event & News Intelligence Module",
            contour="event_contour",
            trigger_type="scheduled",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:SBER",),
            horizons=("intraday",),
            time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T10:00:00Z"),
            input_refs=(),
            config_ref="",
            run_mode="live_trading",
            idempotency_key="different_job_id",
        ),
    )
    assert same_content_new_job.cache_key == news_request.cache_key
    changed_prompt = EventNewsInput(
        routing_message_refs=(),
        raw_text_refs=("raw_text.raw_text_item:news1",),
        instrument_ids=("moex:SBER",),
        event_ontology_version="event_ontology:v1",
        llm_prompt_version="prompt:v3",
        market_reaction_window=("1h",),
    )
    assert service.create_llm_request(news, changed_prompt, job).cache_key != news_request.cache_key


def test_llm_throttle_blocks_excess_calls_without_crashing(monkeypatch) -> None:
    from agent_app.contracts.unified_objects import ExternalRequest
    from agent_app.modules.external_request_gateway.service import ExternalRequestGatewayService

    service = ExternalRequestGatewayService()
    service._llm_call_timestamps.clear()
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_MAX_CALLS_PER_MINUTE", "1")

    request = ExternalRequest(
        request_id="req_llm_1",
        caller_module="Event & News Intelligence Module",
        provider="polza_ai",
        request_type="llm_completion",
        payload={"model": "deepseek/deepseek-v4-flash", "task_type": "event_extraction"},
        idempotency_key="idem_llm_1",
    )

    assert service.apply_llm_throttle(request) is True
    assert service.apply_llm_throttle(request) is False


def _risk_request_job() -> ModuleJob:
    return ModuleJob(
        job_id="job_risk_edge",
        module_name="Risk Control Module",
        contour="decision_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=(
            "decisions.decision_set:decision_edge",
            "portfolio.portfolio_snapshot:snapshot_edge",
            "risk.risk_policy:live_policy",
            "features.market_state_record:market",
            "features.data_quality_record:dq",
        ),
        config_ref="live_policy",
        run_mode="live_trading",
        idempotency_key="idem_risk_edge",
    )


def _risk_payload(edge_after_cost: float):
    from agent_app.modules.risk_control.repository import (
        DecisionSet,
        FeatureVector,
        InMemoryRiskControlRepository,
        InstrumentLimit,
        PortfolioLimit,
        PortfolioSnapshot,
        RiskPolicy,
    )

    policy_id = "live_policy"
    repo = InMemoryRiskControlRepository(
        decision_sets=(
            DecisionSet(
                decision_set_id="decision_edge",
                decision_request_id="request_edge",
                horizon="intraday",
                decisions=(
                    {
                        "instrument_id": "moex:SBER",
                        "action": "buy",
                        "target_quantity": 1,
                        "expected_edge_score": 0.02,
                        "expected_edge_after_cost_score": edge_after_cost,
                        "primary_reason_codes": ["turnover_mandate_urgency"],
                    },
                ),
                calculation_version="test",
                created_at="2026-05-24T09:00:00Z",
            ),
        ),
        risk_policies=(RiskPolicy(policy_id, "live", "1", "active", ("live_trading",), {"market_session_status": "open", "market_regime": "normal"}),),
        instrument_limits=(InstrumentLimit(policy_id, "moex:SBER", 1.0, 100_000, 20, {"arena_go_secid": "SBER"}),),
        portfolio_limits=(
            PortfolioLimit(policy_id, "min_expected_edge_after_cost_score", 0.01, {}),
            PortfolioLimit(policy_id, "max_portfolio_exposure_pct", 1.0, {}),
            PortfolioLimit(policy_id, "max_sector_exposure_pct", 1.0, {}),
            PortfolioLimit(policy_id, "max_daily_loss_rub", 50_000, {}),
            PortfolioLimit(policy_id, "max_drawdown_limit", 0.5, {}),
            PortfolioLimit(policy_id, "max_allowed_slippage_bps", 20, {}),
            PortfolioLimit(policy_id, "arena_go_daily_trade_limit", 1000, {}),
            PortfolioLimit(policy_id, "total_risk_budget", 1.0, {}),
            PortfolioLimit(policy_id, "used_risk_budget", 0.0, {}),
            PortfolioLimit(policy_id, "min_data_quality_score", 0.5, {}),
            PortfolioLimit(policy_id, "portfolio_snapshot_ttl_seconds", 300, {}),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot(
                "snapshot_edge",
                "arena_go_default",
                "moex_top20_manual",
                "2026-05-24T09:00:00Z",
                1_000_000,
                1_000_000,
                1_000_000,
                0,
                0,
                0,
                0,
                {"market_session_status": "open", "market_regime": "normal"},
            ),
        ),
        feature_vectors=(
            FeatureVector(
                "fv_edge",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "latest_price": {"raw_value": 250, "ttl_status": "fresh"},
                    "spread_bps": {"raw_value": 4, "ttl_status": "fresh"},
                    "estimated_slippage_bps": {"raw_value": 3, "ttl_status": "fresh"},
                    "market_session_status": {"value": "open", "ttl_status": "fresh"},
                    "market_regime": {"value": "normal", "ttl_status": "fresh"},
                    "arena_go_secid": {"value": "SBER", "ttl_status": "fresh"},
                },
                1.0,
                1.0,
                "test",
            ),
        ),
    )
    payload = {
        "risk_check_request": {
            "decision_set_id": "decision_edge",
            "portfolio_state_ref": "portfolio.portfolio_snapshot:snapshot_edge",
            "risk_policy_id": policy_id,
            "market_state_ref": "features.market_state_record:market",
            "data_quality_report_ref": "features.data_quality_record:dq",
            "run_mode": "live_trading",
        }
    }
    return repo, payload


def test_turnover_behind_negative_edge_is_rejected_by_risk() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(edge_after_cost=-0.01)
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "rejected"
    assert "expected_edge_after_cost_below_threshold" in result.risk_check_result.risk_flags
    assert "turnover_trade_without_positive_edge" in result.risk_check_result.risk_flags
    assert result.order_intents == ()


def test_turnover_behind_positive_edge_is_approved_when_risk_ok() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(edge_after_cost=0.02)
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved"
    assert len(result.order_intents) == 1
    assert result.order_intents[0].payload["risk_metrics"]["expected_edge_after_cost_score"] == 0.02
    assert result.order_intents[0].payload["generated_by"] == "agent"
    assert result.order_intents[0].payload["decision_set_id"] == "decision_edge"
    assert result.order_intents[0].payload["risk_check_id"].startswith("risk_check_")
    assert result.order_intents[0].payload["run_mode"] == "live_trading"


def test_live_execution_requires_explicit_safe_live_submit(monkeypatch) -> None:
    from agent_app.modules.execution_engine.repository import InMemoryExecutionEngineRepository
    from agent_app.modules.execution_engine.service import ExecutionEngineService

    monkeypatch.delenv("SAFE_LIVE_SUBMIT", raising=False)
    repo = InMemoryExecutionEngineRepository(
        order_intents=(
            {
                "order_intent_id": "order_live",
                "instrument_id": "SBER",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "limit_price": 250,
                "time_in_force": "day",
                "max_slippage_bps": 10,
                "execution_ttl_seconds": 300,
                "decision_set_id": "decision",
                "risk_check_id": "risk",
                "run_mode": "live_trading",
                "created_at": "2026-05-24T09:00:00Z",
                "payload": {"market_session_status": "open"},
            },
        ),
        risk_check_results=(
            {
                "risk_check_id": "risk",
                "decision_set_id": "decision",
                "status": "approved",
                "approved_order_intents": ["orders.order_intent:order_live"],
                "checked_at": "2026-05-24T09:00:00Z",
                "payload": {"market_session_status": "open"},
            },
        ),
        instrument_profiles=(
            {
                "instrument_id": "SBER",
                "universe_id": "moex_top20_manual",
                "ticker": "SBER",
                "lot_size": 1,
                "tradable": True,
                "execution_enabled": True,
                "arena_go_secid": "SBER",
                "arena_go_quantity_mode": "shares",
            },
        ),
        portfolio_snapshots=(
            {
                "portfolio_snapshot_id": "snap",
                "portfolio_id": "arena_go_default",
                "universe_id": "moex_top20_manual",
                "as_of_ts": "2026-05-24T09:00:00Z",
                "cash": 1_000_000,
                "equity": 1_000_000,
                "payload": {"market_session_status": "open"},
            },
        ),
    )
    service = ExecutionEngineService(repository=repo)
    job = ModuleJob(
        job_id="job_execution_live",
        module_name="Execution Engine Module",
        contour="execution_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("orders.order_intent:order_live", "market:open", "execution_policy:default"),
        config_ref="execution_policy:default",
        run_mode="live_trading",
        idempotency_key="idem_execution_live",
    )

    result = service.process(
        {
            "execution_request": {
                "order_intent_refs": ["orders.order_intent:order_live"],
                "market_session_status_ref": "market:open",
                "execution_policy_id": "execution_policy:default",
                "run_mode": "live_trading",
                "idempotency_key": "idem_execution_live",
            }
        },
        job,
    )

    assert result.execution_results[0].status == "rejected"
    assert "safe_live_submit_disabled" in result.execution_results[0].errors


def test_market_closed_blocks_execution_submit_even_when_safe_live_enabled(monkeypatch) -> None:
    from agent_app.modules.execution_engine.repository import InMemoryExecutionEngineRepository
    from agent_app.modules.execution_engine.service import ExecutionEngineService

    class Gateway:
        called = False

        def process(self, request):
            self.called = True
            raise AssertionError("submit_order must not be called while market is closed")

    gateway = Gateway()
    monkeypatch.setenv("SAFE_LIVE_SUBMIT", "true")
    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("LIVE_READINESS_PASSED", "true")
    repo = InMemoryExecutionEngineRepository(
        order_intents=(
            {
                "order_intent_id": "order_closed",
                "instrument_id": "SBER",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "limit_price": 250,
                "time_in_force": "day",
                "max_slippage_bps": 10,
                "execution_ttl_seconds": 300,
                "decision_set_id": "decision",
                "risk_check_id": "risk",
                "run_mode": "live_trading",
                "created_at": "2026-05-24T09:00:00Z",
                "payload": {"market_session_status": "closed"},
            },
        ),
        risk_check_results=(
            {
                "risk_check_id": "risk",
                "decision_set_id": "decision",
                "status": "approved",
                "approved_order_intents": ["orders.order_intent:order_closed"],
                "checked_at": "2026-05-24T09:00:00Z",
                "payload": {"market_session_status": "closed"},
            },
        ),
        instrument_profiles=(
            {
                "instrument_id": "SBER",
                "universe_id": "moex_top20_manual",
                "ticker": "SBER",
                "lot_size": 1,
                "tradable": True,
                "execution_enabled": True,
                "arena_go_secid": "SBER",
                "arena_go_quantity_mode": "shares",
            },
        ),
        portfolio_snapshots=(
            {
                "portfolio_snapshot_id": "snap",
                "portfolio_id": "arena_go_default",
                "universe_id": "moex_top20_manual",
                "as_of_ts": "2026-05-24T09:00:00Z",
                "cash": 1_000_000,
                "equity": 1_000_000,
                "payload": {"market_session_status": "closed"},
            },
        ),
    )
    job = ModuleJob(
        job_id="job_execution_closed",
        module_name="Execution Engine Module",
        contour="execution_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("orders.order_intent:order_closed", "market:closed", "execution_policy:default"),
        config_ref="execution_policy:default",
        run_mode="live_trading",
        idempotency_key="idem_execution_closed",
    )

    result = ExecutionEngineService(repository=repo, gateway=gateway).process(
        {
            "execution_request": {
                "order_intent_refs": ["orders.order_intent:order_closed"],
                "market_session_status_ref": "market:closed",
                "execution_policy_id": "execution_policy:default",
                "run_mode": "live_trading",
                "idempotency_key": "idem_execution_closed",
            }
        },
        job,
    )

    assert result.execution_results[0].status == "rejected"
    assert "market_session_not_open" in result.execution_results[0].errors
    assert gateway.called is False


def test_live_execution_requires_sandbox_and_startup_readiness(monkeypatch) -> None:
    from agent_app.modules.execution_engine.repository import InMemoryExecutionEngineRepository
    from agent_app.modules.execution_engine.service import ExecutionEngineService

    def _repo() -> InMemoryExecutionEngineRepository:
        return InMemoryExecutionEngineRepository(
            order_intents=(
                {
                    "order_intent_id": "order_live",
                    "instrument_id": "SBER",
                    "side": "buy",
                    "quantity": 1,
                    "order_type": "limit",
                    "limit_price": 250,
                    "time_in_force": "day",
                    "max_slippage_bps": 10,
                    "execution_ttl_seconds": 300,
                    "decision_set_id": "decision",
                    "risk_check_id": "risk",
                    "run_mode": "live_trading",
                    "created_at": "2026-05-24T09:00:00Z",
                    "payload": {"market_session_status": "open"},
                },
            ),
            risk_check_results=(
                {
                    "risk_check_id": "risk",
                    "decision_set_id": "decision",
                    "status": "approved",
                    "approved_order_intents": ["orders.order_intent:order_live"],
                    "checked_at": "2026-05-24T09:00:00Z",
                    "payload": {"market_session_status": "open"},
                },
            ),
            instrument_profiles=(
                {
                    "instrument_id": "SBER",
                    "universe_id": "moex_top20_manual",
                    "ticker": "SBER",
                    "lot_size": 1,
                    "tradable": True,
                    "execution_enabled": True,
                    "arena_go_secid": "SBER",
                    "arena_go_quantity_mode": "shares",
                },
            ),
            portfolio_snapshots=(
                {
                    "portfolio_snapshot_id": "snap",
                    "portfolio_id": "arena_go_default",
                    "universe_id": "moex_top20_manual",
                    "as_of_ts": "2026-05-24T09:00:00Z",
                    "cash": 1_000_000,
                    "equity": 1_000_000,
                    "payload": {"market_session_status": "open"},
                },
            ),
        )

    job = ModuleJob(
        job_id="job_execution_live",
        module_name="Execution Engine Module",
        contour="execution_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("orders.order_intent:order_live", "market:open", "execution_policy:default"),
        config_ref="execution_policy:default",
        run_mode="live_trading",
        idempotency_key="idem_execution_live",
    )
    payload = {
        "execution_request": {
            "order_intent_refs": ["orders.order_intent:order_live"],
            "market_session_status_ref": "market:open",
            "execution_policy_id": "execution_policy:default",
            "run_mode": "live_trading",
            "idempotency_key": "idem_execution_live",
        }
    }

    monkeypatch.setenv("SAFE_LIVE_SUBMIT", "true")
    monkeypatch.setenv("ARENA_GO_SANDBOX", "false")
    monkeypatch.setenv("LIVE_READINESS_PASSED", "true")
    result = ExecutionEngineService(repository=_repo()).process(payload, job)
    assert result.execution_results[0].status == "rejected"
    assert "arena_go_sandbox_required" in result.execution_results[0].errors

    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("LIVE_READINESS_PASSED", "false")
    result = ExecutionEngineService(repository=_repo()).process(payload, job)
    assert result.execution_results[0].status == "rejected"
    assert "live_readiness_not_passed" in result.execution_results[0].errors


def test_arena_go_auth_prefers_sandbox_api_key_and_falls_back_to_local_token(monkeypatch) -> None:
    from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, RetryPolicy
    from agent_app.modules.external_request_gateway.providers import ProviderRequestNormalizer
    from agent_app.modules.external_request_gateway.repository import default_provider_configs

    config = default_provider_configs()["arena_go"]
    request = ExternalRequest(
        request_id="req_auth_test",
        caller_module="test",
        provider="arena_go",
        request_type="get_bots",
        universe_id="moex_top20_manual",
        instrument_ids=(),
        payload={},
        cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
        timeout_ms=1000,
        retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
        idempotency_key="idem_auth_test",
    )
    normalizer = ProviderRequestNormalizer(env={"SANDBOX_API_KEY": "sandbox-token", "ARENA_GO_TOKEN": "fallback-token"})
    assert normalizer.normalize(request, config).headers["Authorization"] == "sandbox-token"

    normalizer = ProviderRequestNormalizer(env={"ARENA_GO_TOKEN": "fallback-token"})
    assert normalizer.normalize(request, config).headers["Authorization"] == "fallback-token"


def test_startup_resolves_exact_arena_go_bot_from_env(monkeypatch) -> None:
    from agent_app.server_startup import _resolve_bot

    monkeypatch.setenv("ARENA_GO_BOT_NAME", "Real Bot")
    exact = _resolve_bot(({"name": "Real Bot", "cash_balance": 1000}, {"name": "Other", "cash_balance": 1}))
    assert exact.name == "Real Bot"
    assert exact.cash_balance == 1000


def test_startup_resolves_single_arena_go_bot_when_env_absent(monkeypatch) -> None:
    from agent_app.server_startup import _resolve_bot

    monkeypatch.delenv("ARENA_GO_BOT_NAME", raising=False)
    monkeypatch.delenv("ARENA_GO_PORTFOLIO", raising=False)
    identity = _resolve_bot(({"name": "ROMASHKA_misis_guap_udgu_izhgtu", "cash_balance": 1_000_000},))
    assert identity.name == "ROMASHKA_misis_guap_udgu_izhgtu"
    assert identity.cash_balance == 1_000_000


def test_automatic_live_allowed_universe_migration_contains_all_sandbox_tickers() -> None:
    from pathlib import Path

    migration = Path("agent_app/storage/postgres/migrations/014_automatic_live_sandbox_runtime.sql").read_text(encoding="utf-8")
    for ticker in ("LKOH", "SBER", "ROSN", "GAZP", "VTBR", "YDEX", "PLZL", "T", "NVTK", "X5", "GMKN", "MGNT", "ALRS", "AFLT", "CHMF", "NLMK", "MOEX", "SNGSP", "MTSS", "PIKK"):
        assert f"'{ticker}'" in migration
    assert "arena_go_quantity_mode = 'shares'" in migration
    assert "auth_value_source = 'SANDBOX_API_KEY'" in migration


def test_root_dockerfile_uses_autonomous_startup_and_data_volume() -> None:
    from pathlib import Path

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    startup = Path("scripts/start_autonomous.sh").read_text(encoding="utf-8")
    assert "SYSTEM_MODE=automatic_live_trading" in dockerfile
    assert "VOLUME [\"/data\"]" in dockerfile
    assert "scripts/start_autonomous.sh" in dockerfile
    assert "agent_app.storage.postgres.apply_migrations" in startup
    assert "agent_app.server_startup" in startup
    assert "agent_app.scheduler" in startup


def test_deploy_check_uses_dev_requirements_for_pytest() -> None:
    from pathlib import Path

    dev_requirements = Path("requirements-dev.txt").read_text(encoding="utf-8")
    deploy_check = Path("scripts/deploy_check.sh").read_text(encoding="utf-8")

    assert "pytest" in dev_requirements
    assert "requirements-dev.txt" in deploy_check
    assert "pytest not found" in deploy_check
    assert "python -m unittest discover" in deploy_check


def test_scheduler_refuses_without_redis_or_single_leader(monkeypatch) -> None:
    from agent_app.scheduler import AutonomousScheduler, SchedulerConfig

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    scheduler = AutonomousScheduler(SchedulerConfig(once=True, single_scheduler_instance=False))

    assert scheduler.run_once() == 2


def test_staging_runner_parser_defaults_are_capped() -> None:
    from agent_app.staging_runner import build_parser

    args = build_parser().parse_args(["--database-url", "postgresql://example", "--instrument-cap", "100", "--max-news-items", "1"])

    assert args.instrument_cap == 100
    assert args.max_news_items == 1
