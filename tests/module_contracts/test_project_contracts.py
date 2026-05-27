from __future__ import annotations

import re
import unittest

from module_contract_case import MIGRATIONS_DIR, MODULES_DIR, PROJECT_ROOT, read_text


EXPECTED_MODULE_FILES = (
    "01_orchestration_module.md",
    "02_external_request_gateway_module.md",
    "03_selected_instruments_registry_module.md",
    "04_data_intake_routing_module.md",
    "05_data_quality_module.md",
    "06_market_data_metrics_module.md",
    "07_liquidity_microstructure_module.md",
    "08_volatility_risk_metrics_module.md",
    "09_market_context_module.md",
    "10_fundamental_valuation_module.md",
    "11_event_news_intelligence_module.md",
    "12_earnings_dividend_intelligence_module.md",
    "13_corporate_actions_adjustment_module.md",
    "14_derivatives_positioning_module.md",
    "15_normalization_feature_vector_module.md",
    "16_feature_validation_research_module.md",
    "17_decision_engine_module.md",
    "18_risk_control_module.md",
    "19_execution_engine_module.md",
    "20_portfolio_state_module.md",
    "21_backtesting_paper_trading_module.md",
    "22_monitoring_audit_module.md",
)

EXPECTED_SCHEMAS = (
    "registry",
    "raw_market",
    "raw_text",
    "raw_macro",
    "events",
    "features",
    "weights",
    "risk",
    "portfolio",
    "decisions",
    "orders",
    "request_logs",
    "audit",
)

ANALYTICS_VIEWS = (
    "analytics.trade_fact",
    "analytics.performance_daily",
    "analytics.decision_outcome",
    "analytics.feature_contribution",
    "analytics.llm_quality",
    "analytics.risk_gate_effectiveness",
    "analytics.decision_fact",
    "analytics.decision_outcome_30m",
    "analytics.decision_outcome_60m",
    "analytics.instrument_live_stats",
    "analytics.churn_round_trips",
    "analytics.guardrail_rejections",
    "analytics.edge_calibration",
    "analytics.pnl_by_reason_code",
    "analytics.execution_cost_realized",
    "analytics.feature_outcome_attribution",
    "analytics.live_dashboard_summary",
)

CORE_STORE_TABLES = (
    "registry.instrument_profile",
    "registry.instrument_alias",
    "registry.instrument_mapping",
    "raw_market.raw_candle",
    "raw_market.raw_trade",
    "raw_market.raw_orderbook",
    "raw_market.raw_index_value",
    "raw_text.raw_text_item",
    "raw_macro.raw_macro_point",
    "events.structured_event",
    "events.event_cluster",
    "events.event_reaction",
    "features.feature_record",
    "features.feature_vector",
    "weights.weights_profile",
    "weights.metric_weight_rule",
    "risk.risk_policy",
    "risk.instrument_limit",
    "risk.portfolio_limit",
    "portfolio.portfolio_snapshot",
    "portfolio.position_state",
    "decisions.decision_record",
    "decisions.decision_explanation",
    "orders.order_intent",
    "orders.order_status",
    "orders.fill_report",
    "request_logs.external_request_log",
    "audit.audit_record",
)

MODULE_STORE_TABLES = (
    "audit.schedule_config",
    "audit.module_dependency_graph",
    "audit.module_run",
    "request_logs.provider_config",
    "request_logs.request_cache",
    "features.data_quality_record",
    "raw_text.event_routing_message",
    "raw_market.execution_constraint",
    "risk.risk_context_record",
    "features.fundamental_snapshot",
    "events.earnings_dividend_record",
    "events.corporate_action_record",
    "raw_market.derivatives_availability",
    "features.market_state_record",
    "risk.risk_check_result",
    "orders.execution_result",
    "audit.research_report",
    "audit.monitoring_record",
)

