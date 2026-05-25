from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


VALID_MARKET_SESSION_STATUSES = {"open", "closed", "premarket", "postmarket", "unknown"}
DEFAULT_MARKET_TIMEZONE = "Europe/Moscow"
DEFAULT_OPEN_TIME = time(10, 0)
DEFAULT_CLOSE_TIME = time(18, 50)
DEFAULT_PREMARKET_START = time(9, 50)
DEFAULT_POSTMARKET_END = time(23, 50)
DEFAULT_ARENA_GO_CLOSE_TIME = time(23, 50)


@dataclass(frozen=True)
class MarketSession:
    market_session_status: str
    agent_runtime_phase: str
    as_of: datetime
    timezone: str
    reason: str


def current_market_session(now: datetime | None = None, env: dict[str, str] | None = None) -> MarketSession:
    env_map = env if env is not None else os.environ
    session_source = _session_source(env_map)
    override = str(env_map.get("MARKET_SESSION_STATUS_OVERRIDE") or "").strip().lower()
    if override in VALID_MARKET_SESSION_STATUSES:
        resolved_now = _localized_now(now, env_map, source=session_source)
        return MarketSession(override, agent_runtime_phase(override), resolved_now, _timezone_name(env_map, source=session_source), "env_override")

    resolved_now = _localized_now(now, env_map, source=session_source)
    if session_source == "arena_go":
        return _arena_go_session(resolved_now, env_map)
    return _moex_session(resolved_now, env_map)


def _moex_session(resolved_now: datetime, env_map: dict[str, str]) -> MarketSession:
    today = resolved_now.date()
    if _date_in_env(today, env_map.get("MOEX_FORCE_CLOSED_DATES", "")):
        return MarketSession("closed", "off_market", resolved_now, _timezone_name(env_map, source="moex"), "forced_closed_date")
    if _date_in_env(today, env_map.get("MOEX_FORCE_OPEN_DATES", "")):
        return _session_from_time(
            resolved_now,
            reason_prefix="forced_open_date",
            open_time=DEFAULT_OPEN_TIME,
            close_time=DEFAULT_CLOSE_TIME,
            premarket_start=DEFAULT_PREMARKET_START,
            postmarket_end=DEFAULT_POSTMARKET_END,
        )
    if resolved_now.weekday() >= 5:
        return MarketSession("closed", "off_market", resolved_now, _timezone_name(env_map, source="moex"), "weekend")
    return _session_from_time(
        resolved_now,
        reason_prefix="regular_weekday",
        open_time=DEFAULT_OPEN_TIME,
        close_time=DEFAULT_CLOSE_TIME,
        premarket_start=DEFAULT_PREMARKET_START,
        postmarket_end=DEFAULT_POSTMARKET_END,
    )


def _arena_go_session(resolved_now: datetime, env_map: dict[str, str]) -> MarketSession:
    today = resolved_now.date()
    forced_closed = env_map.get("ARENA_GO_FORCE_CLOSED_DATES") or env_map.get("MOEX_FORCE_CLOSED_DATES", "")
    forced_open = env_map.get("ARENA_GO_FORCE_OPEN_DATES") or env_map.get("MOEX_FORCE_OPEN_DATES", "")
    timezone_name = _timezone_name(env_map, source="arena_go")
    if _date_in_env(today, forced_closed):
        return MarketSession("closed", "off_market", resolved_now, timezone_name, "arena_go_forced_closed_date")

    weekend_allowed = _env_bool(env_map, "ARENA_GO_ALLOW_WEEKEND_SESSION", False)
    if resolved_now.weekday() >= 5 and not weekend_allowed and not _date_in_env(today, forced_open):
        return MarketSession("closed", "off_market", resolved_now, timezone_name, "arena_go_weekend")

    extended_session = _env_bool(env_map, "ARENA_GO_MARKET_EXTENDED_SESSION", True)
    close_time = _env_time(env_map, ("ARENA_GO_MARKET_CLOSE_TIME", "ARENA_GO_CLOSE_TIME"), DEFAULT_ARENA_GO_CLOSE_TIME if extended_session else DEFAULT_CLOSE_TIME)
    open_time = _env_time(env_map, ("ARENA_GO_MARKET_OPEN_TIME", "ARENA_GO_OPEN_TIME"), DEFAULT_OPEN_TIME)
    premarket_start = _env_time(env_map, ("ARENA_GO_PREMARKET_START",), DEFAULT_PREMARKET_START)
    postmarket_end = _env_time(env_map, ("ARENA_GO_POSTMARKET_END",), DEFAULT_POSTMARKET_END)
    reason_prefix = "arena_go_sandbox_weekday" if _env_bool(env_map, "ARENA_GO_SANDBOX", False) else "arena_go_configured_weekday"
    if _date_in_env(today, forced_open):
        reason_prefix = "arena_go_forced_open_date"
    if extended_session:
        reason_prefix = f"{reason_prefix}:extended_session"
    return _session_from_time(
        resolved_now,
        reason_prefix=reason_prefix,
        open_time=open_time,
        close_time=close_time,
        premarket_start=premarket_start,
        postmarket_end=postmarket_end,
    )


