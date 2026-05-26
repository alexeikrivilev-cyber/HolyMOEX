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


def test_scheduler_supports_direct_module_only_text_schedules() -> None:
    from agent_app.scheduler import schedule_entry_from_payload, schedule_priority

    text_entry = schedule_entry_from_payload(
        schedule_config_id="schedule:event_news:intake",
        module_name="Event & News Intelligence Module",
        payload={"source": "Raw Text Store", "interval_seconds": 1800, "direct_module_only": True},
        default_run_mode="live_trading",
    )
    trading_entry = schedule_entry_from_payload(
        schedule_config_id="schedule:live_autonomous:portfolio_sync:1m",
        module_name="Portfolio State Module",
        payload={"source": "Order Store", "interval_seconds": 60, "direct_module_only": True},
        default_run_mode="live_trading",
    )

    assert text_entry is not None
    assert text_entry.direct_module_only is True
    assert trading_entry is not None
    assert schedule_priority(trading_entry) < schedule_priority(text_entry)


def test_scheduler_off_market_blocks_heavy_jobs_but_allows_portfolio_and_monitoring(monkeypatch) -> None:
    from agent_app.scheduler import AutonomousScheduler, ScheduleEntry, SchedulerConfig

    monkeypatch.setenv("MARKET_SESSION_STATUS_OVERRIDE", "closed")
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
    orchestration = ScheduleEntry(
        schedule_config_id="schedule:orchestration:service",
        module_name="Orchestration Module",
        source="Audit Log Store",
        payload_ref="schedule:orchestration:service",
        interval_seconds=60,
        run_mode="live_trading",
    )
    market_data = ScheduleEntry(
        schedule_config_id="schedule:live_autonomous:market_data:1m",
        module_name="Market Data Metrics Module",
        source="Raw Market Data Store",
        payload_ref="schedule:live_autonomous:market_data:1m",
        interval_seconds=60,
        run_mode="live_trading",
    )
    derivatives = ScheduleEntry(
        schedule_config_id="schedule:derivatives_positioning:daily",
        module_name="Derivatives & Positioning Module",
        source="Raw Market Data Store",
        payload_ref="schedule:derivatives_positioning:daily",
        interval_seconds=86400,
        run_mode="live_trading",
    )

    assert scheduler.market_session_status() == "closed"
    assert scheduler.skip_reason(decision, market_status="closed", runtime_phase="off_market", db_schedule=True) == "off_market_heavy_trading_loop_blocked"
    assert scheduler.skip_reason(market_data, market_status="closed", runtime_phase="off_market", db_schedule=True) == "off_market_scheduled_loop_blocked"
    assert scheduler.skip_reason(derivatives, market_status="closed", runtime_phase="off_market", db_schedule=True) == "off_market_scheduled_loop_blocked"
    assert scheduler.skip_reason(orchestration, market_status="closed", runtime_phase="off_market", db_schedule=True) == "off_market_scheduled_loop_blocked"
    assert scheduler.skip_reason(portfolio, market_status="closed", runtime_phase="off_market", db_schedule=True) == ""
    assert scheduler.skip_reason(monitoring, market_status="closed", runtime_phase="off_market", db_schedule=True) == ""


def test_scheduler_live_runtime_overrides_legacy_paper_schedule(monkeypatch) -> None:
    from agent_app.scheduler import schedule_entry_from_payload

    monkeypatch.delenv("ALLOW_NON_LIVE_RUNTIME", raising=False)

    entry = schedule_entry_from_payload(
        schedule_config_id="schedule:market_context:global",
        module_name="Market Context Module",
        payload={"run_mode": "paper_trading", "frequency": "60s"},
        default_run_mode="live_trading",
    )

    assert entry is not None
    assert entry.run_mode == "live_trading"


def test_scheduler_market_unknown_blocks_live_trading_jobs(monkeypatch) -> None:
    from agent_app.scheduler import AutonomousScheduler, ScheduleEntry, SchedulerConfig

    monkeypatch.setenv("MARKET_SESSION_STATUS_OVERRIDE", "unknown")
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


