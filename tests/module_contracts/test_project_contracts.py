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
        migration = read_text(MIGRATIONS_DIR / "032_analytics_performance_views.sql")
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


if __name__ == "__main__":
    unittest.main()
