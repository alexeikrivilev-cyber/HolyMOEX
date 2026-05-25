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


@dataclass(frozen=True)
class MarketSession:
    market_session_status: str
    agent_runtime_phase: str
    as_of: datetime
    timezone: str
    reason: str


def current_market_session(now: datetime | None = None, env: dict[str, str] | None = None) -> MarketSession:
    env_map = env if env is not None else os.environ
    override = str(env_map.get("MARKET_SESSION_STATUS_OVERRIDE") or "").strip().lower()
    if override in VALID_MARKET_SESSION_STATUSES:
        resolved_now = _localized_now(now, env_map)
        return MarketSession(override, agent_runtime_phase(override), resolved_now, _timezone_name(env_map), "env_override")

    resolved_now = _localized_now(now, env_map)
    today = resolved_now.date()
    if _date_in_env(today, env_map.get("MOEX_FORCE_CLOSED_DATES", "")):
        return MarketSession("closed", "off_market", resolved_now, _timezone_name(env_map), "forced_closed_date")
    if _date_in_env(today, env_map.get("MOEX_FORCE_OPEN_DATES", "")):
        return _session_from_time(resolved_now, reason_prefix="forced_open_date")
    if resolved_now.weekday() >= 5:
        return MarketSession("closed", "off_market", resolved_now, _timezone_name(env_map), "weekend")
    return _session_from_time(resolved_now, reason_prefix="regular_weekday")


def agent_runtime_phase(market_session_status: str) -> str:
    status = str(market_session_status or "unknown").strip().lower()
    if status == "open":
        return "trading_session"
    if status in {"closed", "premarket", "postmarket"}:
        return "off_market"
    return "degraded"


def _session_from_time(now: datetime, *, reason_prefix: str) -> MarketSession:
    current = now.time()
    timezone_name = getattr(now.tzinfo, "key", DEFAULT_MARKET_TIMEZONE)
    if DEFAULT_PREMARKET_START <= current < DEFAULT_OPEN_TIME:
        return MarketSession("premarket", "off_market", now, timezone_name, f"{reason_prefix}:premarket")
    if DEFAULT_OPEN_TIME <= current < DEFAULT_CLOSE_TIME:
        return MarketSession("open", "trading_session", now, timezone_name, f"{reason_prefix}:main_session")
    if DEFAULT_CLOSE_TIME <= current < DEFAULT_POSTMARKET_END:
        return MarketSession("postmarket", "off_market", now, timezone_name, f"{reason_prefix}:postmarket")
    return MarketSession("closed", "off_market", now, timezone_name, f"{reason_prefix}:outside_session_hours")


def _localized_now(now: datetime | None, env: dict[str, str]) -> datetime:
    zone = ZoneInfo(_timezone_name(env))
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _timezone_name(env: dict[str, str]) -> str:
    return str(env.get("MOEX_MARKET_TIMEZONE") or DEFAULT_MARKET_TIMEZONE)


def _date_in_env(target: date, value: str) -> bool:
    dates = {item.strip() for item in str(value or "").split(",") if item.strip()}
    return target.isoformat() in dates
