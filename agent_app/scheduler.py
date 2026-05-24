from __future__ import annotations

import argparse
import json
import os
import re
import signal
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent_app.main import main as run_orchestration_once


DEFAULT_AUTONOMOUS_SOURCES: tuple[str, ...] = (
    # Fresh portfolio/cash/positions before decisions.
    "Order Store",
    # Public/open data refresh and feature production path.
    "Raw Market Data Store",
    "Raw Macro Data Store",
    "Raw Text Store",
    # Decision/risk/execution path when fresh feature refs exist.
    "Feature Store",
    # Always close each cycle with monitoring.
    "Request Log Store",
    "Audit Log Store",
)

MODULE_TO_SOURCE: dict[str, str] = {
    "Portfolio State Module": "Order Store",
    "Market Data Metrics Module": "Raw Market Data Store",
    "Liquidity & Microstructure Module": "Raw Market Data Store",
    "Volatility & Risk Metrics Module": "Raw Market Data Store",
    "Derivatives & Positioning Module": "Raw Market Data Store",
    "Market Context Module": "Raw Macro Data Store",
    "Data Intake & Routing Module": "Raw Text Store",
    "Event & News Intelligence Module": "Raw Text Store",
    "Earnings & Dividend Intelligence Module": "Raw Text Store",
    "Fundamental & Valuation Module": "Raw Text Store",
    "Corporate Actions Adjustment Module": "Event Store",
    "Normalization & Feature Vector Module": "Feature Store",
    "Decision Engine Module": "Feature Store",
    "Risk Control Module": "Decision Engine Module",
    "Execution Engine Module": "Risk Control Module",
    "Monitoring & Audit Module": "Audit Log Store",
    "Selected Instruments Registry Module": "Selected Instruments DB",
}

EVENT_DRIVEN_TRIGGERS = {
    "on_decision_set",
    "on_approved_order",
    "on_event",
    "on_feature_update",
    "on_feature_vector_update",
    "dependency",
}


@dataclass(frozen=True)
class ScheduleEntry:
    schedule_config_id: str
    source: str
    payload_ref: str
    interval_seconds: float
    run_mode: str
    trigger_type: str = "scheduled"
    priority: str = "normal"


@dataclass(frozen=True)
class SchedulerConfig:
    interval_seconds: float = 60.0
    once: bool = False
    sources: tuple[str, ...] = DEFAULT_AUTONOMOUS_SOURCES
    system_mode: str = "paper_trading"
    universe_id: str = "moex_top20_manual"
    trigger_type: str = "scheduled"
    use_db_schedules: bool = True