def agent_runtime_phase(market_session_status: str) -> str:
    status = str(market_session_status or "unknown").strip().lower()
    if status == "open":
        return "trading_session"
    if status in {"closed", "premarket", "postmarket"}:
        return "off_market"
    return "degraded"


def _session_from_time(
    now: datetime,
    *,
    reason_prefix: str,
    open_time: time,
    close_time: time,
    premarket_start: time,
    postmarket_end: time,
) -> MarketSession:
    current = now.time()
    timezone_name = getattr(now.tzinfo, "key", DEFAULT_MARKET_TIMEZONE)
    if premarket_start <= current < open_time:
        return MarketSession("premarket", "off_market", now, timezone_name, f"{reason_prefix}:premarket")
    if open_time <= current < close_time:
        return MarketSession("open", "trading_session", now, timezone_name, f"{reason_prefix}:main_session")
    if close_time <= current < postmarket_end:
        return MarketSession("postmarket", "off_market", now, timezone_name, f"{reason_prefix}:postmarket")
    return MarketSession("closed", "off_market", now, timezone_name, f"{reason_prefix}:outside_session_hours")


def _localized_now(now: datetime | None, env: dict[str, str], *, source: str) -> datetime:
    zone = ZoneInfo(_timezone_name(env, source=source))
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _timezone_name(env: dict[str, str], *, source: str) -> str:
    if source == "arena_go":
        return str(env.get("ARENA_GO_MARKET_TIMEZONE") or env.get("MOEX_MARKET_TIMEZONE") or DEFAULT_MARKET_TIMEZONE)
    return str(env.get("MOEX_MARKET_TIMEZONE") or DEFAULT_MARKET_TIMEZONE)


def _date_in_env(target: date, value: str) -> bool:
    dates = {item.strip() for item in str(value or "").split(",") if item.strip()}
    return target.isoformat() in dates


def _session_source(env: dict[str, str]) -> str:
    raw = str(
        env.get("MARKET_SESSION_SOURCE")
        or env.get("MARKET_CALENDAR_PROVIDER")
        or env.get("RUNTIME_MARKET_SESSION_SOURCE")
        or "auto"
    ).strip().lower()
    if raw in {"arena_go", "arenago", "arena"}:
        return "arena_go"
    if raw in {"moex", "moex_iss", "iss"}:
        return "moex"
    if _env_bool(env, "ARENA_GO_SANDBOX", False):
        return "arena_go"
    return "moex"


def _env_bool(env: dict[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_time(env: dict[str, str], names: tuple[str, ...], default: time) -> time:
    for name in names:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        parts = value.split(":")
        try:
            if len(parts) == 2:
                return time(int(parts[0]), int(parts[1]))
            if len(parts) == 3:
                return time(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
    return default
