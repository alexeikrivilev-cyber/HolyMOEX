from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_app.contracts.unified_objects import ModuleJob, TimeRange
from agent_app.modules.external_request_gateway.providers import _parse_json_body
from agent_app.modules.external_request_gateway.repository import _timestamp_from_item
from agent_app.modules.orchestration.dependency_graph import DependencyGraph
from agent_app.modules.liquidity_microstructure import (
    InMemoryLiquidityMicrostructureRepository,
    LiquidityMicrostructureService,
)
from agent_app.modules.normalization_feature_vector import (
    InMemoryNormalizationFeatureVectorRepository,
    NormalizationFeatureVectorService,
)
from agent_app.modules.volatility_risk_metrics import (
    InMemoryVolatilityRiskMetricsRepository,
    VolatilityRiskMetricsService,
)


class FeatureRelevanceQualityPatchTests(unittest.TestCase):
    def test_normalization_penalizes_proxy_and_low_coverage_flags(self) -> None:
        repo = InMemoryNormalizationFeatureVectorRepository(
            profiles=(
                {
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "ticker": "SBER",
                    "allowed_horizons": ["intraday"],
                },
            ),
            feature_records=(
                {
                    "feature_id": "feature_sber_spread_proxy",
                    "instrument_id": "moex:SBER",
                    "metric_name": "spread_bps",
                    "metric_group": "liquidity",
                    "metric_type": "proxy_metric",
                    "raw_value": 12.0,
                    "normalized_value": 0.4,
                    "unit": "bps",
                    "horizon": "intraday",
                    "contour": "realtime_contour",
                    "timestamp": "2026-05-26T10:04:00Z",
                    "ttl_seconds": 300,
                    "confidence_score": 1.0,
                    "source_module": "Liquidity & Microstructure Module",
                    "source_refs": ["raw_market.raw_candle:candle_sber"],
                    "calculation_version": "liquidity_microstructure_v1",
                    "quality_flags": [
                        "missing_orderbook_using_candle_liquidity_proxy:moex:SBER",
                        "low_trade_coverage:moex:SBER",
                        "low_context_coverage",
                    ],
                },
            ),
        )

        result = NormalizationFeatureVectorService(repo).process(
            {
                "normalization_input": {
                    "instrument_ids": ["moex:SBER"],
                    "horizons": ["intraday"],
                    "feature_refs": ["features.feature_record:feature_sber_spread_proxy"],
                    "normalization_profile_id": "live_autonomous:v1",
                    "as_of_ts": "2026-05-26T10:04:30Z",
                }
            },
            _normalization_job(),
        )

        vector = result.feature_vectors[0]
        self.assertLess(vector.data_quality_score, 1.0)
        self.assertEqual(vector.features["_meta"]["data_quality_score"], vector.data_quality_score)
        self.assertLessEqual(vector.data_quality_score, 0.72)

    def test_normalization_latest_feature_ref_does_not_filter_out_real_records(self) -> None:
        repo = InMemoryNormalizationFeatureVectorRepository(
            profiles=(
                {
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "ticker": "SBER",
                    "allowed_horizons": ["intraday"],
                },
            ),
            feature_records=(
                {
                    "feature_id": "feature_sber_return",
                    "instrument_id": "moex:SBER",
                    "metric_name": "return_5m",
                    "metric_group": "price",
                    "metric_type": "point_in_time",
                    "raw_value": 0.01,
                    "normalized_value": 0.6,
                    "unit": "ratio",
                    "horizon": "intraday",
                    "contour": "realtime_contour",
                    "timestamp": "2026-05-26T10:04:00Z",
                    "ttl_seconds": 300,
                    "confidence_score": 1.0,
                    "source_module": "Market Data Metrics Module",
                    "source_refs": ["raw_market.raw_candle:candle_sber"],
                    "calculation_version": "market_data_metrics_v1",
                    "quality_flags": [],
                },
            ),
        )

        result = NormalizationFeatureVectorService(repo).process(
            {
                "normalization_input": {
                    "instrument_ids": ["moex:SBER"],
                    "horizons": ["intraday"],
                    "feature_refs": ["features.feature_record:latest"],
                    "normalization_profile_id": "live_autonomous:v1",
                    "as_of_ts": "2026-05-26T10:04:30Z",
                }
            },
            _normalization_job(),
        )

        vector = result.feature_vectors[0]
        self.assertGreater(vector.coverage_ratio, 0.0)
        self.assertIn("return_5m", vector.features)

    def test_liquidity_quote_proxy_is_flagged_and_not_treated_as_full_orderbook(self) -> None:
        repo = InMemoryLiquidityMicrostructureRepository(
            profiles=(
                {
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "ticker": "SBER",
                    "is_active": True,
                    "tradable": True,
                },
            ),
            orderbooks=(
                {
                    "raw_orderbook_id": "quote_proxy",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "snapshot_ts": "2026-05-21T10:04:00Z",
                    "bids": [{"price": 99.95, "quantity": 1000}],
                    "asks": [{"price": 100.05, "quantity": 1000}],
                    "provider": "moex_iss",
                    "source_payload": {
                        "orderbook_proxy": {
                            "source": "moex_iss_marketdata",
                            "scope": "top_of_book",
                        }
                    },
                },
            ),
        )

        result = LiquidityMicrostructureService(repository=repo).execute(
            payload=_liquidity_payload(),
            job=_liquidity_job(),
        )

        self.assertFalse(result.execution_constraint_hints[0].market_order_allowed)
        self.assertIn("quote_proxy_orderbook", result.execution_constraint_hints[0].reason_codes)
        self.assertTrue(any("top_of_book_only:moex:SBER" in record.quality_flags for record in result.feature_records))

    def test_volatility_reads_history_outside_short_realtime_job_window(self) -> None:
        candles = []
        start = datetime(2026, 4, 20, tzinfo=timezone.utc)
        for index in range(30):
            close_ts = start + timedelta(days=index)
            price = 100.0 + index
            candles.append(
                {
                    "raw_candle_id": f"candle_{index}",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "timeframe": "1d",
                    "open_ts": close_ts.isoformat().replace("+00:00", "Z"),
                    "close_ts": close_ts.isoformat().replace("+00:00", "Z"),
                    "open_price": price - 0.5,
                    "high_price": price + 1.0,
                    "low_price": price - 1.0,
                    "close_price": price,
                    "volume": 1000,
                    "turnover": price * 1000,
                    "provider": "moex_iss",
                }
            )
        repo = InMemoryVolatilityRiskMetricsRepository(candles=tuple(candles))

        result = VolatilityRiskMetricsService(repository=repo).execute(
            payload={
                "risk_metrics_input": {
                    "instrument_ids": ["moex:SBER"],
                    "candles_ref": "raw_market.raw_candle:scheduled",
                    "market_index_ref": "",
                    "sector_index_ref": "",
                    "macro_refs": [],
                    "windows": [5, 20],
                    "horizons": ["intraday"],
                }
            },
            job=_volatility_job(),
        )

        metric_names = {record.metric_name for record in result.feature_records}
        self.assertIn("realized_vol_5d", metric_names)
        self.assertIn("volatility_risk_score", metric_names)

    def test_cbr_key_rate_html_parser_ignores_page_chrome_numbers(self) -> None:
        html = """
        <html><body>
        107016, Москва, ул. Неглинная, д. 12
        Ключевая ставка Банка России
        Дата Ставка 26.05.2026 14,50 25.05.2026 14,50
        </body></html>
        """.encode("utf-8")

        parsed = _parse_json_body(html, {"Content-Type": "text/html; charset=UTF-8"})

        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["points"][0], {"point_ts": "2026-05-26T00:00:00Z", "value": 14.5})
        self.assertTrue(all(point["value"] < 100 for point in parsed["points"]))

    def test_moex_marketdata_wall_clock_timestamp_is_converted_from_moscow_time(self) -> None:
        parsed = _timestamp_from_item(
            {"systime": "2026-05-27 10:14:44", "updatetime": "10:14:44"},
            "2026-05-27T07:14:46Z",
            ("snapshot_ts", "timestamp", "ts", "systime"),
            date_keys=("date", "tradedate"),
            time_keys=("time", "updatetime"),
            default_utc_offset="+03:00",
        )

        self.assertEqual(parsed, "2026-05-27T07:14:44Z")

    def test_raw_market_cycle_reaches_feature_to_execution_contour(self) -> None:
        targets = DependencyGraph.default().module_targets(("Raw Market Data Store",), transitive=True)

        self.assertIn("Market Data Metrics Module", targets)
        self.assertIn("Liquidity & Microstructure Module", targets)
        self.assertNotIn("Derivatives & Positioning Module", targets)
        self.assertIn("Normalization & Feature Vector Module", targets)
        self.assertIn("Decision Engine Module", targets)
        self.assertIn("Risk Control Module", targets)
        self.assertIn("Execution Engine Module", targets)
        self.assertLess(targets.index("Normalization & Feature Vector Module"), targets.index("Decision Engine Module"))
        self.assertLess(targets.index("Decision Engine Module"), targets.index("Risk Control Module"))
        self.assertLess(targets.index("Risk Control Module"), targets.index("Execution Engine Module"))