class AutonomousScheduler:
    """Persistent schedule-aware worker for server/Docker runtime.

    The worker still keeps module boundaries intact: it never calls analytical,
    risk or execution modules directly.  When PostgreSQL is available it reads
    enabled records from ``audit.schedule_config`` and converts due schedules
    into Orchestration triggers.  Event-driven schedules (risk after decision,
    execution after approved order) are not triggered on a blind timer because
    they must be driven by current-cycle refs produced by upstream modules.
    Without database schedules the worker falls back to the explicit source list
    for local smoke tests.
    """

    def __init__(self, config: SchedulerConfig) -> None:
        self.config = config
        self._stop_requested = False
        self._last_run_by_schedule: dict[str, float] = {}

    def run(self) -> int:
        self._install_signal_handlers()
        exit_code = 0
        while not self._stop_requested:
            exit_code = self.run_once()
            if self.config.once:
                break
            self._sleep_with_stop(self.config.interval_seconds)
        return exit_code

    def run_once(self) -> int:
        entries = self._scheduled_entries()
        if entries:
            return self._run_due_entries(entries)
        return self._run_source_fallback()

    def _run_due_entries(self, entries: tuple[ScheduleEntry, ...]) -> int:
        now = time.monotonic()
        exit_code = 0
        for entry in entries:
            if self._stop_requested:
                break
            last_run = self._last_run_by_schedule.get(entry.schedule_config_id)
            if last_run is not None and now - last_run < entry.interval_seconds and not self.config.once:
                continue
            current = self._run_entry(entry)
            self._last_run_by_schedule[entry.schedule_config_id] = time.monotonic()
            if current != 0:
                exit_code = current
        return exit_code

    def _run_source_fallback(self) -> int:
        exit_code = 0
        for source in self.config.sources:
            if self._stop_requested:
                break
            entry = ScheduleEntry(
                schedule_config_id=f"fallback:{source}",
                source=source,
                payload_ref="",
                interval_seconds=self.config.interval_seconds,
                run_mode=self.config.system_mode,
                trigger_type=self.config.trigger_type,
            )
            current = self._run_entry(entry)
            if current != 0:
                exit_code = current
        return exit_code

    def _run_entry(self, entry: ScheduleEntry) -> int:
        argv = [
            "--trigger-type",
            entry.trigger_type,
            "--source",
            entry.source,
            "--payload-ref",
            entry.payload_ref,
            "--system-mode",
            entry.run_mode,
            "--universe-id",
            self.config.universe_id,
        ]
        if os.getenv("DATABASE_URL"):
            argv.append("--use-postgres")
        return run_orchestration_once(argv)

    def _scheduled_entries(self) -> tuple[ScheduleEntry, ...]:
        if not self.config.use_db_schedules:
            return ()
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            return ()
        try:
            return load_schedule_entries_from_postgres(database_url, default_run_mode=self.config.system_mode)
        except Exception as error:  # pragma: no cover - defensive runtime fallback
            print(
                json.dumps(
                    {
                        "event": "scheduler_schedule_load_failed",
                        "error": str(error),
                        "fallback_sources": list(self.config.sources),
                    },
                    ensure_ascii=False,
                )
            )
            return ()

    def stop(self, *_args: object) -> None:
        self._stop_requested = True

    def _install_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        except ValueError:
            # Non-main thread / embedded test runner.
            pass

    def _sleep_with_stop(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def load_schedule_entries_from_postgres(database_url: str, *, default_run_mode: str) -> tuple[ScheduleEntry, ...]:
    import psycopg

    query = """
        SELECT schedule_config_id, module_name, contour, schedule_payload
          FROM audit.schedule_config
         WHERE enabled = true
         ORDER BY schedule_config_id
    """
    entries: list[ScheduleEntry] = []
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
    for schedule_config_id, module_name, _contour, payload in rows:
        payload_map = dict(payload or {}) if isinstance(payload, Mapping) else {}
        entry = schedule_entry_from_payload(
            schedule_config_id=str(schedule_config_id),
            module_name=str(module_name),
            payload=payload_map,
            default_run_mode=default_run_mode,
        )
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def schedule_entry_from_payload(
    *,
    schedule_config_id: str,
    module_name: str,
    payload: Mapping[str, Any],
    default_run_mode: str,
) -> ScheduleEntry | None:
    trigger = str(payload.get("trigger") or "scheduled")
    if trigger in EVENT_DRIVEN_TRIGGERS and "interval_seconds" not in payload and "frequency" not in payload:
        return None
    interval = schedule_interval_seconds(payload)
    if interval is None:
        return None
    source = schedule_source(module_name, payload)
    run_mode = str(payload.get("run_mode") or default_run_mode)
    return ScheduleEntry(
        schedule_config_id=schedule_config_id,
        source=source,
        payload_ref=schedule_config_id,
        interval_seconds=interval,
        run_mode=run_mode,
        trigger_type="scheduled",
    )


def schedule_source(module_name: str, payload: Mapping[str, Any]) -> str:
    source = payload.get("source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    sources = payload.get("sources")
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)) and sources:
        first = str(sources[0]).strip()
        if first:
            return first
    return MODULE_TO_SOURCE.get(module_name, "Audit Log Store")


def schedule_interval_seconds(payload: Mapping[str, Any]) -> float | None:
    explicit = payload.get("interval_seconds")
    try:
        if explicit is not None and float(explicit) > 0:
            return float(explicit)
    except (TypeError, ValueError):
        pass
    frequency = str(payload.get("frequency") or payload.get("trigger") or "").strip().lower()
    if not frequency or frequency in EVENT_DRIVEN_TRIGGERS:
        return None
    if frequency in {"continuous", "scheduled", "scheduled_live_monitoring"}:
        return 60.0
    if frequency in {"daily_after_market_close", "after_market_close", "daily"}:
        return 86_400.0
    match = re.search(r"(\d+(?:\.\d+)?)([smhd])", frequency)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    multiplier = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86_400.0}[unit]
    return value * multiplier


def _env_sources() -> tuple[str, ...]:
    value = os.getenv("SCHEDULER_SOURCES", "")
    if not value.strip():
        return DEFAULT_AUTONOMOUS_SOURCES
    return tuple(item.strip() for item in value.split(",") if item.strip()) or DEFAULT_AUTONOMOUS_SOURCES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent autonomous scheduler for MOEX trading agent")
    parser.add_argument("--interval-seconds", type=float, default=float(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60")))
    parser.add_argument("--once", action="store_true", default=os.getenv("SCHEDULER_ONCE", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--source", action="append", default=[], help="Fallback source trigger to include in each cycle; repeatable")
    parser.add_argument("--system-mode", default=os.getenv("RUN_MODE", "paper_trading"))
    parser.add_argument("--universe-id", default=os.getenv("SELECTED_UNIVERSE_ID", "moex_top20_manual"))
    parser.add_argument("--trigger-type", default="scheduled")
    parser.add_argument("--no-db-schedules", action="store_true", help="Ignore audit.schedule_config and use fallback source loop")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = tuple(args.source) if args.source else _env_sources()
    config = SchedulerConfig(
        interval_seconds=args.interval_seconds,
        once=bool(args.once),
        sources=sources,
        system_mode=args.system_mode,
        universe_id=args.universe_id,
        trigger_type=args.trigger_type,
        use_db_schedules=not bool(args.no_db_schedules),
    )
    return AutonomousScheduler(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