DATA_INTAKE_DISCOVERY_TABLES = (
    "raw_text.text_source_config",
    "raw_text.scheduled_external_news_discovery_run",
    "raw_text.scheduled_external_news_discovery_item",
    "raw_text.external_text_search_request",
)

CONTROLLED_VALIDATION_REPORT_SQL = PROJECT_ROOT / "sql" / "analytics" / "034_controlled_paper_validation_report.sql"
LIVE_SMOKE_PREFLIGHT_REPORT_SQL = PROJECT_ROOT / "sql" / "analytics" / "035_live_smoke_preflight_report.sql"
GUARDED_LIVE_SMOKE_DOC = PROJECT_ROOT / "docs" / "guarded_live_smoke_test.md"

EXPECTED_TICKERS = (
    "LKOH",
    "SBER",
    "ROSN",
    "GAZP",
    "VTBR",
    "YDEX",
    "PLZL",
    "T",
    "NVTK",
    "X5",
    "GMKN",
    "MGNT",
    "ALRS",
    "AFLT",
    "CHMF",
    "NLMK",
    "MOEX",
    "SNGSP",
    "MTSS",
    "PIKK",
)


def all_migration_sql() -> str:
    return "\n".join(read_text(path) for path in sorted(MIGRATIONS_DIR.glob("*.sql")))


