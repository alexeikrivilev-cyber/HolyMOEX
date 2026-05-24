from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence


CALCULATION_VERSION = "controlled_staging_runner_v1"
DEFAULT_UNIVERSE_ID = "moex_top20_manual"


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
) -> dict[str, Any]:
    import psycopg
    from psycopg.types.json import Jsonb

    as_of = utc_now()
    as_of_text = iso(as_of)
    run_id = f"staging_{as_of.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    portfolio_id = os.getenv("ARENA_GO_PORTFOLIO") or os.getenv("ARENA_GO_BOT_NAME") or "arena_go_default"
    execution_mode = "mock_fill"
    refs: dict[str, list[str]] = {
        "feature_vectors": [],
        "decision_sets": [],
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

                decision_set_ref, decision_set_id = insert_decision_set(cur, Jsonb, run_id, instrument, feature_vector_ref, run_mode, universe_id, as_of_text)
                refs["decision_sets"].append(decision_set_ref)
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

    return {
        "run_id": run_id,
        "run_mode": run_mode,
        "execution_mode": execution_mode,
        "instrument_count": len(instruments),
        "max_news_items": max_news_items,
        "refs": refs,
        "safe_live_submit": os.getenv("SAFE_LIVE_SUBMIT", "").lower() in {"1", "true", "yes"},
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
         ORDER BY ticker
         LIMIT %s
        """,
        (universe_id, max(1, min(3, cap))),
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
            universe_id, instrument_ids, source, source_url, title, body, language,
            published_at, fetched_at, content_hash, source_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, md5(%s), %s)
        ON CONFLICT (content_hash) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
        RETURNING raw_text_item_id
        """,
        (
            DEFAULT_UNIVERSE_ID,
            [instrument["instrument_id"]],
            "controlled_staging",
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
    metrics = {
        "latest_price": price,
        "spread_bps": 4.0,
        "estimated_slippage_bps": 3.0,
        "commission_bps": 1.0,
        "market_session_status": "open",
        "market_regime": "normal",
        "arena_go_secid": instrument["arena_go_secid"],
    }
    refs: list[str] = []
    for metric_name, value in metrics.items():
        feature_id = f"{run_id}:{instrument['ticker']}:{metric_name}"
        raw_value = value if isinstance(value, (int, float)) else None
        payload = {
            "run_id": run_id,
            "ttl_status": "fresh",
            "source_refs": raw_refs,
            "calculation_version": CALCULATION_VERSION,
            "value": value,
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
                "controlled_staging",
                "numeric" if raw_value is not None else "categorical",
                raw_value,
                raw_value,
                "bps" if metric_name.endswith("_bps") else None,
                "intraday",
                "staging_contour",
                as_of_text,
                300,
                1.0,
                "Controlled Staging Runner",
                raw_refs,
                CALCULATION_VERSION,
                [],
                Jsonb(payload),
            ),
        )
        refs.append(f"features.feature_record:{feature_id}")
    return refs


def insert_feature_vector(cur: Any, Jsonb: Any, run_id: str, instrument: Mapping[str, Any], price: float, feature_refs: list[str], as_of_text: str) -> str:
    vector_id = f"{run_id}:{instrument['ticker']}:intraday"
    features = {
        "latest_price": {"raw_value": price, "normalized_value": price, "ttl_status": "fresh", "source_refs": feature_refs},
        "spread_bps": {"raw_value": 4.0, "normalized_value": 4.0, "ttl_status": "fresh", "source_refs": feature_refs, "ttl_seconds": 300},
        "estimated_slippage_bps": {"raw_value": 3.0, "normalized_value": 3.0, "ttl_status": "fresh", "source_refs": feature_refs, "ttl_seconds": 300},
        "commission_bps": {"raw_value": 1.0, "normalized_value": 1.0, "ttl_status": "fresh", "source_refs": feature_refs},
        "market_session_status": {"value": "open", "raw_value": "open", "ttl_status": "fresh", "source_refs": feature_refs},
        "market_regime": {"value": "normal", "raw_value": "normal", "ttl_status": "fresh", "source_refs": feature_refs},
        "arena_go_secid": {"value": instrument["arena_go_secid"], "raw_value": instrument["arena_go_secid"], "ttl_status": "fresh", "source_refs": feature_refs},
        "_meta": {
            "coverage_ratio": 1.0,
            "data_quality_score": 1.0,
            "source_refs": feature_refs,
            "ttl_status": "fresh",
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
        (vector_id, instrument["instrument_id"], "intraday", as_of_text, Jsonb(features), 1.0, 1.0, CALCULATION_VERSION),
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
) -> tuple[str, str]:
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
            "portfolio.portfolio_snapshot:controlled_staging_current_cycle",
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
    return f"decisions.decision_set:{decision_set_id}", decision_set_id


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled end-to-end staging run for HolyMOEX")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--universe-id", default=os.getenv("SELECTED_UNIVERSE_ID", DEFAULT_UNIVERSE_ID))
    parser.add_argument("--run-mode", default=os.getenv("RUN_MODE", "paper_trading"))
    parser.add_argument("--instrument-cap", type=int, default=int(os.getenv("STAGING_INSTRUMENT_CAP", "2")))
    parser.add_argument("--max-news-items", type=int, default=int(os.getenv("STAGING_MAX_NEWS_ITEMS", "2")))
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
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
