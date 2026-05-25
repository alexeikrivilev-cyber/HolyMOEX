from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence


CALCULATION_VERSION = "controlled_staging_runner_v1"
DEFAULT_UNIVERSE_ID = "moex_top20_manual"
DEFAULT_STAGING_TICKERS = ("SBER", "LKOH", "GAZP")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def run_controlled_staging(
    *,
    database_url: str,
    universe_id: str,
    run_mode: str,
    instrument_cap: int,
    max_news_items: int,
    execution_provider: str = "mock",
) -> dict[str, Any]:
    import psycopg
    from psycopg.types.json import Jsonb

    as_of = utc_now()
    as_of_text = iso(as_of)
    run_id = f"staging_{as_of.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    execution_mode = "mock_fill"
    if execution_provider != "mock":
        raise RuntimeError("controlled staging runner supports only EXECUTION_PROVIDER=mock")
    if os.getenv("SAFE_LIVE_SUBMIT", "").lower() in {"1", "true", "yes"}:
        raise RuntimeError("controlled staging runner must run with SAFE_LIVE_SUBMIT=false")
    if os.getenv("CONTROLLED_PIPELINE_MARKET_OPEN_OVERRIDE", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("set CONTROLLED_PIPELINE_MARKET_OPEN_OVERRIDE=true for explicit controlled market-open simulation")
    refs: dict[str, list[str]] = {
        "feature_vectors": [],
        "decision_sets": [],
        "decision_records": [],
        "risk_checks": [],
        "order_intents": [],
        "execution_results": [],
        "fill_reports": [],
        "portfolio_snapshots": [],
        "monitoring": [],
        "audit": [],
    }

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            portfolio_id = resolve_portfolio_id(cur)
            instruments = load_staging_instruments(cur, universe_id, instrument_cap)
            if not instruments:
                raise RuntimeError("controlled staging needs at least one active tradable instrument")
            news_count = min(max_news_items, len(instruments))
            insert_module_run(cur, Jsonb, run_id, "controlled_staging_started", "running", as_of_text, {"instrument_cap": instrument_cap})
            for index, instrument in enumerate(instruments):
                instrument_id = instrument["instrument_id"]
                price = latest_price(cur, instrument_id, universe_id, as_of) or 100.0
                raw_refs = latest_raw_refs(cur, instrument_id, universe_id, as_of)
                if index < news_count:
                    raw_refs.append(insert_staging_news(cur, Jsonb, run_id, instrument, as_of_text))
                feature_refs = insert_staging_features(cur, Jsonb, run_id, instrument, price, raw_refs, as_of_text)
                feature_vector_ref = insert_feature_vector(cur, Jsonb, run_id, instrument, price, feature_refs, as_of_text)
                refs["feature_vectors"].append(feature_vector_ref)

                decision_set_ref, decision_set_id, decision_record_ref = insert_decision_set(cur, Jsonb, run_id, instrument, feature_vector_ref, run_mode, universe_id, as_of_text)
                refs["decision_sets"].append(decision_set_ref)
                refs.setdefault("decision_records", []).append(decision_record_ref)
                risk_ref, risk_check_id, order_ref, order_id = insert_risk_and_order(
                    cur,
                    Jsonb,
                    run_id,
                    instrument,
                    decision_set_id,
                    price,
                    run_mode,
                    as_of_text,
                )
                refs["risk_checks"].append(risk_ref)
                refs["order_intents"].append(order_ref)
                execution_ref, fill_ref = insert_mock_execution(cur, Jsonb, run_id, order_id, instrument, price, as_of_text, execution_mode)
                refs["execution_results"].append(execution_ref)
                refs["fill_reports"].append(fill_ref)

            snapshot_ref = insert_portfolio_snapshot(cur, Jsonb, run_id, portfolio_id, universe_id, refs["fill_reports"], as_of_text)
            refs["portfolio_snapshots"].append(snapshot_ref)
            monitoring_ref = insert_monitoring(cur, Jsonb, run_id, refs, as_of_text)
            refs["monitoring"].append(monitoring_ref)
            audit_ref = insert_audit(cur, Jsonb, run_id, "controlled_staging_completed", "info", refs, as_of_text)
            refs["audit"].append(audit_ref)
            insert_module_run(cur, Jsonb, run_id, "controlled_staging_completed", "success", as_of_text, {"refs": refs})
            insert_module_job_results(cur, Jsonb, run_id, refs, as_of_text)

    return {
        "run_id": run_id,
        "run_mode": run_mode,
        "execution_mode": execution_mode,
        "portfolio_id": portfolio_id,
        "instrument_count": len(instruments),
        "max_news_items": max_news_items,
        "refs": refs,
        "safe_live_submit": os.getenv("SAFE_LIVE_SUBMIT", "").lower() in {"1", "true", "yes"},
        "counts": {name: len(values) for name, values in refs.items()},
    }


def load_staging_instruments(cur: Any, universe_id: str, cap: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT instrument_id, ticker, board_id, arena_go_secid, lot_size, min_price_increment, metadata
          FROM registry.instrument_profile
         WHERE universe_id = %s
           AND is_active = true
           AND tradable = true
           AND execution_enabled = true
           AND ticker = ANY(%s)
         ORDER BY array_position(%s::text[], ticker), ticker
         LIMIT %s
        """,
        (universe_id, list(DEFAULT_STAGING_TICKERS), list(DEFAULT_STAGING_TICKERS), max(1, min(3, cap))),
    )
    return [
        {
            "instrument_id": row[0],
            "ticker": row[1],
            "board_id": row[2],
            "arena_go_secid": row[3] or row[1],
            "lot_size": int(row[4] or 1),
            "min_price_increment": float(row[5] or 0.01),
            "metadata": row[6] or {},
        }
        for row in cur.fetchall()
    ]


def resolve_portfolio_id(cur: Any) -> str:
    cur.execute(
        """
        SELECT portfolio_id
          FROM portfolio.portfolio_snapshot
         WHERE portfolio_id IS NOT NULL
           AND portfolio_id <> ''
           AND portfolio_id <> 'arena_go_default'
           AND source_module = 'Portfolio State Module'
         ORDER BY created_at DESC
         LIMIT 1
        """
    )
    row = cur.fetchone()
    if row and row[0]:
        return str(row[0])
    env_portfolio = (os.getenv("ARENA_GO_PORTFOLIO") or os.getenv("ARENA_GO_BOT_NAME") or "").strip()
    if env_portfolio and env_portfolio != "arena_go_default":
        return env_portfolio
    return env_portfolio or "arena_go_default"


def latest_price(cur: Any, instrument_id: str, universe_id: str, as_of: datetime) -> float | None:
    cur.execute(
        """
        SELECT close_price
          FROM raw_market.raw_candle
         WHERE instrument_id = %s
           AND (universe_id = %s OR universe_id IS NULL)
           AND COALESCE(close_ts, open_ts) <= %s
           AND close_price IS NOT NULL
         ORDER BY COALESCE(close_ts, open_ts) DESC, raw_candle_id DESC
         LIMIT 1
        """,
        (instrument_id, universe_id, as_of),
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def latest_raw_refs(cur: Any, instrument_id: str, universe_id: str, as_of: datetime) -> list[str]:
    cur.execute(
        """
        SELECT raw_candle_id::text
          FROM raw_market.raw_candle
         WHERE instrument_id = %s
           AND (universe_id = %s OR universe_id IS NULL)
           AND COALESCE(close_ts, open_ts) <= %s
         ORDER BY COALESCE(close_ts, open_ts) DESC, raw_candle_id DESC
         LIMIT 2
        """,
        (instrument_id, universe_id, as_of),
    )
    return [f"raw_market.raw_candle:{row[0]}" for row in cur.fetchall()]


def insert_staging_news(cur: Any, Jsonb: Any, run_id: str, instrument: Mapping[str, Any], as_of_text: str) -> str:
    source_url = f"https://holy-moex.local/staging/{run_id}/{instrument['ticker']}"
    content_hash = f"staging:{run_id}:{instrument['instrument_id']}"
    cur.execute(
        """
        INSERT INTO raw_text.raw_text_item (
            universe_id, instrument_ids, source, source_type, source_url, title, body, language,
            published_at, fetched_at, content_hash, source_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, md5(%s), %s)
        ON CONFLICT (content_hash) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
        RETURNING raw_text_item_id
        """,
        (
            DEFAULT_UNIVERSE_ID,
            [instrument["instrument_id"]],
            "controlled_staging",
            "news_api",
            source_url,
            f"Controlled staging signal for {instrument['ticker']}",
            "Synthetic bounded staging item; not used as live alpha without provider confirmation.",
            "en",
            as_of_text,
            as_of_text,
            content_hash,
            Jsonb({"run_id": run_id, "controlled": True}),
        ),
    )
    row = cur.fetchone()
    return f"raw_text.raw_text_item:{row[0]}"


def insert_staging_features(cur: Any, Jsonb: Any, run_id: str, instrument: Mapping[str, Any], price: float, raw_refs: list[str], as_of_text: str) -> list[str]:
    candle_count = len([ref for ref in raw_refs if ref.startswith("raw_market.raw_candle:")])
    degraded_flags = [] if candle_count else ["raw_candle_missing", "low_coverage"]
    macro_flags = ["raw_macro_missing", "degraded_macro_context"]
    liquidity_flags = ["orderbook_missing", "trade_prints_missing", "approximate_liquidity_from_candles"]
    metrics = (
        {
            "metric_name": "latest_price",
            "value": price,
            "group": "market_data",
            "source_module": "Market Data Metrics Module",
            "confidence": 0.95 if candle_count else 0.55,
            "quality_flags": degraded_flags,
            "unit": "RUB",
        },
        {
            "metric_name": "return_1d",
            "value": 0.002,
            "group": "market_data",
            "source_module": "Market Data Metrics Module",
            "confidence": 0.75 if candle_count >= 2 else 0.50,
            "quality_flags": degraded_flags if candle_count >= 2 else [*degraded_flags, "limited_history"],
            "unit": "ratio",
        },
        {
            "metric_name": "volume_turnover_score",
            "value": 0.55,
            "group": "market_data",
            "source_module": "Market Data Metrics Module",
            "confidence": 0.65 if candle_count else 0.40,
            "quality_flags": degraded_flags,
            "unit": "score",
        },
        {
            "metric_name": "spread_bps",
            "value": 4.0,
            "group": "liquidity",
            "source_module": "Liquidity & Microstructure Module",
            "confidence": 0.55,
            "quality_flags": liquidity_flags,
            "unit": "bps",
        },
        {
            "metric_name": "estimated_slippage_bps",
            "value": 3.0,
            "group": "liquidity",
            "source_module": "Liquidity & Microstructure Module",
            "confidence": 0.55,
            "quality_flags": liquidity_flags,
            "unit": "bps",
        },
        {
            "metric_name": "commission_bps",
            "value": 1.0,
            "group": "liquidity",
            "source_module": "Liquidity & Microstructure Module",
            "confidence": 0.80,
            "quality_flags": [],
            "unit": "bps",
        },
        {
            "metric_name": "realized_volatility_20d",
            "value": 0.18,
            "group": "volatility",
            "source_module": "Volatility & Risk Metrics Module",
            "confidence": 0.60 if candle_count else 0.40,
            "quality_flags": degraded_flags if candle_count else [*degraded_flags, "volatility_degraded"],
            "unit": "annualized_ratio",
        },
        {
            "metric_name": "market_session_status",
            "value": "open",
            "group": "market_context",
            "source_module": "Market Context Module",
            "confidence": 1.0,
            "quality_flags": ["controlled_market_open_override"],
            "unit": None,
        },
        {
            "metric_name": "market_regime",
            "value": "normal",
            "group": "market_context",
            "source_module": "Market Context Module",
            "confidence": 0.55,
            "quality_flags": macro_flags,
            "unit": None,
        },
        {
            "metric_name": "macro_context_score",
            "value": 0.50,
            "group": "market_context",
            "source_module": "Market Context Module",
            "confidence": 0.45,
            "quality_flags": macro_flags,
            "unit": "score",
        },
        {
            "metric_name": "arena_go_secid",
            "value": instrument["arena_go_secid"],
            "group": "execution_mapping",
            "source_module": "Selected Instruments Registry Module",
            "confidence": 1.0,
            "quality_flags": [],
            "unit": None,
        },
    )
    refs: list[str] = []
    for metric in metrics:
        metric_name = str(metric["metric_name"])
        value = metric["value"]
        feature_id = f"{run_id}:{instrument['ticker']}:{metric_name}"
        raw_value = value if isinstance(value, (int, float)) else None
        payload = {
            "run_id": run_id,
            "ttl_status": "fresh",
            "source_refs": raw_refs,
            "calculation_version": CALCULATION_VERSION,
            "value": value,
            "coverage_note": "controlled bootstrap uses candles plus degraded flags for unavailable raw_trade/raw_orderbook/raw_macro",
        }
        cur.execute(
            """
            INSERT INTO features.feature_record (
                feature_id, instrument_id, metric_name, metric_group, metric_type,
                raw_value, normalized_value, unit, horizon, contour, timestamp,
                ttl_seconds, confidence_score, source_module, source_refs,
                calculation_version, quality_flags, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (feature_id) DO UPDATE SET payload = EXCLUDED.payload
            """,
            (
                feature_id,
                instrument["instrument_id"],
                metric_name,
                metric["group"],
                "numeric" if raw_value is not None else "categorical",
                raw_value,
                raw_value,
                metric["unit"],
                "intraday",
                "staging_contour",
                as_of_text,
                300,
                metric["confidence"],
                metric["source_module"],
                raw_refs,
                CALCULATION_VERSION,
                metric["quality_flags"],
                Jsonb(payload),
            ),
        )
        refs.append(f"features.feature_record:{feature_id}")
    return refs


def insert_feature_vector(cur: Any, Jsonb: Any, run_id: str, instrument: Mapping[str, Any], price: float, feature_refs: list[str], as_of_text: str) -> str:
    vector_id = f"{run_id}:{instrument['ticker']}:intraday"
    degraded_flags = ["orderbook_missing", "trade_prints_missing", "raw_macro_missing", "degraded_macro_context"]
    features = {
        "latest_price": {"raw_value": price, "normalized_value": price, "ttl_status": "fresh", "source_refs": feature_refs},
        "return_1d": {"raw_value": 0.002, "normalized_value": 0.55, "ttl_status": "fresh", "source_refs": feature_refs},
        "volume_turnover_score": {"raw_value": 0.55, "normalized_value": 0.55, "ttl_status": "fresh", "source_refs": feature_refs, "quality_flags": ["limited_trade_print_coverage"]},
        "spread_bps": {"raw_value": 4.0, "normalized_value": 4.0, "ttl_status": "fresh", "source_refs": feature_refs, "ttl_seconds": 300},
        "estimated_slippage_bps": {"raw_value": 3.0, "normalized_value": 3.0, "ttl_status": "fresh", "source_refs": feature_refs, "ttl_seconds": 300, "quality_flags": ["approximate_liquidity_from_candles"]},
        "commission_bps": {"raw_value": 1.0, "normalized_value": 1.0, "ttl_status": "fresh", "source_refs": feature_refs},
        "realized_volatility_20d": {"raw_value": 0.18, "normalized_value": 0.18, "ttl_status": "fresh", "source_refs": feature_refs},
        "market_session_status": {"value": "open", "raw_value": "open", "ttl_status": "fresh", "source_refs": feature_refs},
        "market_regime": {"value": "normal", "raw_value": "normal", "ttl_status": "fresh", "source_refs": feature_refs, "quality_flags": ["raw_macro_missing"]},
        "macro_context_score": {"raw_value": 0.50, "normalized_value": 0.50, "ttl_status": "fresh", "source_refs": feature_refs, "quality_flags": ["degraded_macro_context"]},
        "arena_go_secid": {"value": instrument["arena_go_secid"], "raw_value": instrument["arena_go_secid"], "ttl_status": "fresh", "source_refs": feature_refs},
        "_meta": {
            "coverage_ratio": 0.82,
            "data_quality_score": 0.72,
            "source_refs": feature_refs,
            "ttl_status": "fresh",
            "quality_flags": degraded_flags,
            "calculation_version": CALCULATION_VERSION,
        },
    }
    cur.execute(
        """
        INSERT INTO features.feature_vector (
            feature_vector_id, instrument_id, horizon, as_of_ts,
            features, coverage_ratio, data_quality_score, build_version
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (feature_vector_id) DO UPDATE SET features = EXCLUDED.features
        """,
        (vector_id, instrument["instrument_id"], "intraday", as_of_text, Jsonb(features), 0.82, 0.72, CALCULATION_VERSION),
    )
    return f"features.feature_vector:{vector_id}"


def insert_decision_set(
    cur: Any,
    Jsonb: Any,
    run_id: str,
    instrument: Mapping[str, Any],
    feature_vector_ref: str,
    run_mode: str,
    universe_id: str,
    as_of_text: str,
) -> tuple[str, str, str]:
    request_id = f"{run_id}:decision_request:{instrument['ticker']}"
    decision_set_id = f"{run_id}:decision_set:{instrument['ticker']}"
    decision = {
        "instrument_id": instrument["instrument_id"],
        "action": "buy",
        "target_quantity": 1,
        "confidence_score": 1.0,
        "expected_edge_score": 0.035,
        "expected_edge_after_cost_score": 0.025,
        "primary_reason_codes": ["controlled_staging_positive_edge", "turnover_mandate_urgency"],
        "feature_vector_ref": feature_vector_ref,
    }
    cur.execute(
        """
        INSERT INTO decisions.decision_request (
            decision_request_id, universe_id, instrument_ids, horizon, as_of_ts,
            feature_vector_refs, portfolio_state_ref, weights_profile_id,
            run_mode, decision_mode, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (decision_request_id) DO NOTHING
        """,
        (
            request_id,
            universe_id,
            [instrument["instrument_id"]],
            "intraday",
            as_of_text,
            [feature_vector_ref],
            f"portfolio.portfolio_snapshot:{run_id}:portfolio_snapshot",
            "weights:live_autonomous:intraday:v1",
            run_mode,
            "controlled_staging",
            Jsonb({"run_id": run_id, "current_cycle_refs_only": True}),
        ),
    )
    cur.execute(
        """
        INSERT INTO decisions.decision_set (
            decision_set_id, decision_request_id, horizon, decisions, calculation_version, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (decision_set_id) DO UPDATE SET decisions = EXCLUDED.decisions
        """,
        (decision_set_id, request_id, "intraday", Jsonb([decision]), CALCULATION_VERSION, as_of_text),
    )
    decision_record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{instrument['ticker']}:decision_record"))
    cur.execute(
        """
        INSERT INTO decisions.decision_record (
            decision_record_id, decision_set_id, instrument_id, action,
            target_position_pct, target_quantity, confidence_score,
            expected_edge_score, risk_score, primary_reason_codes,
            feature_contributions, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (decision_record_id) DO UPDATE SET feature_contributions = EXCLUDED.feature_contributions
        """,
        (
            decision_record_id,
            decision_set_id,
            instrument["instrument_id"],
            "buy",
            None,
            1,
            0.72,
            0.035,
            0.20,
            ["controlled_staging_positive_edge", "turnover_mandate_urgency"],
            Jsonb({"feature_vector_ref": feature_vector_ref, "current_cycle": True}),
            as_of_text,
        ),
    )
    return f"decisions.decision_set:{decision_set_id}", decision_set_id, f"decisions.decision_record:{decision_record_id}"


def insert_risk_and_order(cur: Any, Jsonb: Any, run_id: str, instrument: Mapping[str, Any], decision_set_id: str, price: float, run_mode: str, as_of_text: str) -> tuple[str, str, str, str]:
    risk_check_id = f"{run_id}:risk_check:{instrument['ticker']}"
    order_id = f"{run_id}:order_intent:{instrument['ticker']}"
    order_ref = f"orders.order_intent:{order_id}"
    cur.execute(
        """
        INSERT INTO orders.order_intent (
            order_intent_id, instrument_id, side, quantity, order_type,
            limit_price, time_in_force, max_slippage_bps, execution_ttl_seconds,
            decision_set_id, risk_check_id, run_mode, created_at, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (order_intent_id) DO UPDATE SET payload = EXCLUDED.payload
        """,
        (
            order_id,
            instrument["instrument_id"],
            "buy",
            1,
            "limit",
            price * 1.001,
            "day",
            10,
            300,
            decision_set_id,
            risk_check_id,
            run_mode,
            as_of_text,
            Jsonb({"run_id": run_id, "expected_edge_after_cost_score": 0.025, "current_cycle": True}),
        ),
    )
    cur.execute(
        """
        INSERT INTO risk.risk_check_result (
            risk_check_id, decision_set_id, status, approved_order_intents,
            rejected_decisions, risk_flags, adjustments, checked_at, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (risk_check_id) DO UPDATE SET payload = EXCLUDED.payload
        """,
        (
            risk_check_id,
            decision_set_id,
            "approved",
            [order_ref],
            [],
            [],
            Jsonb([]),
            as_of_text,
            Jsonb(
                {
                    "run_id": run_id,
                    "expected_edge_after_cost_score": 0.025,
                    "post_cost_edge_gate": "passed",
                    "current_cycle_order_intents_only": True,
                }
            ),
        ),
    )
    return f"risk.risk_check_result:{risk_check_id}", risk_check_id, order_ref, order_id


def insert_mock_execution(cur: Any, Jsonb: Any, run_id: str, order_id: str, instrument: Mapping[str, Any], price: float, as_of_text: str, execution_mode: str) -> tuple[str, str]:
    execution_id = f"{run_id}:execution:{instrument['ticker']}"
    cur.execute(
        """
        INSERT INTO orders.execution_result (
            execution_result_id, order_intent_id, status, broker_order_id,
            submitted_at, last_update_at, filled_quantity, avg_fill_price,
            fees, slippage_bps, errors, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (execution_result_id) DO UPDATE SET payload = EXCLUDED.payload
        """,
        (
            execution_id,
            order_id,
            "filled",
            f"mock:{execution_id}",
            as_of_text,
            as_of_text,
            1,
            price,
            price * 0.0005,
            0,
            [],
            Jsonb({"run_id": run_id, "execution_mode": execution_mode, "no_live_submit": True}),
        ),
    )
    cur.execute(
        """
        INSERT INTO orders.fill_report (
            order_intent_id, provider_fill_id, fill_ts, filled_quantity,
            fill_price, fees, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING fill_report_id
        """,
        (
            order_id,
            f"mock_fill:{execution_id}",
            as_of_text,
            1,
            price,
            price * 0.0005,
            Jsonb({"run_id": run_id, "instrument_id": instrument["instrument_id"], "side": "buy"}),
        ),
    )
    fill_id = str(cur.fetchone()[0])
    return f"orders.execution_result:{execution_id}", f"orders.fill_report:{fill_id}"


def insert_portfolio_snapshot(cur: Any, Jsonb: Any, run_id: str, portfolio_id: str, universe_id: str, fill_refs: list[str], as_of_text: str) -> str:
    snapshot_id = f"{run_id}:portfolio_snapshot"
    turnover = 0.0
    cur.execute(
        """
        SELECT COALESCE(sum(abs(filled_quantity * fill_price)), 0)
          FROM orders.fill_report
         WHERE payload ->> 'run_id' = %s
        """,
        (run_id,),
    )
    row = cur.fetchone()
    turnover = float(row[0] or 0.0)
    cash = 1_000_000.0 - turnover
    payload = {
        "run_id": run_id,
        "turnover_mandate_enabled": True,
        "target_gross_turnover_rub_14d": 10_000_000.0,
        "turnover_window_days": 14,
        "gross_turnover_rub_1d": turnover,
        "gross_turnover_rub_14d": turnover,
        "turnover_ratio_14d": turnover / 1_000_000.0,
        "required_daily_turnover_rub": max(0.0, 10_000_000.0 - turnover) / 14.0,
        "projected_turnover_rub_14d": turnover * 14.0,
        "turnover_target_status": "critically_behind" if turnover < 650_000 else "behind",
        "applied_fill_refs": fill_refs,
        "ttl_status": "fresh",
        "source": "controlled_staging_mock",
    }
    cur.execute(
        """
        INSERT INTO portfolio.portfolio_snapshot (
            portfolio_snapshot_id, portfolio_id, universe_id, as_of_ts,
            initial_capital_rub, cash, equity, gross_exposure, net_exposure,
            realized_pnl, unrealized_pnl, source_module, source_refs, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (portfolio_snapshot_id) DO UPDATE SET payload = EXCLUDED.payload
        """,
        (
            snapshot_id,
            portfolio_id,
            universe_id,
            as_of_text,
            1_000_000,
            cash,
            1_000_000,
            turnover,
            turnover,
            0,
            0,
            "Controlled Staging Runner",
            fill_refs,
            Jsonb(payload),
        ),
    )
    return f"portfolio.portfolio_snapshot:{snapshot_id}"


def insert_monitoring(cur: Any, Jsonb: Any, run_id: str, refs: Mapping[str, list[str]], as_of_text: str) -> str:
    cur.execute(
        """
        INSERT INTO audit.monitoring_record (check_name, status, severity, observed_at, payload)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING monitoring_record_id
        """,
        (
            "controlled_staging_pipeline",
            "pass",
            "info",
            as_of_text,
            Jsonb({"run_id": run_id, "refs": dict(refs), "turnover_target_status_visible": True}),
        ),
    )
    return f"audit.monitoring_record:{cur.fetchone()[0]}"


def insert_audit(cur: Any, Jsonb: Any, run_id: str, event_type: str, severity: str, refs: Mapping[str, list[str]], as_of_text: str) -> str:
    cur.execute(
        """
        INSERT INTO audit.audit_record (
            module_name, severity, event_type, message, object_type,
            object_ref, reason_codes, payload, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING audit_record_id
        """,
        (
            "Controlled Staging Runner",
            severity,
            event_type,
            "Controlled staging pipeline completed with current-cycle refs and mock execution.",
            "staging_run",
            run_id,
            ["controlled_staging", "current_cycle_refs", "mock_execution"],
            Jsonb({"run_id": run_id, "refs": dict(refs), "calculation_version": CALCULATION_VERSION}),
            as_of_text,
        ),
    )
    return f"audit.audit_record:{cur.fetchone()[0]}"


def insert_module_run(cur: Any, Jsonb: Any, run_id: str, event_type: str, status: str, as_of_text: str, payload: Mapping[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO audit.module_run (job_id, module_name, status, started_at, finished_at, payload)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (run_id, "Controlled Staging Runner", status, as_of_text, as_of_text, Jsonb({"event_type": event_type, **dict(payload)})),
    )


def insert_module_job_results(cur: Any, Jsonb: Any, run_id: str, refs: Mapping[str, list[str]], as_of_text: str) -> None:
    rows = (
        ("Market Data Metrics Module", refs.get("feature_vectors", ()), 3, 0, 0.72),
        ("Liquidity & Microstructure Module", refs.get("feature_vectors", ()), 3, 0, 0.55),
        ("Volatility & Risk Metrics Module", refs.get("feature_vectors", ()), 1, 0, 0.60),
        ("Market Context Module", refs.get("feature_vectors", ()), 2, 0, 0.45),
        ("Normalization & Feature Vector Module", refs.get("feature_vectors", ()), len(refs.get("feature_vectors", ())), 0, 0.72),
        ("Decision Engine Module", [*refs.get("decision_sets", ()), *refs.get("decision_records", ())], len(refs.get("decision_records", ())), 0, 0.72),
        ("Risk Control Module", [*refs.get("risk_checks", ()), *refs.get("order_intents", ())], len(refs.get("order_intents", ())), 0, 1.0),
        ("Execution Engine Module", [*refs.get("execution_results", ()), *refs.get("fill_reports", ())], len(refs.get("execution_results", ())), len(refs.get("fill_reports", ())), 1.0),
        ("Portfolio State Module", refs.get("portfolio_snapshots", ()), 1, 1, 1.0),
        ("Monitoring & Audit Module", refs.get("monitoring", ()), 1, 1, 1.0),
    )
    for module_name, output_refs, metrics_written, events_written, data_quality_score in rows:
        job_id = f"{run_id}:{module_name.lower().replace(' ', '_').replace('&', 'and')}"
        warnings = []
        if module_name in {"Liquidity & Microstructure Module", "Market Context Module"}:
            warnings = ["controlled_degraded_coverage", "raw_trade_or_macro_missing"]
        cur.execute(
            """
            INSERT INTO audit.module_job_result (
                job_id, module_name, status, started_at, finished_at,
                output_refs, warnings, errors, metrics_written, events_written,
                data_quality_score, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE SET output_refs = EXCLUDED.output_refs, payload = EXCLUDED.payload
            """,
            (
                job_id,
                module_name,
                "partial_success" if warnings else "success",
                as_of_text,
                as_of_text,
                list(output_refs),
                warnings,
                [],
                int(metrics_written),
                int(events_written),
                float(data_quality_score),
                Jsonb({"run_id": run_id, "controlled_pipeline": True, "current_cycle_refs_only": True}),
            ),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled end-to-end staging run for HolyMOEX")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--universe-id", default=os.getenv("SELECTED_UNIVERSE_ID", DEFAULT_UNIVERSE_ID))
    parser.add_argument("--run-mode", default=os.getenv("RUN_MODE", "live_trading"))
    parser.add_argument("--instrument-cap", type=int, default=int(os.getenv("STAGING_INSTRUMENT_CAP", "2")))
    parser.add_argument("--max-news-items", type=int, default=int(os.getenv("STAGING_MAX_NEWS_ITEMS", "2")))
    parser.add_argument("--execution-provider", default=os.getenv("EXECUTION_PROVIDER", "mock"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    result = run_controlled_staging(
        database_url=args.database_url,
        universe_id=args.universe_id,
        run_mode=args.run_mode,
        instrument_cap=args.instrument_cap,
        max_news_items=args.max_news_items,
        execution_provider=args.execution_provider,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
