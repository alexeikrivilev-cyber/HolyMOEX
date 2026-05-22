from __future__ import annotations

import unittest

from agent_app.contracts.unified_objects import ModuleJob, TimeRange
from agent_app.modules.liquidity_microstructure import (
    InMemoryLiquidityMicrostructureRepository,
    LiquidityMicrostructureService,
)
from agent_app.modules.liquidity_microstructure.metrics import (
    build_order_book_snapshot,
    compute_bid_ask_spread,
    compute_order_book_depth,
    estimate_slippage,
)


class LiquidityMicrostructureMetricsTests(unittest.TestCase):
    def test_orderbook_formulas_match_documentation(self) -> None:
        snapshot = build_order_book_snapshot(
            bids=[[99.95, 1000], [99.90, 2000]],
            asks=[[100.05, 1000], [100.10, 2000]],
        )

        self.assertAlmostEqual(compute_bid_ask_spread(snapshot.best_bid, snapshot.best_ask), 10.0)
        self.assertEqual(compute_order_book_depth(snapshot, 10), 6000)

        slippage = estimate_slippage(snapshot, 100000)
        self.assertTrue(slippage.filled)
        self.assertIsNotNone(slippage.conservative_slippage_bps)


class LiquidityMicrostructureServiceTests(unittest.TestCase):
    def test_writes_liquidity_features_and_execution_hint_from_fresh_orderbook(self) -> None:
        repository = InMemoryLiquidityMicrostructureRepository(
            profiles=(
                {
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "ticker": "SBER",
                    "sector": "financials",
                    "is_active": True,
                    "tradable": True,
                },
            ),
            orderbooks=(
                {
                    "raw_orderbook_id": "ob_prev",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "snapshot_ts": "2026-05-21T10:03:00Z",
                    "bids": [[99.95, 1500], [99.90, 2000], [99.50, 10000]],
                    "asks": [[100.05, 1600], [100.10, 2000], [100.50, 10000]],
                    "provider": "moex_iss",
                },
                {
                    "raw_orderbook_id": "ob_latest",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "snapshot_ts": "2026-05-21T10:04:00Z",
                    "bids": [[99.95, 3000], [99.90, 3000], [99.50, 10000]],
                    "asks": [[100.05, 1000], [100.10, 3000], [100.50, 10000]],
                    "provider": "moex_iss",
                },
            ),
            trades=(
                {
                    "raw_trade_id": "trade_1",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "trade_ts": "2026-05-21T10:04:10Z",
                    "price": 100.06,
                    "quantity": 100,
                    "side": "buy",
                },
                {
                    "raw_trade_id": "trade_2",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "trade_ts": "2026-05-21T10:04:30Z",
                    "price": 99.94,
                    "quantity": 50,
                    "side": "sell",
                },
            ),
            active_weights={
                (
                    "intraday",
                    ("order_book_imbalance", "trade_imbalance", "order_flow_imbalance"),
                ): {
                    "order_book_imbalance": 1.0,
                    "trade_imbalance": 1.0,
                    "order_flow_imbalance": 1.0,
                },
                (
                    "intraday",
                    ("spread_percentile", "estimated_slippage_1m", "amihud_illiquidity", "order_book_depth_30bps_z"),
                ): {
                    "spread_percentile": 1.0,
                    "estimated_slippage_1m": 1.0,
                    "amihud_illiquidity": 1.0,
                    "order_book_depth_30bps_z": 1.0,
                },
            },
        )
        result = LiquidityMicrostructureService(repository=repository).execute(
            payload=_payload(),
            job=_job(),
        )

        self.assertEqual(result.module_job_result.status, "success")
        self.assertEqual(len(result.execution_constraint_hints), 1)
        self.assertTrue(result.execution_constraint_hints[0].market_order_allowed)
        self.assertEqual(result.execution_constraint_hints[0].reason_codes, ("fresh_orderbook",))

        metric_names = {record.metric_name for record in result.feature_records}
        for expected_metric in (
            "bid_ask_spread_bps",
            "order_book_depth_10bps",
            "order_book_depth_30bps",
            "order_book_depth_50bps",
            "estimated_slippage_100k",
            "estimated_slippage_1m",
            "order_book_imbalance",
            "trade_imbalance",
            "aggressive_buy_ratio",
            "aggressive_sell_ratio",
            "quote_velocity",
            "short_term_pressure_score",
            "liquidity_risk_score",
        ):
            self.assertIn(expected_metric, metric_names)

        self.assertTrue(all(record.metric_group == "liquidity" for record in result.feature_records))
        self.assertTrue(all(record.horizon == "intraday" for record in result.feature_records))

    def test_stale_orderbook_writes_blocking_execution_hint_without_simulating_liquidity(self) -> None:
        repository = InMemoryLiquidityMicrostructureRepository(
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
                    "raw_orderbook_id": "stale_ob",
                    "instrument_id": "moex:SBER",
                    "universe_id": "moex_top20_manual",
                    "snapshot_ts": "2026-05-21T09:55:00Z",
                    "bids": [[99.95, 3000]],
                    "asks": [[100.05, 3000]],
                    "provider": "moex_iss",
                },
            ),
        )
        result = LiquidityMicrostructureService(repository=repository).execute(
            payload=_payload(),
            job=_job(),
        )

        self.assertEqual(result.module_job_result.status, "partial_success")
        self.assertEqual(result.feature_records, ())
        self.assertEqual(len(result.execution_constraint_hints), 1)
        self.assertFalse(result.execution_constraint_hints[0].market_order_allowed)
        self.assertIn("stale_orderbook", result.execution_constraint_hints[0].reason_codes)

    def test_rejects_undocumented_input_fields(self) -> None:
        payload = _payload()
        payload["liquidity_input"]["extra"] = "not documented"
        result = LiquidityMicrostructureService().execute(payload=payload, job=_job())

        self.assertEqual(result.module_job_result.status, "failed")
        self.assertIn("undocumented fields", result.module_job_result.errors[0])


def _payload() -> dict[str, object]:
    return {
        "liquidity_input": {
            "instrument_ids": ["moex:SBER"],
            "orderbook_ref": "raw_market.raw_orderbook:fixture",
            "trades_ref": "raw_market.raw_trade:fixture",
            "quotes_ref": "raw_market.raw_orderbook:fixture",
            "depth_levels": ["10bps", "30bps", "50bps", "100bps"],
            "notional_scenarios": [100000, 1000000],
        }
    }


def _job() -> ModuleJob:
    return ModuleJob(
        job_id="job_liquidity_1",
        module_name="Liquidity & Microstructure Module",
        contour="realtime_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=("moex:SBER",),
        horizons=("intraday",),
        time_range=TimeRange(
            from_ts="2026-05-21T10:00:00Z",
            to_ts="2026-05-21T10:05:00Z",
            timezone="UTC",
        ),
        run_mode="paper_trading",
        idempotency_key="job_liquidity_1",
    )


if __name__ == "__main__":
    unittest.main()
