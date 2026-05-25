from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent_app.main import main as run_orchestration_once
from agent_app.runtime_calendar import agent_runtime_phase, current_market_session


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

MARKET_SESSION_STATUSES = {"open", "closed", "premarket", "postmarket", "unknown"}
TRADING_HEAVY_MODULES = {
    "Decision Engine Module",
    "Risk Control Module",
    "Execution Engine Module",
}
LLM_HEAVY_MODULES = {
    "Event & News Intelligence Module",
    "Earnings & Dividend Intelligence Module",
    "Fundamental & Valuation Module",
}
TEXT_HEAVY_SOURCES = {"Raw Text Store"}
OFF_MARKET_ALLOWED_SOURCES: set[str] = set()
OFF_MARKET_ALLOWED_MODULES = {
    "Portfolio State Module",
    "Monitoring & Audit Module",
}
MIN_LLM_FALLBACK_INTERVAL_SECONDS = 900.0


@dataclass(frozen=True)
class ScheduleEntry:
    schedule_config_id: str
    module_name: str
    source: str
    payload_ref: str
    interval_seconds: float
    run_mode: str
    trigger_type: str = "scheduled"
    priority: str = "normal"
    direct_module_only: bool = False


@dataclass(frozen=True)
class SchedulerConfig:
    interval_seconds: float = 60.0
    once: bool = False
    sources: tuple[str, ...] = DEFAULT_AUTONOMOUS_SOURCES
    system_mode: str = "paper_trading"
    universe_id: str = "moex_top20_manual"
    trigger_type: str = "scheduled"
    use_db_schedules: bool = True
    lock_ttl_seconds: float = 120.0
    single_scheduler_instance: bool = False
    schedule_id_filter: tuple[str, ...] = ()
    max_entries_per_tick: int = 0
    allow_llm_fallback: bool = False
    raw_text_fallback_interval_seconds: float = 1800.0


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
        self._runtime_checked = False
        self._runtime_ready = True
        self._owner_id = os.getenv("SCHEDULER_OWNER_ID") or f"{socket.gethostname()}:{os.getpid()}"
        self._last_fallback_warning_at: float = 0.0

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
        if not self._ensure_runtime_mode():
            return 2
        market_status = self.market_session_status()
        runtime_phase = agent_runtime_phase(market_status)
        entries = self._scheduled_entries()
        if entries:
            return self._run_due_entries(entries, market_status=market_status, runtime_phase=runtime_phase)
        return self._run_source_fallback(market_status=market_status, runtime_phase=runtime_phase)

    def _run_due_entries(self, entries: tuple[ScheduleEntry, ...], *, market_status: str, runtime_phase: str) -> int:
        now = time.monotonic()
        exit_code = 0
        executed_count = 0
        for entry in entries:
            if self._stop_requested:
                break
            if self.config.max_entries_per_tick > 0 and executed_count >= self.config.max_entries_per_tick:
                break
            last_run = self._last_run_by_schedule.get(entry.schedule_config_id)
            if last_run is not None and now - last_run < entry.interval_seconds and not self.config.once:
                self._log_tick_event(entry, "scheduler_tick_skipped", "interval_not_due")
                continue
            skip_reason = self.skip_reason(entry, market_status=market_status, runtime_phase=runtime_phase, db_schedule=True)
            if skip_reason:
                self._log_tick_event(entry, "scheduler_tick_skipped", skip_reason, market_status=market_status, runtime_phase=runtime_phase)
                self._audit_scheduler_warning(
                    os.getenv("DATABASE_URL", ""),
                    "scheduler_entry_off_market_skipped",
                    f"Scheduler skipped {entry.schedule_config_id}: {skip_reason}.",
                    (skip_reason, f"market_session_status:{market_status}", f"agent_runtime_phase:{runtime_phase}"),
                    severity="warning" if market_status == "unknown" else "info",
                    payload={
                        "schedule_config_id": entry.schedule_config_id,
                        "module_name": entry.module_name,
                        "source": entry.source,
                        "market_session_status": market_status,
                        "agent_runtime_phase": runtime_phase,
                    },
                )
                self._last_run_by_schedule[entry.schedule_config_id] = time.monotonic()
                continue
            if not self._acquire_tick_lock(entry):
                self._log_tick_event(entry, "scheduler_tick_skipped", "lock_not_acquired")
                continue
            self._log_tick_event(entry, "scheduler_tick_locked", "lock_acquired")
            current = self._run_entry(entry)
            executed_count += 1
            self._last_run_by_schedule[entry.schedule_config_id] = time.monotonic()
            if current != 0:
                exit_code = current
        return exit_code

    def _run_source_fallback(self, *, market_status: str, runtime_phase: str) -> int:
        exit_code = 0
        for source in self.config.sources:
            if self._stop_requested:
                break
            entry = ScheduleEntry(
                schedule_config_id=f"fallback:{source}",
                module_name=fallback_module_name(source),
                source=source,
                payload_ref="",
                interval_seconds=(
                    self.config.raw_text_fallback_interval_seconds
                    if source in TEXT_HEAVY_SOURCES
                    else self.config.interval_seconds
                ),
                run_mode=self.config.system_mode,
                trigger_type=self.config.trigger_type,
            )
            last_run = self._last_run_by_schedule.get(entry.schedule_config_id)
            if last_run is not None and time.monotonic() - last_run < entry.interval_seconds and not self.config.once:
                self._log_tick_event(entry, "scheduler_tick_skipped", "interval_not_due", market_status=market_status, runtime_phase=runtime_phase)
                continue
            skip_reason = self.skip_reason(entry, market_status=market_status, runtime_phase=runtime_phase, db_schedule=False)
            if skip_reason:
                self._log_tick_event(entry, "scheduler_tick_skipped", skip_reason, market_status=market_status, runtime_phase=runtime_phase)
                if skip_reason == "raw_text_fallback_disabled_in_production":
                    self._audit_raw_text_fallback_disabled()
                continue
            if not self._acquire_tick_lock(entry):
                self._log_tick_event(entry, "scheduler_tick_skipped", "lock_not_acquired")
                continue
            self._log_tick_event(entry, "scheduler_tick_locked", "lock_acquired")
            current = self._run_entry(entry)
            self._last_run_by_schedule[entry.schedule_config_id] = time.monotonic()
            if current != 0:
                exit_code = current
        return exit_code

    def _log_tick_event(
        self,
        entry: ScheduleEntry,
        event: str,
        reason: str,
        *,
        market_status: str | None = None,
        runtime_phase: str | None = None,
    ) -> None:
        print(
            json.dumps(
                {
                    "event": event,
                    "schedule_config_id": entry.schedule_config_id,
                    "module_name": entry.module_name,
                    "source": entry.source,
                    "run_mode": entry.run_mode,
                    "reason": reason,
                    "owner_id": self._owner_id,
                    "market_session_status": market_status or self.market_session_status(),
                    "agent_runtime_phase": runtime_phase or agent_runtime_phase(market_status or self.market_session_status()),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def _run_entry(self, entry: ScheduleEntry) -> int:
        started = time.monotonic()
        print(
            json.dumps(
                {
                    "event": "scheduler_entry_started",
                    "schedule_config_id": entry.schedule_config_id,
                    "module_name": entry.module_name,
                    "source": entry.source,
                    "run_mode": entry.run_mode,
                    "owner_id": self._owner_id,
                    "market_session_status": self.market_session_status(),
                    "agent_runtime_phase": agent_runtime_phase(self.market_session_status()),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        argv = [
            "--trigger-type",
            "manual" if entry.direct_module_only else entry.trigger_type,
            "--source",
            entry.source,
            "--payload-ref",
            f"module:{entry.module_name}" if entry.direct_module_only else entry.payload_ref,
            "--system-mode",
            entry.run_mode,
            "--universe-id",
            self.config.universe_id,
        ]
        if os.getenv("DATABASE_URL"):
            argv.append("--use-postgres")
        exit_code = run_orchestration_once(argv)
        print(
            json.dumps(
                {
                    "event": "scheduler_entry_finished",
                    "schedule_config_id": entry.schedule_config_id,
                    "module_name": entry.module_name,
                    "source": entry.source,
                    "run_mode": entry.run_mode,
                    "exit_code": exit_code,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "owner_id": self._owner_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return exit_code

    def market_session_status(self) -> str:
        status = str(os.getenv("MARKET_SESSION_STATUS_OVERRIDE") or "").strip().lower()
        if status in MARKET_SESSION_STATUSES:
            return status
        session = current_market_session()
        if session.market_session_status in MARKET_SESSION_STATUSES and session.market_session_status != "unknown":
            return session.market_session_status
        database_url = os.getenv("DATABASE_URL", "")
        if database_url:
            try:
                db_status = load_market_session_status_from_postgres(database_url)
                if db_status in MARKET_SESSION_STATUSES:
                    return db_status
            except Exception:
                pass
        if session.market_session_status in MARKET_SESSION_STATUSES:
            return session.market_session_status
        return "unknown"

    def skip_reason(self, entry: ScheduleEntry, *, market_status: str, runtime_phase: str, db_schedule: bool) -> str:
        del runtime_phase
        if market_status != "open" and is_trading_heavy_entry(entry):
            return "off_market_heavy_trading_loop_blocked" if market_status != "unknown" else "market_session_unknown_live_loop_blocked"
        if market_status != "open" and is_llm_heavy_entry(entry):
            return "off_market_text_llm_loop_blocked" if market_status != "unknown" else "market_session_unknown_text_llm_loop_blocked"
        if production_env() and is_llm_heavy_entry(entry) and not _env_bool("ENABLE_LLM_TEXT_SCHEDULES", False):
            return "llm_text_schedule_disabled_in_production"
        if market_status != "open" and not is_off_market_allowed_entry(entry):
            return "off_market_scheduled_loop_blocked" if market_status != "unknown" else "market_session_unknown_scheduled_loop_blocked"
        if not db_schedule and entry.source in TEXT_HEAVY_SOURCES and production_env():
            if not self.config.allow_llm_fallback:
                return "raw_text_fallback_disabled_in_production"
            if self.config.raw_text_fallback_interval_seconds < MIN_LLM_FALLBACK_INTERVAL_SECONDS:
                return "raw_text_fallback_interval_too_low"
        return ""

    def _audit_raw_text_fallback_disabled(self) -> None:
        now = time.monotonic()
        if now - self._last_fallback_warning_at < 300:
            return
        self._last_fallback_warning_at = now
        self._audit_scheduler_warning(
            os.getenv("DATABASE_URL", ""),
            "raw_text_fallback_disabled_in_production",
            "Schedule config is unavailable and production Raw Text/EventNews fallback is disabled by default.",
            ("raw_text_fallback_disabled_in_production", "explicit_allow_llm_fallback_required"),
            payload={
                "allow_llm_fallback": self.config.allow_llm_fallback,
                "raw_text_fallback_interval_seconds": self.config.raw_text_fallback_interval_seconds,
                "min_interval_seconds": MIN_LLM_FALLBACK_INTERVAL_SECONDS,
            },
        )

    def _ensure_runtime_mode(self) -> bool:
        if self._runtime_checked:
            return self._runtime_ready
        self._runtime_checked = True
        database_url = os.getenv("DATABASE_URL", "")
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            self._audit_scheduler_warning(
                database_url,
                "scheduler_redis_not_used",
                "Redis URL is configured, but this runtime uses PostgreSQL advisory tick locks plus single-leader deployment policy.",
                ("redis_optional", "postgres_tick_lock_enabled"),
            )
        if not redis_url and not self.config.single_scheduler_instance:
            self._runtime_ready = False
            self._audit_scheduler_warning(
                database_url,
                "scheduler_single_leader_required",
                "Redis is unavailable and SINGLE_SCHEDULER_INSTANCE is not true; scheduler refused to start to avoid duplicate jobs.",
                ("redis_unavailable", "single_leader_required"),
                severity="error",
            )
            return False
        if not redis_url and self.config.single_scheduler_instance:
            self._audit_scheduler_warning(
                database_url,
                "scheduler_single_leader_mode",
                "Redis is unavailable; scheduler is running in explicit single-leader mode with PostgreSQL tick locks.",
                ("redis_unavailable", "single_leader_mode", "postgres_tick_lock_enabled"),
            )
        return True

    def _acquire_tick_lock(self, entry: ScheduleEntry) -> bool:
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            return True
        try:
            return acquire_scheduler_tick_lock(
                database_url,
                schedule_config_id=entry.schedule_config_id,
                owner_id=self._owner_id,
                ttl_seconds=self.config.lock_ttl_seconds,
            )
        except Exception as error:  # pragma: no cover - defensive runtime fallback
            print(
                json.dumps(
                    {
                        "event": "scheduler_tick_lock_failed",
                        "schedule_config_id": entry.schedule_config_id,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                )
            )
            return bool(self.config.single_scheduler_instance)

    def _audit_scheduler_warning(
        self,
        database_url: str,
        event_type: str,
        message: str,
        reason_codes: tuple[str, ...],
        *,
        severity: str = "warning",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        audit_payload = {
            "owner_id": self._owner_id,
            "single_scheduler_instance": self.config.single_scheduler_instance,
            "lock_ttl_seconds": self.config.lock_ttl_seconds,
        }
        audit_payload.update(dict(payload or {}))
        if database_url:
            try:
                write_scheduler_audit(database_url, event_type, message, reason_codes, severity=severity, payload=audit_payload)
                return
            except Exception:
                pass
        print(
            json.dumps(
                {
                    "event": event_type,
                    "severity": severity,
                    "message": message,
                    "reason_codes": list(reason_codes),
                    "payload": audit_payload,
                },
                ensure_ascii=False,
            )
        )

    def _scheduled_entries(self) -> tuple[ScheduleEntry, ...]:
        if not self.config.use_db_schedules:
            return ()
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            return ()
        try:
            entries = load_schedule_entries_from_postgres(database_url, default_run_mode=self.config.system_mode)
            if self.config.schedule_id_filter:
                wanted = set(self.config.schedule_id_filter)
                entries = tuple(entry for entry in entries if entry.schedule_config_id in wanted)
            return entries
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
    return tuple(sorted(entries, key=schedule_priority))


def load_market_session_status_from_postgres(database_url: str) -> str:
    import psycopg

    query = """
        SELECT payload ->> 'market_session_status'
          FROM portfolio.portfolio_snapshot
         WHERE payload ? 'market_session_status'
         ORDER BY as_of_ts DESC
         LIMIT 1
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
    return str(row[0]).strip().lower() if row and row[0] else "unknown"


def acquire_scheduler_tick_lock(
    database_url: str,
    *,
    schedule_config_id: str,
    owner_id: str,
    ttl_seconds: float,
) -> bool:
    import psycopg

    query = """
        INSERT INTO audit.scheduler_tick_lock (
            schedule_config_id, owner_id, locked_until, last_tick_at, tick_count
        ) VALUES (
            %s, %s, now() + (%s::text || ' seconds')::interval, now(), 1
        )
        ON CONFLICT (schedule_config_id) DO UPDATE SET
            owner_id = EXCLUDED.owner_id,
            locked_until = EXCLUDED.locked_until,
            last_tick_at = now(),
            tick_count = audit.scheduler_tick_lock.tick_count + 1
        WHERE audit.scheduler_tick_lock.locked_until <= now()
           OR audit.scheduler_tick_lock.owner_id = EXCLUDED.owner_id
        RETURNING schedule_config_id
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (schedule_config_id, owner_id, max(1, int(ttl_seconds))))
            row = cur.fetchone()
    return row is not None


def write_scheduler_audit(
    database_url: str,
    event_type: str,
    message: str,
    reason_codes: tuple[str, ...],
    *,
    severity: str,
    payload: Mapping[str, Any],
) -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit.audit_record (
                    module_name, severity, event_type, message,
                    object_type, object_ref, reason_codes, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "Orchestration Module",
                    severity,
                    event_type,
                    message,
                    "scheduler_worker",
                    "agent_app.scheduler",
                    list(reason_codes),
                    Jsonb(dict(payload)),
                ),
            )


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
    run_mode = schedule_run_mode(payload, default_run_mode)
    return ScheduleEntry(
        schedule_config_id=schedule_config_id,
        module_name=module_name,
        source=source,
        payload_ref=schedule_config_id,
        interval_seconds=interval,
        run_mode=run_mode,
        trigger_type="scheduled",
        direct_module_only=bool(payload.get("direct_module_only", False)),
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


def fallback_module_name(source: str) -> str:
    for module_name, mapped_source in MODULE_TO_SOURCE.items():
        if mapped_source == source:
            return module_name
    return "Orchestration Module"


def is_trading_heavy_entry(entry: ScheduleEntry) -> bool:
    return entry.module_name in TRADING_HEAVY_MODULES or entry.schedule_config_id.startswith(
        (
            "schedule:live_autonomous:decision",
            "schedule:live_autonomous:risk",
            "schedule:live_autonomous:execution",
        )
    )


def is_llm_heavy_entry(entry: ScheduleEntry) -> bool:
    return entry.module_name in LLM_HEAVY_MODULES or entry.source in TEXT_HEAVY_SOURCES


def is_off_market_allowed_entry(entry: ScheduleEntry) -> bool:
    return entry.module_name in OFF_MARKET_ALLOWED_MODULES or entry.source in OFF_MARKET_ALLOWED_SOURCES


def schedule_run_mode(payload: Mapping[str, Any], default_run_mode: str) -> str:
    configured = str(payload.get("run_mode") or default_run_mode)
    if (
        default_run_mode == "live_trading"
        and configured != "live_trading"
        and not _env_bool("ALLOW_NON_LIVE_RUNTIME", False)
    ):
        return "live_trading"
    return configured


def production_env() -> bool:
    return str(os.getenv("APP_ENV") or os.getenv("ENV") or "production").strip().lower() in {"prod", "production", "server"}


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


def schedule_priority(entry: ScheduleEntry) -> tuple[int, str]:
    """Keep trading-critical live schedules ahead of text/LLM maintenance."""
    priority_by_id = {
        "schedule:live_autonomous:portfolio_sync:1m": 0,
        "schedule:live_autonomous:market_data:1m": 1,
        "schedule:market_data_metrics:intraday": 2,
        "schedule:liquidity_microstructure:realtime": 3,
        "schedule:volatility_risk:realtime": 4,
        "schedule:live_autonomous:decision:1m": 5,
        "schedule:live_autonomous:monitoring:1m": 6,
    }
    if entry.schedule_config_id in priority_by_id:
        return (priority_by_id[entry.schedule_config_id], entry.schedule_config_id)
    if entry.module_name in LLM_HEAVY_MODULES or entry.source in TEXT_HEAVY_SOURCES:
        return (80, entry.schedule_config_id)
    if entry.module_name in OFF_MARKET_ALLOWED_MODULES:
        return (10, entry.schedule_config_id)
    return (50, entry.schedule_config_id)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_sources() -> tuple[str, ...]:
    value = os.getenv("SCHEDULER_SOURCES", "")
    if not value.strip():
        return DEFAULT_AUTONOMOUS_SOURCES
    return tuple(item.strip() for item in value.split(",") if item.strip()) or DEFAULT_AUTONOMOUS_SOURCES


def _env_schedule_ids() -> tuple[str, ...]:
    value = os.getenv("SCHEDULER_SCHEDULE_IDS", "")
    return tuple(item.strip() for item in value.split(",") if item.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent autonomous scheduler for MOEX trading agent")
    parser.add_argument("--interval-seconds", type=float, default=float(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60")))
    parser.add_argument("--once", action="store_true", default=os.getenv("SCHEDULER_ONCE", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--source", action="append", default=[], help="Fallback source trigger to include in each cycle; repeatable")
    parser.add_argument("--system-mode", default=os.getenv("RUN_MODE", "paper_trading"))
    parser.add_argument("--universe-id", default=os.getenv("SELECTED_UNIVERSE_ID", "moex_top20_manual"))
    parser.add_argument("--trigger-type", default="scheduled")
    parser.add_argument("--no-db-schedules", action="store_true", help="Ignore audit.schedule_config and use fallback source loop")
    parser.add_argument("--lock-ttl-seconds", type=float, default=float(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "120")))
    parser.add_argument("--schedule-id", action="append", default=[], help="Only run matching audit.schedule_config id; repeatable")
    parser.add_argument("--max-entries-per-tick", type=int, default=int(os.getenv("SCHEDULER_MAX_ENTRIES_PER_TICK", "0")))
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
        lock_ttl_seconds=args.lock_ttl_seconds,
        single_scheduler_instance=os.getenv("SINGLE_SCHEDULER_INSTANCE", "").lower() in {"1", "true", "yes"},
        schedule_id_filter=tuple(args.schedule_id) if args.schedule_id else _env_schedule_ids(),
        max_entries_per_tick=max(0, int(args.max_entries_per_tick)),
        allow_llm_fallback=_env_bool("ALLOW_LLM_FALLBACK", False),
        raw_text_fallback_interval_seconds=max(0.0, _env_float("RAW_TEXT_FALLBACK_INTERVAL_SECONDS", 1800.0)),
    )
    return AutonomousScheduler(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