def test_runtime_calendar_closes_weekends_and_opens_regular_session(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from agent_app.runtime_calendar import current_market_session

    monkeypatch.delenv("MARKET_SESSION_STATUS_OVERRIDE", raising=False)
    monkeypatch.setenv("MARKET_SESSION_SOURCE", "moex")
    sunday = current_market_session(datetime(2026, 5, 24, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")))
    monday_open = current_market_session(datetime(2026, 5, 25, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")))
    monday_after_close = current_market_session(datetime(2026, 5, 25, 19, 10, tzinfo=ZoneInfo("Europe/Moscow")))

    assert sunday.market_session_status == "closed"
    assert sunday.agent_runtime_phase == "off_market"
    assert monday_open.market_session_status == "open"
    assert monday_open.agent_runtime_phase == "trading_session"
    assert monday_after_close.market_session_status == "postmarket"


def test_runtime_calendar_uses_arena_go_extended_session_for_sandbox(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from agent_app.runtime_calendar import current_market_session

    monkeypatch.delenv("MARKET_SESSION_STATUS_OVERRIDE", raising=False)
    monkeypatch.setenv("MARKET_SESSION_SOURCE", "auto")
    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    session = current_market_session(datetime(2026, 5, 25, 21, 30, tzinfo=ZoneInfo("Europe/Moscow")))

    assert session.market_session_status == "open"
    assert session.agent_runtime_phase == "trading_session"
    assert "arena_go" in session.reason
    assert "extended_session" in session.reason


def test_runtime_calendar_uses_recent_arena_go_provider_probe(monkeypatch) -> None:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    import agent_app.arena_go_session_probe as probe_module
    from agent_app.runtime_calendar import current_market_session

    monkeypatch.delenv("MARKET_SESSION_STATUS_OVERRIDE", raising=False)
    monkeypatch.setenv("MARKET_SESSION_SOURCE", "auto")
    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("ARENA_GO_SESSION_PROBE_ENABLED", "true")
    monkeypatch.setenv("ARENA_GO_SESSION_PROBE_REQUIRED_FOR_OPEN", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    def fake_recent_probe(_database_url: str, *, max_age_seconds: float):
        assert max_age_seconds > 0
        return {
            "market_session_status": "open",
            "reason": "arena_go_broker_session_probe_success",
            "created_at": datetime.now(UTC),
        }

    monkeypatch.setattr(probe_module, "read_recent_arena_go_session_probe", fake_recent_probe)

    session = current_market_session(datetime(2026, 5, 25, 8, 30, tzinfo=ZoneInfo("Europe/Moscow")))

    assert session.market_session_status == "open"
    assert session.agent_runtime_phase == "trading_session"
    assert session.reason == "arena_go_broker_session_probe_success"


def test_runtime_calendar_probe_required_blocks_time_only_open(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from agent_app.runtime_calendar import current_market_session

    monkeypatch.delenv("MARKET_SESSION_STATUS_OVERRIDE", raising=False)
    monkeypatch.setenv("MARKET_SESSION_SOURCE", "auto")
    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("ARENA_GO_SESSION_PROBE_ENABLED", "true")
    monkeypatch.setenv("ARENA_GO_SESSION_PROBE_REQUIRED_FOR_OPEN", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    session = current_market_session(datetime(2026, 5, 25, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")))

    assert session.market_session_status == "unknown"
    assert session.agent_runtime_phase == "degraded"
    assert session.reason == "arena_go_session_probe_missing_or_stale"


def test_scheduler_uses_arena_go_probe_before_time_calendar(monkeypatch) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import agent_app.scheduler as scheduler_module
    from agent_app.scheduler import AutonomousScheduler, SchedulerConfig

    monkeypatch.delenv("MARKET_SESSION_STATUS_OVERRIDE", raising=False)
    monkeypatch.setenv("MARKET_SESSION_SOURCE", "auto")
    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("ARENA_GO_SESSION_PROBE_ENABLED", "true")
    monkeypatch.setenv("ARENA_GO_SESSION_PROBE_REQUIRED_FOR_OPEN", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    def fake_probe(_database_url: str, *, min_interval_seconds: float):
        assert min_interval_seconds == 120.0
        return SimpleNamespace(
            market_session_status="open",
            reason="arena_go_broker_session_probe_success",
            source="arena_go_session_probe",
            as_of=datetime.now(UTC),
            bot_name="bot",
            warnings=(),
            errors=(),
            from_cache=False,
        )

    monkeypatch.setattr(scheduler_module, "maybe_probe_arena_go_session", fake_probe)

    scheduler = AutonomousScheduler(SchedulerConfig(single_scheduler_instance=True))

    assert scheduler.market_session_status() == "open"


def test_arena_go_extended_session_keeps_recent_moex_market_features_usable(monkeypatch) -> None:
    from agent_app.modules.normalization_feature_vector.repository import FeatureRecord
    from agent_app.modules.normalization_feature_vector.service import check_ttl_status

    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("ARENA_GO_MARKET_EXTENDED_SESSION", "true")
    monkeypatch.setenv("ALLOW_ARENA_GO_EXTENDED_MARKET_DATA_GRACE", "true")
    monkeypatch.setenv("ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS", "21600")
    monkeypatch.setenv("ARENA_GO_MARKET_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("ARENA_GO_MARKET_CLOSE_TIME", "23:50")

    record = FeatureRecord(
        feature_id="feature_sber_intraday_return",
        instrument_id="moex:SBER",
        metric_name="intraday_return",
        metric_group="price",
        metric_type="derived_metric",
        raw_value=0.001,
        normalized_value=0.52,
        unit="ratio",
        horizon="intraday",
        contour="realtime_contour",
        timestamp="2026-05-25T15:50:00Z",
        ttl_seconds=300,
        confidence_score=0.8,
        source_module="Market Data Metrics Module",
        source_refs=("raw_market.raw_candle:SBER",),
        calculation_version="test",
        quality_flags=(),
        payload={},
    )

    assert check_ttl_status(record, "2026-05-25T19:05:00Z") == "fresh"


def test_arena_go_extended_session_expands_pipeline_lookback(monkeypatch) -> None:
    from datetime import datetime, timezone

    from agent_app.main import _arena_go_extended_lookback_minutes

    monkeypatch.setenv("ARENA_GO_SANDBOX", "true")
    monkeypatch.setenv("ARENA_GO_MARKET_EXTENDED_SESSION", "true")
    monkeypatch.setenv("ALLOW_ARENA_GO_EXTENDED_MARKET_DATA_GRACE", "true")
    monkeypatch.setenv("ARENA_GO_MARKET_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("MOEX_MARKET_CLOSE_TIME", "18:50")
    monkeypatch.setenv("ARENA_GO_MARKET_CLOSE_TIME", "23:50")
    monkeypatch.setenv("ARENA_GO_EXTENDED_LOOKBACK_MINUTES", "480")

    now = datetime(2026, 5, 25, 19, 35, tzinfo=timezone.utc)

    assert _arena_go_extended_lookback_minutes(now, 240) == 480


def test_moex_daily_candle_end_uses_moscow_offset_when_naive() -> None:
    from agent_app.modules.external_request_gateway.repository import _timestamp_text

    assert _timestamp_text("2026-05-25 22:38:55", default_utc_offset="+03:00") == "2026-05-25T19:38:55Z"


def test_moex_intraday_candle_begin_uses_moscow_offset_when_naive() -> None:
    from agent_app.modules.external_request_gateway.repository import _timestamp_from_item

    item = {"begin": "2026-05-26 10:25:00", "end": "2026-05-26 10:25:59"}

    assert (
        _timestamp_from_item(
            item,
            "2026-05-26T07:26:00Z",
            ("begin",),
            default_utc_offset="+03:00",
        )
        == "2026-05-26T07:25:00Z"
    )


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


def test_live_orchestration_refreshes_downstream_job_time_range_for_current_cycle() -> None:
    from agent_app.contracts.unified_objects import TimeRange
    from agent_app.contracts.unified_objects.module_job import parse_utc_iso
    from agent_app.modules.orchestration.repository import InMemoryOrchestrationRepository
    from agent_app.modules.orchestration.service import (
        IncomingTrigger,
        OrchestrationInput,
        OrchestrationService,
        PipelineContext,
    )

    service = OrchestrationService(InMemoryOrchestrationRepository())
    context = PipelineContext(
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(
            from_ts="2026-05-26T09:00:00Z",
            to_ts="2026-05-26T09:00:00Z",
        ),
        system_mode="live_trading",
    )
    request = OrchestrationInput(
        schedule_config_ref=None,
        dependency_graph_ref=None,
        incoming_trigger=IncomingTrigger(
            trigger_type="scheduled",
            source_module="Feature Store",
            payload_ref="features.feature_record:current",
        ),
        system_mode="live_trading",
    )

    job = service.build_module_job(
        "Normalization & Feature Vector Module",
        request,
        context,
        ("moex:SBER",),
    )
    payload = service._autonomous_default_payload(job, context)

    assert parse_utc_iso(job.time_range.to_ts) >= parse_utc_iso(context.time_range.to_ts)
    assert payload["normalization_input"]["as_of_ts"] == job.time_range.to_ts


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


def test_data_intake_does_not_store_polza_llm_response_as_raw_text() -> None:
    from agent_app.contracts.unified_objects import ExternalResponse
    from agent_app.modules.data_intake_routing.service import DataIntakeRequest, DataIntakeRoutingService

    service = DataIntakeRoutingService()
    request = DataIntakeRequest(
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source_types=("rbc_news",),
        discovery_mode="scheduled",
        per_instrument_discovery=True,
        time_range={"from_ts": "2026-05-24T09:00:00Z", "to_ts": "2026-05-24T09:05:00Z"},
        routing_targets=("Event & News Intelligence Module",),
    )
    response = ExternalResponse(
        request_id="polza_response_not_source",
        provider="polza_ai",
        status="success",
        data_ref="request_logs.external_response:polza_response_not_source",
        received_at="2026-05-24T09:01:00Z",
        latency_ms=1,
        data={"items": [{"source_type": "polza_ai", "title": "LLM envelope", "body": "{}"}]},
    )

    payloads, warnings = service._raw_payloads_from_external_response(response, request)

    assert payloads == []
    assert warnings == ["llm_response_not_raw_text_source:polza_response_not_source"]


def test_data_intake_skips_polza_raw_payload_before_validation() -> None:
    from agent_app.modules.data_intake_routing.service import DataIntakeRequest, DataIntakeRoutingService

    service = DataIntakeRoutingService()
    request = DataIntakeRequest(
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source_types=("rbc_news",),
        discovery_mode="scheduled",
        per_instrument_discovery=True,
        time_range={"from_ts": "2026-05-24T09:00:00Z", "to_ts": "2026-05-24T09:05:00Z"},
        routing_targets=("Event & News Intelligence Module",),
    )

    payload, warnings = service._raw_payload_with_valid_source(
        {"source_type": "polza_ai", "title": "LLM output", "body": "{}", "model_id": "qwen/qwen3.6-35b-a3b"},
        request,
    )

    assert payload is None
    assert warnings == ("llm_raw_text_payload_skipped:polza_ai",)


def test_data_intake_repository_ignores_legacy_polza_raw_text_rows() -> None:
    from agent_app.modules.data_intake_routing.repository import _raw_text_item_from_row

    row = (
        "00000000-0000-0000-0000-000000000001",
        "moex_top20_manual",
        ["moex:SBER"],
        "polza_ai",
        "polza_ai",
        None,
        "LLM output",
        "{}",
        "en",
        None,
        None,
        "hash",
        {"source_type": "polza_ai"},
        None,
        None,
        None,
        None,
        None,
        0.0,
        [],
        [],
        [],
    )

    assert _raw_text_item_from_row(row) is None


def test_data_intake_repository_maps_legacy_controlled_staging_rows() -> None:
    from agent_app.modules.data_intake_routing.repository import _raw_text_item_from_row

    row = (
        "00000000-0000-0000-0000-000000000002",
        "moex_top20_manual",
        ["moex:SBER"],
        "controlled_staging",
        "controlled_staging",
        "https://holy-moex.local/staging/test",
        "Controlled staging signal",
        "Synthetic bounded staging item.",
        "en",
        None,
        None,
        "hash_controlled",
        {"source": "controlled_staging"},
        None,
        None,
        None,
        None,
        None,
        0.0,
        [],
        [],
        [],
    )

    item = _raw_text_item_from_row(row)

    assert item is not None
    assert item.source_type == "news_api"
    assert item.source == "controlled_staging"


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
                "portfolio_id": "arena_go_default",
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
    assert result.portfolio_snapshot.portfolio_id == "ROMASHKA_misis_guap_udgu_izhgtu"
    assert result.portfolio_snapshot.cash == 1_000_000


def test_successful_live_broker_sync_refreshes_stale_previous_snapshot() -> None:
    from agent_app.contracts.unified_objects import ExternalResponse

    class Gateway:
        def process(self, external_request):
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

    repository = InMemoryPortfolioStateRepository(
        portfolio_snapshots=(
            {
                "portfolio_snapshot_id": "old_snapshot",
                "portfolio_id": "ROMASHKA_misis_guap_udgu_izhgtu",
                "universe_id": "moex_top20_manual",
                "as_of_ts": "2026-05-24T08:00:00Z",
                "initial_capital_rub": 1_000_000,
                "cash": 1_000_000,
                "equity": 1_000_000,
                "gross_exposure": 0,
                "net_exposure": 0,
                "realized_pnl": 0,
                "unrealized_pnl": 0,
                "source_module": "Portfolio State Module",
                "source_refs": (),
                "payload": {},
            },
        )
    )
    service = PortfolioStateService(
        repository=repository,
        gateway=Gateway(),
        config={"arena_go_bot_name": "ROMASHKA_misis_guap_udgu_izhgtu", "arena_go_portfolio": "ROMASHKA_misis_guap_udgu_izhgtu"},
    )

    result = service.process(
        {
            "portfolio_update_request": {
                "portfolio_id": "arena_go_default",
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
    assert result.portfolio_snapshot is not None
    assert result.portfolio_snapshot.payload["ttl_status"] == "fresh"
    assert result.portfolio_snapshot.payload["portfolio_state_stale_or_inconsistent"] is False


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
        ProviderHttpResponse(status_code=200, body={"data": [{"id": "qwen/qwen3.6-35b-a3b"}]}),
    )

    assert status == "success"
    assert data["models"][0]["id"] == "qwen/qwen3.6-35b-a3b"
    assert warnings == ()
    assert errors == ()


def test_polza_task_model_routing_and_llm_cache_key(monkeypatch) -> None:
    from agent_app.modules.event_news_intelligence.repository import RawTextItem
    from agent_app.modules.event_news_intelligence.service import EventNewsInput, EventNewsIntelligenceService, polza_model_for_task

    monkeypatch.delenv("POLZA_LLM_MODEL", raising=False)
    assert polza_model_for_task("event_extraction") == "deepseek/deepseek-v4-flash"
    assert polza_model_for_task("report_extraction") == "qwen/qwen3.6-35b-a3b"
    monkeypatch.setenv("POLZA_LLM_MODEL", "deepseek/" + "deepseek-v4" + "-pro")
    monkeypatch.delenv("POLZA_DEFAULT_MODEL", raising=False)
    assert polza_model_for_task("unknown_task") == "qwen/qwen3.6-35b-a3b"

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


def test_russian_event_news_routing_uses_reasoning_model() -> None:
    from agent_app.modules.event_news_intelligence.repository import RawTextItem
    from agent_app.modules.event_news_intelligence.service import EventNewsInput, EventNewsIntelligenceService

    service = EventNewsIntelligenceService()
    event_input = EventNewsInput(
        routing_message_refs=(),
        raw_text_refs=("raw_text.raw_text_item:ru_report",),
        instrument_ids=("moex:SBER",),
        event_ontology_version="event_ontology:v1",
        llm_prompt_version="prompt:v2",
        market_reaction_window=("1h",),
    )
    item = RawTextItem(
        raw_text_item_id="ru_report",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source="issuer_disclosure",
        source_type="issuer_disclosure",
        title="Сбер опубликовал отчёт МСФО и финансовые результаты",
        body="Совет директоров рассмотрел дивиденды и существенный факт.",
        fetched_at="2026-05-24T09:00:00Z",
        content_hash="hash_ru_report",
    )
    request = service.create_llm_request(item, event_input, _job(module_name="Event & News Intelligence Module", run_mode="live_trading"))

    assert request.payload["task_type"] == "report_extraction"
    assert request.payload["model"] == "qwen/qwen3.6-35b-a3b"


def test_earnings_russian_text_routes_to_dividend_or_report_model() -> None:
    from agent_app.modules.earnings_dividend_intelligence.repository import RawTextItem
    from agent_app.modules.earnings_dividend_intelligence.service import EarningsDividendIntelligenceService

    service = EarningsDividendIntelligenceService()
    dividend_item = RawTextItem(
        raw_text_item_id="ru_dividend",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source="issuer_disclosure",
        title="Совет директоров рекомендовал дивиденды",
        body="Собрание акционеров рассмотрит дивиденды и дату закрытия реестра.",
        fetched_at="2026-05-24T09:00:00Z",
    )
    report_item = RawTextItem(
        raw_text_item_id="ru_report",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source="issuer_disclosure",
        title="Отчёт МСФО и финансовые результаты",
        body="Компания раскрыла операционные результаты и существенный факт.",
        fetched_at="2026-05-24T09:00:00Z",
    )

    assert service.llm_task_type(dividend_item) == "dividend_extraction"
    assert service.llm_task_type(report_item) == "report_extraction"
    assert service.model_id == "qwen/qwen3.6-35b-a3b"


def test_event_news_live_llm_guard_skips_gateway_when_text_schedules_disabled() -> None:
    import os

    from agent_app.modules.event_news_intelligence.repository import RawTextItem
    from agent_app.modules.event_news_intelligence.service import EventNewsInput, EventNewsIntelligenceService

    previous = os.environ.get("ENABLE_LLM_TEXT_SCHEDULES")
    os.environ["ENABLE_LLM_TEXT_SCHEDULES"] = "false"

    class Gateway:
        def __init__(self) -> None:
            self.requests = []

        def process(self, request):
            self.requests.append(request)
            return None

    gateway = Gateway()
    service = EventNewsIntelligenceService(gateway=gateway)
    raw_item = RawTextItem(
        raw_text_item_id="news_guard",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        source="rbc_news",
        source_type="news_api",
        title="SBER ordinary news",
        body="A text item that would otherwise need LLM extraction.",
        fetched_at="2026-05-24T09:00:00Z",
        content_hash="hash_guard",
    )
    event_input = EventNewsInput(
        routing_message_refs=(),
        raw_text_refs=("raw_text.raw_text_item:news_guard",),
        instrument_ids=("moex:SBER",),
        event_ontology_version="event_ontology:v1",
        llm_prompt_version="prompt:v2",
        market_reaction_window=("1h",),
    )
    try:
        envelope, warnings = service.load_or_request_llm_envelope(
            raw_item,
            event_input,
            _job(module_name="Event & News Intelligence Module", run_mode="live_trading"),
        )
    finally:
        if previous is None:
            os.environ.pop("ENABLE_LLM_TEXT_SCHEDULES", None)
        else:
            os.environ["ENABLE_LLM_TEXT_SCHEDULES"] = previous

    assert envelope is None
    assert warnings == ("llm_text_module_disabled_in_live_runtime",)
    assert gateway.requests == []


def test_event_news_empty_items_is_valid_no_event_result() -> None:
    from agent_app.modules.event_news_intelligence.repository import InMemoryEventNewsIntelligenceRepository
    from agent_app.modules.event_news_intelligence.service import EventNewsIntelligenceService

    raw_item = {
        "raw_text_item_id": "no_event",
        "universe_id": "moex_top20_manual",
        "instrument_ids": ("moex:SBER",),
        "source": "rbc_news",
        "source_type": "news_api",
        "title": "Market digest",
        "body": "No material issuer-specific information.",
        "fetched_at": "2026-05-24T09:00:00Z",
        "content_hash": "hash_no_event",
        "source_payload": {
            "llm_output": {
                "schema_version": "event_news:v1",
                "model_id": "deepseek/deepseek-v4-flash",
                "model_version": "test",
                "task_type": "event_extraction",
                "instrument_ids": ["moex:SBER"],
                "items": [],
                "confidence_score": 0.9,
                "evidence": [],
                "reason_codes": ["no_material_event"],
                "warnings": ["no_event_found"],
            }
        },
    }
    repo = InMemoryEventNewsIntelligenceRepository(raw_text_items=(raw_item,))
    service = EventNewsIntelligenceService(repository=repo)
    job = ModuleJob(
        job_id="job_event_no_event",
        module_name="Event & News Intelligence Module",
        contour="event_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T08:00:00Z", to_ts="2026-05-24T10:00:00Z"),
        input_refs=("raw_text.raw_text_item:no_event",),
        config_ref="",
        run_mode="live_trading",
        idempotency_key="idem_event_no_event",
    )
    result = service.process(
        {
            "event_news_input": {
                "routing_message_refs": [],
                "raw_text_refs": ["raw_text.raw_text_item:no_event"],
                "instrument_ids": ["moex:SBER"],
                "event_ontology_version": "event_ontology:v1",
                "llm_prompt_version": "prompt:v2",
                "market_reaction_window": ["1h"],
            }
        },
        job,
    )

    assert result.module_job_result.status == "partial_success"
    assert result.module_job_result.events_written == 0
    assert "no_event_found" in result.module_job_result.warnings
    assert {record.event_type for record in repo.audit_records} == {"llm_no_event_found"}


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


def test_data_quality_flags_future_timestamp_as_blocking() -> None:
    from datetime import timedelta

    from agent_app.contracts.unified_objects.module_job import to_utc_iso, utc_now
    from agent_app.modules.data_quality.repository import InMemoryDataQualityRepository
    from agent_app.modules.data_quality.service import DataQualityService

    ref = "raw_market.raw_candle:future"
    future_ts = to_utc_iso(utc_now() + timedelta(days=1))
    repo = InMemoryDataQualityRepository(
        {
            ref: {
                "raw_candle_id": "future",
                "instrument_id": "moex:SBER",
                "timestamp": future_ts,
                "source_module": "MOEX ISS",
                "calculation_version": "raw",
                "ttl_seconds": 300,
            }
        }
    )
    job = ModuleJob(
        job_id="job_dq_future",
        module_name="Data Quality Module",
        contour="decision_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=(ref,),
        config_ref="",
        run_mode="live_trading",
        idempotency_key="idem_dq_future",
    )
    result = DataQualityService(repo).process(
        {
            "quality_check_request": {
                "input_refs": [ref],
                "check_level": "decision",
                "required_freshness_seconds": 300,
                "required_coverage_ratio": 1.0,
                "critical_fields": ["timestamp", "source_module", "calculation_version"],
            }
        },
        job,
    )

    assert result.data_quality_report.freshness_status == "invalid"
    assert "future_timestamp" in result.data_quality_report.quality_flags
    assert "future_timestamp_blocks_decision:1" in result.data_quality_report.blocking_errors


def test_monitoring_missing_replay_chain_is_not_false_healthy(monkeypatch) -> None:
    from agent_app.modules.monitoring_audit.repository import InMemoryMonitoringAuditRepository
    from agent_app.modules.monitoring_audit.service import MonitoringAuditService

    monkeypatch.setenv("MARKET_SESSION_STATUS_OVERRIDE", "open")
    service = MonitoringAuditService(repository=InMemoryMonitoringAuditRepository())
    job = ModuleJob(
        job_id="job_monitoring_false_green",
        module_name="Monitoring & Audit Module",
        contour="monitoring_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:05:00Z"),
        input_refs=(),
        config_ref="monitoring:default",
        run_mode="live_trading",
        idempotency_key="idem_monitoring_false_green",
    )

    result = service.process(
        {
            "monitoring_event": {
                "event_id": "event_monitoring_false_green",
                "event_type": "system",
                "severity": "info",
                "source_module": "Monitoring & Audit Module",
                "payload_ref": "audit.monitoring_record:seed",
                "created_at": "2026-05-24T09:05:00Z",
            }
        },
        job,
    )

    assert result.health_report is not None
    assert result.health_report["system_status"] != "healthy"
    assert "decision_replay_not_possible" in result.health_report["reason_codes"]
    assert "current_window_decision_chain_missing" in result.health_report["reason_codes"]


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


def _risk_payload(
    edge_after_cost: float,
    future_feature: bool = False,
    daily_turnover_mode: str | None = None,
    current_daily_turnover: float = 0.0,
    max_daily_turnover: float | None = None,
    current_quantity: float = 0.0,
    action: str = "buy",
    target_quantity: float = 1.0,
    expected_edge_score: float = 0.02,
    shorts_allowed: bool = True,
    lot_size: int | None = None,
    arena_go_submit_quantity_units: str | None = None,
    min_risk_increasing_order_value: float | None = None,
):
    from agent_app.modules.risk_control.repository import (
        DecisionSet,
        FeatureVector,
        InMemoryRiskControlRepository,
        InstrumentLimit,
        PortfolioLimit,
        PortfolioSnapshot,
        PositionState,
        RiskPolicy,
    )

    policy_id = "live_policy"
    policy_rules = {
        "market_session_status": "open",
        "market_regime": "normal",
        "arena_go_shorts_allowed": shorts_allowed,
        "decision_allow_short_selling": shorts_allowed,
    }
    if daily_turnover_mode is not None:
        policy_rules["daily_turnover_limit_mode"] = daily_turnover_mode
        policy_rules["max_daily_turnover_hard_block_enabled"] = daily_turnover_mode != "monitor_only"
    if arena_go_submit_quantity_units is not None:
        policy_rules["arena_go_submit_quantity_units"] = arena_go_submit_quantity_units
    portfolio_limits = [
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
    ]
    if max_daily_turnover is not None:
        portfolio_limits.append(
            PortfolioLimit(
                policy_id,
                "max_daily_turnover_rub",
                max_daily_turnover,
                {"limit_mode": daily_turnover_mode} if daily_turnover_mode is not None else {},
            )
        )
    if min_risk_increasing_order_value is not None:
        portfolio_limits.append(
            PortfolioLimit(
                policy_id,
                "min_risk_increasing_order_value_rub",
                min_risk_increasing_order_value,
                {},
            )
        )

    repo = InMemoryRiskControlRepository(
        decision_sets=(
            DecisionSet(
                decision_set_id="decision_edge",
                decision_request_id="request_edge",
                horizon="intraday",
                decisions=(
                    {
                        "instrument_id": "moex:SBER",
                        "action": action,
                        "target_quantity": target_quantity,
                        "expected_edge_score": expected_edge_score,
                        "expected_edge_after_cost_score": edge_after_cost,
                        "primary_reason_codes": ["turnover_mandate_urgency"],
                    },
                ),
                calculation_version="test",
                created_at="2026-05-24T09:00:00Z",
            ),
        ),
        risk_policies=(RiskPolicy(policy_id, "live", "1", "active", ("live_trading",), policy_rules),),
        instrument_limits=(
            InstrumentLimit(
                policy_id,
                "moex:SBER",
                1.0,
                100_000,
                20,
                {
                    "arena_go_secid": "SBER",
                    **({"lot_size": lot_size, "arena_go_quantity_mode": "shares"} if lot_size else {}),
                },
            ),
        ),
        portfolio_limits=tuple(portfolio_limits),
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
                {
                    "market_session_status": "open",
                    "market_regime": "normal",
                    "gross_turnover_rub_1d": current_daily_turnover,
                },
            ),
        ),
        position_states=(
            PositionState(
                "position_edge",
                "arena_go_default",
                "moex:SBER",
                "2026-05-24T09:00:00Z",
                current_quantity,
                250,
                250,
                current_quantity * 250,
                0,
                {},
            ),
        ) if current_quantity else (),
        feature_vectors=(
            FeatureVector(
                "fv_edge",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "latest_price": {
                        "raw_value": 250,
                        "ttl_status": "fresh",
                        "quality_flags": ["future_timestamp"] if future_feature else [],
                    },
                    "spread_bps": {"raw_value": 4, "ttl_status": "fresh"},
                    "estimated_slippage_bps": {"raw_value": 3, "ttl_status": "fresh"},
                    "market_session_status": {"value": "open", "ttl_status": "fresh"},
                    "market_regime": {"value": "normal", "ttl_status": "fresh"},
                    "arena_go_secid": {"value": "SBER", "ttl_status": "fresh"},
                    "_meta": {
                        "ttl_status": "invalid" if future_feature else "fresh",
                        "quality_flags": ["future_timestamp"] if future_feature else [],
                    },
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


def test_daily_turnover_soft_limit_does_not_block_positive_edge_order() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=0.02,
        daily_turnover_mode="monitor_only",
        current_daily_turnover=2_000_000,
        max_daily_turnover=1_500_000,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved"
    assert len(result.order_intents) == 1
    risk_payload = result.order_intents[0].payload
    assert "max_daily_turnover_soft_warning" in result.risk_check_result.risk_flags
    assert "max_daily_turnover_failed" not in result.risk_check_result.risk_flags
    assert risk_payload["risk_metrics"]["daily_turnover_limit_monitor_only"] == 1.0


def test_risk_rounds_small_short_to_min_executable_lot_when_edge_justifies() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.06,
        action="sell",
        target_quantity=-4,
        expected_edge_score=-0.06,
        lot_size=10,
        arena_go_submit_quantity_units="lots",
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.order_intents
    assert result.order_intents[0].quantity == 10
    assert result.order_intents[0].payload["position_effect"] == "open_short"
    assert "order_quantity_rounded_up_to_min_lot" in result.risk_check_result.risk_flags
    assert any(item["reason_code"] == "min_lot_round_up" for item in result.risk_check_result.adjustments)


def test_risk_rejects_sub_lot_short_when_post_cost_edge_is_too_weak() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.005,
        action="sell",
        target_quantity=-4,
        expected_edge_score=-0.005,
        lot_size=10,
        arena_go_submit_quantity_units="lots",
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.order_intents == ()
    assert "min_lot_edge_not_justified" in result.risk_check_result.risk_flags


def test_risk_rejects_tiny_risk_increasing_order_when_min_value_configured() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=0.06,
        expected_edge_score=0.06,
        target_quantity=1,
        min_risk_increasing_order_value=5_000,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.order_intents == ()
    assert "risk_increasing_order_value_below_minimum" in result.risk_check_result.risk_flags


def test_risk_floors_order_quantity_to_lot_multiple_before_execution() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.02,
        action="sell",
        target_quantity=-34,
        expected_edge_score=-0.02,
        lot_size=10,
        arena_go_submit_quantity_units="lots",
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.order_intents
    assert result.order_intents[0].quantity == 30
    assert any(item["reason_code"] == "lot_size_floor" for item in result.risk_check_result.adjustments)


def test_buy_order_uses_delta_to_target_quantity_not_full_target_each_cycle() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(edge_after_cost=0.02, current_quantity=1.0)
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "rejected"
    assert result.order_intents == ()
    assert "order_quantity_non_positive" in result.risk_check_result.risk_flags


def test_risk_reducing_sell_is_not_blocked_by_positive_edge_gate() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.02,
        current_quantity=10.0,
        action="sell",
        target_quantity=0.0,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved"
    assert len(result.order_intents) == 1
    assert result.order_intents[0].side == "sell"
    assert result.order_intents[0].quantity == 10.0
    assert "expected_edge_after_cost_below_threshold" not in result.risk_check_result.risk_flags
    assert "turnover_trade_without_positive_edge" not in result.risk_check_result.risk_flags


def test_short_opening_sell_order_is_supported_by_risk() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.02,
        expected_edge_score=-0.02,
        current_quantity=0.0,
        action="sell",
        target_quantity=-10.0,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved"
    assert len(result.order_intents) == 1
    assert result.order_intents[0].side == "sell"
    assert result.order_intents[0].quantity == 10.0
    assert result.order_intents[0].payload["position_effect"] == "open_short"
    metrics = result.order_intents[0].payload["risk_metrics"]
    assert metrics["instrument_exposure_after_trade"] > 0.0
    assert metrics["portfolio_exposure_after_trade"] > 0.0


def test_short_opening_is_rejected_when_provider_capability_disabled() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.02,
        expected_edge_score=-0.02,
        current_quantity=0.0,
        action="sell",
        target_quantity=-10.0,
        shorts_allowed=False,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "rejected"
    assert result.order_intents == ()
    assert "short_selling_not_supported" in result.risk_check_result.risk_flags


def test_reduce_short_order_uses_buy_to_cover_side() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=0.02,
        current_quantity=-10.0,
        action="reduce",
        target_quantity=0.0,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved"
    assert len(result.order_intents) == 1
    assert result.order_intents[0].side == "buy"
    assert result.order_intents[0].quantity == 10.0
    assert result.order_intents[0].payload["position_effect"] == "close_short"


def test_existing_long_sell_is_reduce_long_not_open_short() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(
        edge_after_cost=-0.02,
        expected_edge_score=-0.02,
        current_quantity=10.0,
        action="sell",
        target_quantity=5.0,
    )
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved"
    assert len(result.order_intents) == 1
    assert result.order_intents[0].payload["position_effect"] == "reduce_long"


def test_execution_position_effect_direction_mapping() -> None:
    from agent_app.modules.execution_engine.service import ExecutionEngineService

    service = ExecutionEngineService()

    assert service.side_for_position_effect("open_short") == "sell"
    assert service.side_for_position_effect("increase_short") == "sell"
    assert service.side_for_position_effect("close_short") == "buy"
    assert service.side_for_position_effect("reduce_short") == "buy"
    assert service.side_for_position_effect("open_long") == "buy"
    assert service.side_for_position_effect("close_long") == "sell"


def test_batch_risk_limits_rank_and_cap_new_long_fanout() -> None:
    from agent_app.modules.risk_control.repository import (
        DecisionSet,
        FeatureVector,
        InMemoryRiskControlRepository,
        InstrumentLimit,
        PortfolioLimit,
        PortfolioSnapshot,
        RiskPolicy,
    )
    from agent_app.modules.risk_control.service import RiskControlService

    policy_id = "live_policy"
    instruments = ("moex:AAA", "moex:BBB", "moex:CCC", "moex:DDD")
    edges = {
        "moex:AAA": 0.02,
        "moex:BBB": 0.05,
        "moex:CCC": 0.04,
        "moex:DDD": 0.03,
    }
    common_limits = [
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
        PortfolioLimit(policy_id, "max_risk_increasing_order_intents_per_cycle", 10, {}),
        PortfolioLimit(policy_id, "max_new_long_order_intents_per_cycle", 2, {}),
    ]
    repo = InMemoryRiskControlRepository(
        decision_sets=(
            DecisionSet(
                "decision_edge",
                "request_edge",
                "intraday",
                tuple(
                    {
                        "instrument_id": instrument_id,
                        "action": "buy",
                        "target_quantity": 10,
                        "expected_edge_score": edge,
                        "expected_edge_after_cost_score": edge,
                        "primary_reason_codes": [],
                    }
                    for instrument_id, edge in edges.items()
                ),
                "test",
                created_at="2026-05-24T09:00:00Z",
            ),
        ),
        risk_policies=(RiskPolicy(policy_id, "live", "1", "active", ("live_trading",), {"market_session_status": "open", "market_regime": "normal"}),),
        instrument_limits=tuple(InstrumentLimit(policy_id, instrument_id, 1.0, 100_000, 20, {"arena_go_secid": instrument_id.rsplit(":", 1)[-1]}) for instrument_id in instruments),
        portfolio_limits=tuple(common_limits),
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
        feature_vectors=tuple(
            FeatureVector(
                f"fv_{instrument_id}",
                instrument_id,
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "latest_price": {"raw_value": 100, "ttl_status": "fresh"},
                    "spread_bps": {"raw_value": 4, "ttl_status": "fresh"},
                    "estimated_slippage_bps": {"raw_value": 3, "ttl_status": "fresh"},
                    "market_session_status": {"value": "open", "ttl_status": "fresh"},
                    "market_regime": {"value": "normal", "ttl_status": "fresh"},
                    "arena_go_secid": {"value": instrument_id.rsplit(":", 1)[-1], "ttl_status": "fresh"},
                    "_meta": {"ttl_status": "fresh", "quality_flags": []},
                },
                1.0,
                1.0,
                "test",
            )
            for instrument_id in instruments
        ),
    )
    repo_payload = {
        "risk_check_request": {
            "decision_set_id": "decision_edge",
            "portfolio_state_ref": "portfolio.portfolio_snapshot:snapshot_edge",
            "risk_policy_id": policy_id,
            "market_state_ref": "features.market_state_record:market",
            "data_quality_report_ref": "features.data_quality_record:dq",
            "run_mode": "live_trading",
        }
    }

    result = RiskControlService(repo).process(repo_payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "approved_with_changes"
    assert [order.instrument_id for order in result.order_intents] == ["moex:BBB", "moex:CCC"]
    assert "new_long_cycle_order_limit_reached" in result.risk_check_result.risk_flags


def test_risk_prioritizes_strong_short_with_longs_by_abs_post_cost_edge() -> None:
    from agent_app.modules.risk_control.repository import PositionState
    from agent_app.modules.risk_control.service import RiskControlService

    decisions = (
        {"instrument_id": "moex:WEAK_LONG", "action": "buy", "expected_edge_after_cost_score": 0.018},
        {"instrument_id": "moex:STRONG_SHORT", "action": "sell", "expected_edge_after_cost_score": -0.035},
        {"instrument_id": "moex:STRONG_LONG", "action": "buy", "expected_edge_after_cost_score": 0.030},
        {"instrument_id": "moex:REDUCE_LONG", "action": "sell", "expected_edge_after_cost_score": -0.005},
    )
    positions = {
        "moex:REDUCE_LONG": PositionState(
            "pos_reduce",
            "portfolio",
            "moex:REDUCE_LONG",
            "2026-05-24T09:00:00Z",
            10,
            100,
            100,
            1000,
            0,
        )
    }

    ordered = RiskControlService().prioritized_decisions(decisions, positions)

    assert [item["instrument_id"] for item in ordered] == [
        "moex:REDUCE_LONG",
        "moex:STRONG_SHORT",
        "moex:STRONG_LONG",
        "moex:WEAK_LONG",
    ]


def test_decision_engine_can_choose_short_opening_sell() -> None:
    from agent_app.modules.decision_engine.service import DecisionEngineService, DecisionPolicy, DecisionRequest

    request = DecisionRequest(
        decision_request_id="decision_short",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizon="intraday",
        as_of_ts="2026-05-24T09:00:00Z",
        feature_vector_refs=("features.feature_vector:fv",),
        portfolio_state_ref="portfolio.portfolio_snapshot:snapshot",
        weights_profile_id="weights:live_autonomous:intraday:v1",
        run_mode="live_trading",
        decision_mode="normal",
    )
    service = DecisionEngineService()

    assert service.choose_action(
        request=request,
        expected_edge=-0.08,
        risk_score=0.2,
        margin=0.03,
        reason_codes=(),
        position=None,
    ) == "sell"

    live_scale_service = DecisionEngineService(
        policy=DecisionPolicy(short_entry_threshold=0.05, short_add_threshold=0.06)
    )
    assert live_scale_service.choose_action(
        request=request,
        expected_edge=-0.04,
        gross_expected_edge=-0.041,
        expected_edge_after_cost=-0.04,
        risk_score=0.2,
        margin=-0.01,
        reason_codes=(),
        position=None,
    ) == "hold"
    assert live_scale_service.choose_action(
        request=request,
        expected_edge=-0.061,
        gross_expected_edge=-0.062,
        expected_edge_after_cost=-0.061,
        risk_score=0.2,
        margin=0.011,
        reason_codes=(),
        position=None,
    ) == "sell"


def test_macro_degraded_overlay_does_not_create_short_from_flat_edge() -> None:
    from agent_app.modules.decision_engine.service import DecisionEngineService

    service = DecisionEngineService()
    overlay = {
        "edge_penalty": 0.04,
        "edge_multiplier": 0.75,
        "risk_penalty": 0.03,
        "reason_codes": ("macro_context_degraded",),
    }

    adjusted, contribution = service.apply_macro_overlay(0.01, overlay)

    assert adjusted == 0.0
    assert contribution <= 0.0


def test_turnover_on_track_does_not_add_decision_urgency() -> None:
    from agent_app.modules.decision_engine.repository import PortfolioSnapshot
    from agent_app.modules.decision_engine.service import DecisionEngineService

    snapshot = PortfolioSnapshot(
        "snapshot_turnover_on_track",
        "arena_go",
        "moex_top20_manual",
        "2026-05-24T09:00:00Z",
        1_000_000,
        1_000_000,
        1_000_000,
        0,
        0,
        0,
        0,
        {
            "turnover_mandate_enabled": True,
            "turnover_target_status": "on_track",
            "turnover_progress_ratio": 0.2,
            "remaining_turnover_rub_14d": 8_000_000,
            "target_gross_turnover_rub_14d": 10_000_000,
        },
    )

    context = DecisionEngineService().turnover_context(snapshot)

    assert context["turnover_target_status"] == "on_track"
    assert context["trade_urgency_score"] == 0.0


def test_market_context_missing_macro_uses_real_public_series_endpoints() -> None:
    from agent_app.modules.market_context.service import MarketContextInput, MarketContextService

    service = MarketContextService()
    job = ModuleJob(
        job_id="job_market_context_public_sources",
        module_name="Market Context Module",
        contour="global_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-20T00:00:00Z", to_ts="2026-05-26T00:00:00Z"),
        input_refs=(),
        config_ref="runtime_config:live_autonomous:v1",
        run_mode="live_trading",
        idempotency_key="idem_market_context_public_sources",
    )
    market_input = MarketContextInput.from_dict(
        {
            "market_context_input": {
                "universe_id": "moex_top20_manual",
                "instrument_ids": ["moex:SBER"],
                "macro_refs": ["raw_macro.raw_macro_point:scheduled"],
                "index_refs": ["index:imoex"],
                "sector_refs": [],
                "event_refs": [],
                "windows": [5, 20],
            }
        },
        job,
    )

    requests = service.create_external_requests_for_missing_raw_data(
        market_input=market_input,
        job=job,
        macro_points=(),
        index_values=(),
        candles=(),
    )
    endpoints = {str(request.payload.get("endpoint") or "") for request in requests}

    assert "https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True&UniDbQuery.From={from_ddmmyyyy_dot}&UniDbQuery.To={to_ddmmyyyy_dot}" in endpoints
    assert "https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={from_ddmmyyyy}&date_req2={to_ddmmyyyy}&VAL_NM_RQ=R01235" in endpoints
    assert "/engines/stock/markets/index/boards/SNDX/securities/IMOEX/candles.json" in endpoints
    assert all(request.payload.get("endpoint") != "/series" for request in requests)
    assert any(str(request.payload.get("time_range", {}).get("from_ts", "")).startswith("2026-01-26") for request in requests)


def test_directional_target_position_has_meaningful_minimum_size() -> None:
    from agent_app.modules.decision_engine.service import DecisionEngineService, DecisionPolicy

    service = DecisionEngineService(
        policy=DecisionPolicy(
            action_threshold=0.05,
            min_post_cost_edge_score=0.05,
            min_target_position_pct=0.012,
            target_full_edge_score=0.18,
        )
    )

    target_pct = service.directional_target_position_pct(0.06, 0.2, 0.08)

    assert 0.009 <= target_pct <= 0.08


def test_decision_feature_contribution_is_centered_for_alpha_signals() -> None:
    from agent_app.modules.decision_engine.metrics import feature_contribution

    assert feature_contribution(0.25, 1.0, "positive", 1.0) < 0
    assert feature_contribution(0.75, 1.0, "positive", 1.0) > 0
    assert feature_contribution(0.25, 1.0, "negative", 1.0) > 0
    assert feature_contribution(0.02, 1.0, "positive", 1.0, centered=False) == 0.02
    assert feature_contribution(-0.02, 1.0, "positive", 1.0, centered=False) == -0.02


def test_decision_action_selection_uses_post_cost_edge() -> None:
    from agent_app.modules.decision_engine.repository import PositionState
    from agent_app.modules.decision_engine.service import DecisionEngineService, DecisionRequest

    request = DecisionRequest(
        decision_request_id="decision_post_cost",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizon="intraday",
        as_of_ts="2026-05-24T09:00:00Z",
        feature_vector_refs=("features.feature_vector:fv",),
        portfolio_state_ref="portfolio.portfolio_snapshot:snapshot",
        weights_profile_id="weights:live_autonomous:intraday:v1",
        run_mode="live_trading",
        decision_mode="normal",
    )
    service = DecisionEngineService()

    no_position_action = service.choose_action(
        request=request,
        expected_edge=-0.01,
        gross_expected_edge=0.02,
        expected_edge_after_cost=-0.01,
        risk_score=0.2,
        margin=-0.04,
        reason_codes=(),
        position=None,
    )
    long_position_action = service.choose_action(
        request=request,
        expected_edge=-0.01,
        gross_expected_edge=0.02,
        expected_edge_after_cost=-0.01,
        risk_score=0.2,
        margin=-0.04,
        reason_codes=(),
        position=PositionState("pos", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", 10, 100, 100, 1000, 0),
    )

    assert no_position_action == "hold"
    assert long_position_action == "sell"
    assert service.post_cost_edge_score(0.02, 10) == 0.019
    assert service.post_cost_edge_score(-0.02, 10) == -0.019
    assert service.edge_to_cost_ratio(0.02, 10) == 20.0


def test_decision_exit_overlay_closes_take_profit_and_stop_loss() -> None:
    from agent_app.modules.decision_engine.repository import PositionState
    from agent_app.modules.decision_engine.service import DecisionEngineService

    service = DecisionEngineService()
    long_position = PositionState("pos_long", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", 10, 100, 102, 1020, 20)
    short_position = PositionState("pos_short", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", -10, 100, 98, -980, 20)
    losing_position = PositionState("pos_loss", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", 10, 100, 98, 980, -20)

    long_exit = service.position_exit_overlay(
        position=long_position,
        latest_price=102,
        current_quantity=10,
        expected_edge=0.0,
        risk_score=0.2,
    )
    short_exit = service.position_exit_overlay(
        position=short_position,
        latest_price=98,
        current_quantity=-10,
        expected_edge=0.0,
        risk_score=0.2,
    )
    stop_exit = service.position_exit_overlay(
        position=losing_position,
        latest_price=98,
        current_quantity=10,
        expected_edge=0.2,
        risk_score=0.2,
    )

    assert long_exit is not None and long_exit[0] == "reduce"
    assert short_exit is not None and short_exit[0] == "reduce"
    assert stop_exit is not None and stop_exit[0] == "reduce"
    assert "position_take_profit_triggered" in long_exit[3]
    assert "position_take_profit_triggered" in short_exit[3]
    assert "position_stop_loss_triggered" in stop_exit[3]


def test_long_exit_policy_is_post_cost_and_partial_take_profit_aware() -> None:
    from agent_app.modules.decision_engine.repository import PositionState
    from agent_app.modules.decision_engine.service import DecisionEngineService

    service = DecisionEngineService()
    profitable_long = PositionState("pos_long", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", 10, 100, 102, 1020, 20)
    flat_long = PositionState("pos_flat", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", 10, 100, 100, 1000, 0)

    weak_profit = service.position_exit_overlay(
        position=profitable_long,
        latest_price=102,
        current_quantity=10,
        expected_edge=0.08,
        expected_edge_after_cost=0.01,
        risk_score=0.2,
        current_pct=0.05,
    )
    strong_profit = service.position_exit_overlay(
        position=profitable_long,
        latest_price=102,
        current_quantity=10,
        expected_edge=0.10,
        expected_edge_after_cost=0.10,
        risk_score=0.2,
        current_pct=0.05,
    )
    negative_edge = service.position_exit_overlay(
        position=flat_long,
        latest_price=100,
        current_quantity=10,
        expected_edge=0.02,
        expected_edge_after_cost=-0.01,
        risk_score=0.2,
        current_pct=0.05,
    )

    assert weak_profit is not None and weak_profit[0] == "reduce"
    assert weak_profit[2] == 5.0
    assert "position_partial_take_profit_triggered" in weak_profit[3]
    assert strong_profit is None
    assert negative_edge is not None and negative_edge[0] == "reduce" and negative_edge[2] == 0.0
    assert "long_post_cost_edge_non_positive" in negative_edge[3]


def test_short_exit_policy_is_side_aware() -> None:
    from agent_app.modules.decision_engine.repository import PositionState
    from agent_app.modules.decision_engine.service import DecisionEngineService

    service = DecisionEngineService()
    profitable_short = PositionState("pos_short", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", -10, 100, 98, -980, 20)
    losing_short = PositionState("pos_short_loss", "portfolio", "moex:SBER", "2026-05-24T09:00:00Z", -10, 100, 102, -1020, -20)

    weak_profit = service.position_exit_overlay(
        position=profitable_short,
        latest_price=98,
        current_quantity=-10,
        expected_edge=-0.08,
        expected_edge_after_cost=-0.01,
        risk_score=0.2,
        current_pct=-0.05,
    )
    strong_profit = service.position_exit_overlay(
        position=profitable_short,
        latest_price=98,
        current_quantity=-10,
        expected_edge=-0.10,
        expected_edge_after_cost=-0.10,
        risk_score=0.2,
        current_pct=-0.05,
    )
    stop = service.position_exit_overlay(
        position=losing_short,
        latest_price=102,
        current_quantity=-10,
        expected_edge=-0.10,
        expected_edge_after_cost=-0.10,
        risk_score=0.2,
        current_pct=-0.05,
    )

    assert weak_profit is not None and weak_profit[0] == "reduce"
    assert weak_profit[2] == -5.0
    assert strong_profit is None
    assert stop is not None and stop[0] == "reduce" and stop[2] == 0.0


def test_decision_engine_emits_negative_short_target_when_edge_is_negative() -> None:
    from agent_app.modules.decision_engine.repository import (
        FeatureVector,
        InMemoryDecisionEngineRepository,
        MarketStateRecord,
        MetricWeightRule,
        PortfolioSnapshot,
        WeightsProfile,
    )
    from agent_app.modules.decision_engine.service import DecisionEngineService

    repository = InMemoryDecisionEngineRepository(
        feature_vectors=(
            FeatureVector(
                "fv_short",
                "moex:SBER",
                "intraday",
                "2026-05-24T09:00:00Z",
                {
                    "latest_price": {"raw_value": 250, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "weak_signal": {"normalized_value": 0.01, "raw_value": 0.01, "confidence_score": 1.0, "ttl_status": "fresh"},
                    "spread_bps": {"raw_value": 3, "ttl_status": "fresh"},
                },
                1.0,
                1.0,
                "test",
            ),
        ),
        weights_profiles=(WeightsProfile("weights_short", "short", "1", "active", "intraday", ("live_trading",)),),
        metric_weight_rules=(
            MetricWeightRule("rule_weak", "weights_short", "weak_signal", "price", "intraday", "all", (), None, 1.0, "positive", "identity", 0.0, "downweight", "test"),
        ),
        portfolio_snapshots=(
            PortfolioSnapshot("snapshot_short", "arena_go_default", "moex_top20_manual", "2026-05-24T09:00:00Z", 1_000_000, 1_000_000, 1_000_000, 0, 0, 0, 0, {"market_session_status": "open"}),
        ),
        market_state_records=(
            MarketStateRecord(
                "market_state_short",
                "moex_top20_manual",
                "2026-05-24T09:00:00Z",
                "open",
                "range",
                {"risk_on_risk_off_score": 0.1, "market_context": {"market_breadth": 0.1}, "macro_context": {}},
            ),
        ),
    )
    job = ModuleJob(
        job_id="job_short_decision",
        module_name="Decision Engine Module",
        contour="decision_contour",
        trigger_type="manual",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(from_ts="2026-05-24T09:00:00Z", to_ts="2026-05-24T09:00:00Z"),
        input_refs=("features.feature_vector:fv_short", "portfolio.portfolio_snapshot:snapshot_short"),
        config_ref="weights_short",
        run_mode="live_trading",
        idempotency_key="idem_short_decision",
    )
    payload = {
        "decision_request": {
            "decision_request_id": "decision_request_short",
            "universe_id": "moex_top20_manual",
            "instrument_ids": ["moex:SBER"],
            "horizon": "intraday",
            "as_of_ts": "2026-05-24T09:00:00Z",
            "feature_vector_refs": ["features.feature_vector:fv_short"],
            "portfolio_state_ref": "portfolio.portfolio_snapshot:snapshot_short",
            "weights_profile_id": "weights_short",
            "run_mode": "live_trading",
            "decision_mode": "normal",
        }
    }

    result = DecisionEngineService(repository=repository).process(payload, job)

    assert result.decision_records
    decision = result.decision_records[0]
    assert decision.action == "sell"
    assert decision.target_position_pct < 0
    assert decision.target_quantity < 0


def test_broker_short_position_direction_is_signed() -> None:
    from agent_app.modules.portfolio_state.service import BrokerSyncResult, PortfolioStateService, PortfolioUpdateRequest

    service = PortfolioStateService(config={"arena_go_position_units": "shares"})
    quantities: dict[str, float] = {}
    payloads: dict[str, dict[str, object]] = {}
    result = service.apply_broker_reconciliation(
        request=PortfolioUpdateRequest(
            portfolio_id="portfolio",
            fill_report_refs=(),
            broker_snapshot_ref="broker.snapshot:test",
            price_snapshot_ref="price.snapshot:test",
            run_mode="live_trading",
            as_of_ts="2026-05-24T09:00:00Z",
        ),
        cash=1_000_000.0,
        realized_pnl=0.0,
        quantities=quantities,
        average_prices={},
        payloads=payloads,
        broker_sync=BrokerSyncResult(
            status="success",
            cash_balance=1_010_000.0,
            positions=({"secid": "SBER", "position": 10, "direction": "S", "average_price": 250},),
            trades=(),
            errors=(),
            request_refs=(),
            portfolio_id="portfolio",
        ),
        previous_snapshot=None,
        lot_sizes={"moex:SBER": 1},
    )

    assert result["warnings"] == ()
    assert result["cash"] == 1_010_000.0
    assert quantities["moex:SBER"] == -10.0
    assert payloads["moex:SBER"]["broker_quantity_signed"] == -10.0
    assert payloads["moex:SBER"]["broker_quantity_shares"] == -10.0
    assert payloads["moex:SBER"]["broker_direction"] == "s"

    signed_quantities: dict[str, float] = {}
    signed_payloads: dict[str, dict[str, object]] = {}
    service.apply_broker_reconciliation(
        request=PortfolioUpdateRequest(
            portfolio_id="portfolio",
            fill_report_refs=(),
            broker_snapshot_ref="broker.snapshot:test",
            price_snapshot_ref="price.snapshot:test",
            run_mode="live_trading",
            as_of_ts="2026-05-24T09:00:00Z",
        ),
        cash=1_000_000.0,
        realized_pnl=0.0,
        quantities=signed_quantities,
        average_prices={},
        payloads=signed_payloads,
        broker_sync=BrokerSyncResult(
            status="success",
            cash_balance=1_010_000.0,
            positions=({"secid": "SBER", "position": -10, "direction": "S", "average_price": 250},),
            trades=(),
            errors=(),
            request_refs=(),
            portfolio_id="portfolio",
        ),
        previous_snapshot=None,
        lot_sizes={"moex:SBER": 1},
    )

    assert signed_quantities["moex:SBER"] == -10.0
    assert signed_payloads["moex:SBER"]["broker_quantity_signed"] == -10.0
    assert signed_payloads["moex:SBER"]["broker_quantity_shares"] == -10.0


def test_live_broker_short_snapshot_keeps_negative_quantity_and_positive_equity_basis() -> None:
    from agent_app.contracts.unified_objects import ExternalResponse

    class Gateway:
        def process(self, external_request):
            data = {
                "get_bots": {"bots": [{"name": "ROMASHKA_misis_guap_udgu_izhgtu", "cash_balance": 900_000}]},
                "get_positions": {
                    "positions": [
                        {
                            "secid": "SBER",
                            "position": -10,
                            "direction": "S",
                            "average_price": 250,
                        }
                    ]
                },
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

    repository = InMemoryPortfolioStateRepository(
        market_prices=(
            {
                "instrument_id": "moex:SBER",
                "price": 240,
                "price_ts": "2026-05-24T09:00:00Z",
                "source_ref": "raw_market.raw_trade:sber",
                "payload": {"lot_size": 10},
            },
        )
    )
    service = PortfolioStateService(
        repository=repository,
        gateway=Gateway(),
        config={
            "arena_go_bot_name": "ROMASHKA_misis_guap_udgu_izhgtu",
            "arena_go_position_units": "lots",
        },
    )

    result = service.process(
        {
            "portfolio_update_request": {
                "portfolio_id": "arena_go_default",
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
    position = next(item for item in result.positions if item.instrument_id == "moex:SBER")
    assert position.quantity == -100.0
    assert position.market_value == -24_000.0
    assert position.unrealized_pnl == 1_000.0
    assert result.portfolio_snapshot.equity == 924_000.0
    assert result.portfolio_snapshot.net_exposure < 0
    assert result.portfolio_snapshot.payload["equity_cash_accounting"] == "arena_go_cash_balance_plus_gross_positions"


def test_future_timestamp_feature_is_rejected_by_risk() -> None:
    from agent_app.modules.risk_control.service import RiskControlService

    repo, payload = _risk_payload(edge_after_cost=0.02, future_feature=True)
    result = RiskControlService(repo).process(payload, _risk_request_job())

    assert result.risk_check_result is not None
    assert result.risk_check_result.status == "rejected"
    assert "future_timestamp_blocked:feature_vector" in result.risk_check_result.risk_flags
    assert result.order_intents == ()


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
                "payload": {"market_session_status": "open", "position_effect": "open_long"},
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


def test_execution_policy_defaults_to_arena_go_lots_for_provider_submit() -> None:
    from agent_app.modules.execution_engine.service import ExecutionPolicy

    policy = ExecutionPolicy.from_mapping("execution_policy:test", {})

    assert policy.arena_go_submit_quantity_units == "lots"


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
                "payload": {"market_session_status": "closed", "position_effect": "open_long"},
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
                    "payload": {"market_session_status": "open", "position_effect": "open_long"},
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