def _normalization_job() -> ModuleJob:
    return ModuleJob(
        job_id="job_norm_quality_patch",
        module_name="Normalization & Feature Vector Module",
        contour="decision_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange("2026-05-26T10:00:00Z", "2026-05-26T10:05:00Z", "UTC"),
        run_mode="live_trading",
        idempotency_key="job_norm_quality_patch",
    )


def _liquidity_payload() -> dict[str, object]:
    return {
        "liquidity_input": {
            "instrument_ids": ["moex:SBER"],
            "orderbook_ref": "raw_market.raw_orderbook:quote_proxy",
            "trades_ref": "raw_market.raw_trade:scheduled",
            "quotes_ref": "raw_market.raw_orderbook:quote_proxy",
            "depth_levels": ["10bps", "30bps", "50bps"],
            "notional_scenarios": [100000, 1000000],
        }
    }


def _liquidity_job() -> ModuleJob:
    return ModuleJob(
        job_id="job_liq_quality_patch",
        module_name="Liquidity & Microstructure Module",
        contour="realtime_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange("2026-05-21T10:00:00Z", "2026-05-21T10:05:00Z", "UTC"),
        run_mode="live_trading",
        idempotency_key="job_liq_quality_patch",
    )


def _volatility_job() -> ModuleJob:
    return ModuleJob(
        job_id="job_vol_quality_patch",
        module_name="Volatility & Risk Metrics Module",
        contour="realtime_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange("2026-05-26T10:00:00Z", "2026-05-26T10:05:00Z", "UTC"),
        run_mode="live_trading",
        idempotency_key="job_vol_quality_patch",
    )
