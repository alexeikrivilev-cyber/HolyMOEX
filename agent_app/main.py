from __future__ import annotations

import argparse
import json
import os
from datetime import time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from agent_app.contracts.unified_objects import TimeRange
from agent_app.contracts.unified_objects.module_job import to_utc_iso, utc_now
from agent_app.modules.orchestration.repository import (
    InMemoryOrchestrationRepository,
    PostgresOrchestrationRepository,
)
from agent_app.modules.orchestration.service import (
    IncomingTrigger,
    OrchestrationInput,
    OrchestrationService,
    PipelineContext,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MOEX AI trading agent runtime entrypoint")
    parser.add_argument("--source", default="Raw Market Data Store")
    parser.add_argument("--payload-ref", default="")
    parser.add_argument("--trigger-type", default="manual")
    parser.add_argument("--priority", default="normal")
    parser.add_argument("--system-mode", default=os.getenv("RUN_MODE", "paper_trading"))
    parser.add_argument("--universe-id", default=os.getenv("SELECTED_UNIVERSE_ID", "moex_top20_manual"))
    parser.add_argument("--instrument-id", action="append", default=[])
    parser.add_argument("--horizon", action="append", default=[])
    parser.add_argument("--use-postgres", action="store_true", help="Force PostgreSQL-backed runtime")
    parser.add_argument("--no-postgres", action="store_true", help="Force in-memory runtime; intended only for tests/smoke")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("DATABASE_URL", "")
    app_env = os.getenv("APP_ENV", "local").lower()
    use_postgres = bool(database_url) and not args.no_postgres and (args.use_postgres or app_env not in {"test", "unit_test"})
    if args.use_postgres and not database_url:
        raise RuntimeError("--use-postgres was requested but DATABASE_URL is not set")
    if args.system_mode in {"paper_trading", "live_trading"} and database_url and not use_postgres:
        raise RuntimeError("paper/live runtime must use PostgreSQL-backed stores unless DATABASE_URL is intentionally unset")
    repository = PostgresOrchestrationRepository(database_url) if use_postgres else InMemoryOrchestrationRepository()
    executor = None
    if use_postgres:
        from agent_app.modules.orchestration.executor import LocalModuleExecutor

        executor = LocalModuleExecutor(database_url=database_url, use_postgres=True)
    service = OrchestrationService(repository, executor=executor)
    request = OrchestrationInput(
        schedule_config_ref=None,
        dependency_graph_ref=None,
        incoming_trigger=IncomingTrigger(
            trigger_type=args.trigger_type,
            source_module=args.source,
            payload_ref=args.payload_ref,
            priority=args.priority,
        ),
        system_mode=args.system_mode,
    )
    context = PipelineContext(
        universe_id=args.universe_id,
        instrument_ids=tuple(args.instrument_id),
        horizons=tuple(args.horizon) if args.horizon else ("intraday", "swing", "position"),
        time_range=_default_time_range(args.system_mode),
        system_mode=args.system_mode,
    )
    pipeline_run = service.run_pipeline(request, context)
    print(json.dumps(pipeline_run.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _default_time_range(system_mode: str) -> TimeRange:
    now = utc_now()
    default_minutes = 240 if system_mode == "live_trading" else 1440
    try:
        lookback_minutes = int(os.getenv("PIPELINE_LOOKBACK_MINUTES", str(default_minutes)))
    except ValueError:
        lookback_minutes = default_minutes
    lookback_minutes = _arena_go_extended_lookback_minutes(now, lookback_minutes)
    lookback_minutes = max(1, lookback_minutes)
    return TimeRange(
        from_ts=to_utc_iso(now - timedelta(minutes=lookback_minutes)),
        to_ts=to_utc_iso(now),
        timezone="UTC",
    )


def _arena_go_extended_lookback_minutes(now, configured_minutes: int) -> int:
    if not _env_bool("ARENA_GO_SANDBOX", False):
        return configured_minutes
    if not _env_bool("ARENA_GO_MARKET_EXTENDED_SESSION", True):
        return configured_minutes
    if not _env_bool("ALLOW_ARENA_GO_EXTENDED_MARKET_DATA_GRACE", True):
        return configured_minutes
    zone = ZoneInfo(os.getenv("ARENA_GO_MARKET_TIMEZONE") or "Europe/Moscow")
    local_time = now.astimezone(zone).time()
    moex_close = _env_time("MOEX_MARKET_CLOSE_TIME", time(18, 50))
    arena_close = _env_time("ARENA_GO_MARKET_CLOSE_TIME", time(23, 50))
    if not (moex_close <= local_time < arena_close):
        return configured_minutes
    fallback_minutes = _env_int("ARENA_GO_EXTENDED_LOOKBACK_MINUTES", 480)
    grace_minutes = int(_env_int("ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS", 21600) / 60) + 60
    return max(configured_minutes, fallback_minutes, grace_minutes)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _env_time(name: str, default: time) -> time:
    value = str(os.getenv(name) or "").strip()
    if not value:
        return default
    parts = value.split(":")
    try:
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
        if len(parts) == 3:
            return time(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return default
    return default


if __name__ == "__main__":
    raise SystemExit(main())