class ProjectDocumentationContractTests(unittest.TestCase):
    def test_all_22_module_specs_exist(self) -> None:
        actual = tuple(path.name for path in sorted(MODULES_DIR.glob("*.md")))
        self.assertEqual(actual, EXPECTED_MODULE_FILES)

    def test_each_module_has_a_dedicated_contract_test_file(self) -> None:
        test_dir = PROJECT_ROOT / "tests" / "module_contracts"
        for module_file in EXPECTED_MODULE_FILES:
            test_name = "test_" + module_file.replace(".md", ".py")
            with self.subTest(test_name=test_name):
                self.assertTrue((test_dir / test_name).exists())

    def test_postgres_migration_declares_recommended_schemas(self) -> None:
        migration = read_text(MIGRATIONS_DIR / "001_create_database.sql")
        for schema in EXPECTED_SCHEMAS:
            with self.subTest(schema=schema):
                self.assertIn(f"CREATE SCHEMA IF NOT EXISTS {schema};", migration)

    def test_postgres_migration_covers_documented_store_records(self) -> None:
        migration = all_migration_sql()
        for table in CORE_STORE_TABLES + MODULE_STORE_TABLES + DATA_INTAKE_DISCOVERY_TABLES:
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} ", migration)

    def test_database_supports_scheduled_external_news_discovery(self) -> None:
        migration = all_migration_sql()
        for token in (
            "scheduled_external_news_discovery",
            "per_instrument_discovery",
            "source_disabled",
            "instrument_not_eligible",
            "schedule:data_intake:scheduled_external_news_discovery",
            "source:news_api:text_search",
            "source:issuer_disclosure:text_search",
            "source:corporate_site:text_search",
        ):
            with self.subTest(token=token):
                self.assertIn(token, migration)

    def test_selected_universe_seed_contains_exactly_20_active_tickers(self) -> None:
        seed = read_text(MIGRATIONS_DIR / "002_seed_selected_instruments.sql")
        seeded = re.findall(r"\('moex:([^']+)'", seed)
        self.assertEqual(tuple(seeded), EXPECTED_TICKERS)
        self.assertIn("max_active_instruments", seed)
        self.assertIn("20", seed)

    def test_database_contract_files_do_not_contain_raw_secrets(self) -> None:
        checked_files = list(MIGRATIONS_DIR.glob("*.sql")) + [
            PROJECT_ROOT / "docker" / "docker-compose.example.yml"
        ]
        secret_pattern = re.compile(r"(pza_[A-Za-z0-9_-]+|[a-f0-9]{64,})")
        for path in checked_files:
            with self.subTest(path=path.name):
                self.assertIsNone(secret_pattern.search(read_text(path)))

    def test_analytics_views_are_read_only_and_cover_live_learning_loop(self) -> None:
        migration = "\n".join(
            read_text(MIGRATIONS_DIR / migration_name)
            for migration_name in (
                "032_analytics_performance_views.sql",
                "033_live_validation_analytics_views.sql",
            )
        )
        self.assertIn("CREATE SCHEMA IF NOT EXISTS analytics;", migration)
        for view_name in ANALYTICS_VIEWS:
            with self.subTest(view=view_name):
                self.assertIn(f"CREATE OR REPLACE VIEW {view_name} AS", migration)
        self.assertIn("orders.execution_result", migration)
        self.assertIn("decisions.decision_record", migration)
        self.assertIn("portfolio.portfolio_snapshot", migration)
        self.assertIn("request_logs.external_request_log", migration)
        self.assertIn("raw_market.raw_candle", migration)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS analytics.", migration)
        self.assertNotIn("UPDATE decisions.", migration)
        self.assertNotIn("UPDATE orders.", migration)
        self.assertNotIn("UPDATE weights.", migration)
        self.assertNotIn("UPDATE risk.", migration)

    def test_live_validation_analytics_cover_guardrail_and_edge_diagnostics(self) -> None:
        migration = read_text(MIGRATIONS_DIR / "033_live_validation_analytics_views.sql")
        required_tokens = (
            "expected_edge_after_cost_bps",
            "execution_cost_estimate_bps",
            "edge_to_cost_ratio",
            "cost_model_quality",
            "candle.close_ts <= decision.decision_ts",
            "candle.close_ts >= decision.decision_ts + interval '30 minutes'",
            "candle.close_ts >= decision.decision_ts + interval '60 minutes'",
            "action_aligned_forward_return_30m_bps",
            "calibration_error_30m_bps",
            "quarantine_candidate",
            "watchlist_candidate",
            "guardrail_rejection_rate",
            "portfolio_scope_warning",
            "second_trade.order_intent_id <> first_trade.order_intent_id",
            "second_trade.run_mode IS NOT DISTINCT FROM first_trade.run_mode",
            "second_trade.portfolio_id IS NOT DISTINCT FROM first_trade.portfolio_id",
            "GROUP BY outcome.instrument_id, outcome.run_mode, outcome.portfolio_id",
            "GROUP BY run_mode, portfolio_id, account_id, strategy_id, portfolio_scope_warning, reason_code",
            "rejected_anti_churn_opposite_action",
            "rejected_reentry_cooldown",
            "rejected_trade_frequency_hourly",
            "rejected_trade_frequency_daily",
            "rejected_low_expected_edge_after_cost",
            "rejected_turnover_without_edge",
            "daily_soft_loss_reduce_only",
            "daily_hard_loss_observation_only",
            "rejected_instrument_quarantine",
            "rejected_instrument_blocked",
            "watchlist_edge_threshold_increased",
            "guardrail_trade_history_missing",
        )
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, migration)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS analytics.", migration)
        self.assertNotIn("INSERT INTO orders.", migration)
        self.assertNotIn("INSERT INTO decisions.", migration)
        self.assertNotIn("UPDATE decisions.", migration)
        self.assertNotIn("UPDATE orders.", migration)
        self.assertNotIn("UPDATE weights.", migration)
        self.assertNotIn("UPDATE risk.", migration)

    def test_controlled_paper_validation_report_is_read_only(self) -> None:
        report = read_text(CONTROLLED_VALIDATION_REPORT_SQL)
        self.assertIn("SELECT *\n  FROM analytics.live_dashboard_summary", report)
        for view_name in (
            "analytics.live_dashboard_summary",
            "analytics.churn_round_trips",
            "analytics.instrument_live_stats",
            "analytics.guardrail_rejections",
            "analytics.edge_calibration",
            "analytics.pnl_by_reason_code",
            "analytics.execution_cost_realized",
            "analytics.feature_outcome_attribution",
        ):
            with self.subTest(view=view_name):
                self.assertIn(view_name, report)

        sql_without_comments = "\n".join(
            line for line in report.splitlines()
            if not line.lstrip().startswith("--")
        )
        forbidden = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|CALL)\b",
            re.IGNORECASE,
        )
        self.assertIsNone(forbidden.search(sql_without_comments))

        statements = [
            statement.strip()
            for statement in sql_without_comments.split(";")
            if statement.strip()
        ]
        self.assertTrue(statements)
        for statement in statements:
            with self.subTest(statement=statement[:40]):
                self.assertRegex(statement, r"^(SELECT|WITH)\b")

    def test_live_smoke_preflight_report_is_read_only_and_checks_risk_scope(self) -> None:
        report = read_text(LIVE_SMOKE_PREFLIGHT_REPORT_SQL)
        for token in (
            "risk.risk_policy",
            "risk.portfolio_limit",
            "risk.instrument_limit",
            "portfolio.portfolio_snapshot",
            "portfolio.position_state",
            "registry.instrument_profile",
            "analytics.instrument_live_stats",
            "daily_hard_loss_missing",
            "daily_hard_loss_too_wide",
            "max_position_pct_missing",
            "max_position_pct_too_wide",
            "universe_too_large",
            "toxic_instrument_in_universe",
            "open_positions_exist",
            "portfolio_snapshot_stale",
            "kill_switch_status_unknown",
            "slippage_limit_missing",
            "liquidity_limit_missing",
            "opposite_action_cooldown_seconds",
            "reentry_after_close_cooldown_seconds",
            "base_min_edge_after_cost_bps",
            "reversal_min_edge_after_cost_bps",
            "quarantine_min_edge_after_cost_bps",
            "max_trades_per_instrument_per_hour",
            "max_trades_per_instrument_per_day",
        ):
            with self.subTest(token=token):
                self.assertIn(token, report)

        sql_without_comments = "\n".join(
            line for line in report.splitlines()
            if not line.lstrip().startswith("--")
        )
        forbidden = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|CALL)\b",
            re.IGNORECASE,
        )
        self.assertIsNone(forbidden.search(sql_without_comments))

        statements = [
            statement.strip()
            for statement in sql_without_comments.split(";")
            if statement.strip()
        ]
        self.assertTrue(statements)
        for statement in statements:
            with self.subTest(statement=statement[:40]):
                self.assertRegex(statement, r"^(SELECT|WITH)\b")

    def test_guarded_live_smoke_runbook_is_bounded_and_observable(self) -> None:
        doc = read_text(GUARDED_LIVE_SMOKE_DOC)
        required_tokens = (
            "30-60 minutes",
            "Preflight: Check Active DB Risk Limits",
            "Get-Content -Raw .\\sql\\analytics\\035_live_smoke_preflight_report.sql",
            "daily hard loss is set and not wider than `-0.10%`",
            "`max_position_pct <= 0.02`",
            "kill switch state is unknown",
            "`SAFE_LIVE_SUBMIT=true` only for the actual guarded smoke",
            "SCHEDULER_ONCE",
            "SCHEDULER_SCHEDULE_IDS",
            "max_position_pct = 0.01-0.02",
            "daily_pnl_pct <= -0.001",
            "flip-flop within 15 minutes",
            "guardrail_rejection_rate = 0",
            "docker compose -f docker/docker-compose.prod.yml stop scheduler_worker",
            "Get-Content -Raw .\\sql\\analytics\\034_controlled_paper_validation_report.sql",
            "analytics.live_dashboard_summary",
            "analytics.guardrail_rejections",
            "analytics.churn_round_trips",
            "analytics.instrument_live_stats",
            "Do not move to weights:v3",
        )
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, doc)


if __name__ == "__main__":
    unittest.main()
