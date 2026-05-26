from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, RetryPolicy
from agent_app.contracts.unified_objects.module_job import to_utc_iso, utc_now
from agent_app.modules.external_request_gateway.repository import PostgresExternalRequestGatewayRepository
from agent_app.modules.external_request_gateway.service import ExternalRequestGatewayService


VALID_SESSION_STATUSES = {"open", "closed", "premarket", "postmarket", "unknown"}
PROBE_EVENT_TYPE = "arena_go_session_probe"
PROBE_MODULE_NAME = "Runtime Calendar"
PROBE_OBJECT_REF = "arena_go:sandbox"
PLACEHOLDER_BOT_NAMES = {"", "arena_go_default", "mybot", "mytradingbot", "portfolio"}


@dataclass(frozen=True)
class ArenaGoSessionProbeResult:
    market_session_status: str
    reason: str
    source: str
    as_of: datetime
    bot_name: str = ""
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    from_cache: bool = False


def arena_go_session_probe_enabled(env: Mapping[str, str] | None = None) -> bool:
    env_map = env if env is not None else os.environ
    explicit = env_map.get("ARENA_GO_SESSION_PROBE_ENABLED")
    if explicit is not None:
        return _env_bool(env_map, "ARENA_GO_SESSION_PROBE_ENABLED", False)
    source = str(env_map.get("MARKET_SESSION_SOURCE") or "auto").strip().lower()
    return _env_bool(env_map, "ARENA_GO_SANDBOX", False) and source in {"auto", "arena_go", "arenago", "arena"}


def arena_go_session_probe_required(env: Mapping[str, str] | None = None) -> bool:
    env_map = env if env is not None else os.environ
    return _env_bool(env_map, "ARENA_GO_SESSION_PROBE_REQUIRED_FOR_OPEN", False)


def maybe_probe_arena_go_session(
    database_url: str,
    *,
    env: Mapping[str, str] | None = None,
    min_interval_seconds: float | None = None,
) -> ArenaGoSessionProbeResult | None:
    env_map = env if env is not None else os.environ
    if not arena_go_session_probe_enabled(env_map) or not database_url:
        return None
    interval = min_interval_seconds
    if interval is None:
        interval = _env_float(env_map, "ARENA_GO_SESSION_PROBE_INTERVAL_SECONDS", 120.0)
    cached = read_recent_arena_go_session_probe(database_url, max_age_seconds=max(1.0, interval))
    if cached is not None:
        return ArenaGoSessionProbeResult(
            market_session_status=cached["market_session_status"],
            reason=cached.get("reason", "recent_arena_go_session_probe"),
            source=cached.get("source", "arena_go_session_probe"),
            as_of=cached["created_at"],
            bot_name=cached.get("bot_name", ""),
            warnings=tuple(cached.get("warnings", ())),
            errors=tuple(cached.get("errors", ())),
            from_cache=True,
        )
    return probe_arena_go_session(database_url, env=env_map)


def probe_arena_go_session(
    database_url: str,
    *,
    env: Mapping[str, str] | None = None,
) -> ArenaGoSessionProbeResult:
    env_map = env if env is not None else os.environ
    now = utc_now()
    cooldown = _env_float(env_map, "ARENA_GO_MARKET_CLOSED_COOLDOWN_SECONDS", 180.0)
    recent_closed = latest_market_closed_response(database_url, max_age_seconds=cooldown)
    if recent_closed is not None:
        result = ArenaGoSessionProbeResult(
            market_session_status="closed",
            reason="recent_arena_go_market_closed_response",
            source="arena_go_submit_order",
            as_of=now,
            warnings=("arena_go_market_closed_cooldown_active",),
            errors=(),
        )
        write_arena_go_session_probe(database_url, result)
        return result

    gateway = ExternalRequestGatewayService(repository=PostgresExternalRequestGatewayRepository(database_url))
    bots_response = gateway.process(_arena_go_request("get_bots", env_map, now, payload={}))
    if bots_response.status not in {"success", "partial_success"}:
        result = ArenaGoSessionProbeResult(
            market_session_status="unknown",
            reason="arena_go_get_bots_failed",
            source="arena_go_session_probe",
            as_of=now,
            errors=tuple(bots_response.errors or ("get_bots_failed",)),
        )
        write_arena_go_session_probe(database_url, result)
        return result

    bots = _response_items(bots_response.data or {})
    bot_name = _resolve_bot_name(bots, env_map)
    if not bot_name:
        result = ArenaGoSessionProbeResult(
            market_session_status="unknown",
            reason="arena_go_bot_identity_unresolved",
            source="arena_go_session_probe",
            as_of=now,
            errors=("arena_go_bot_identity_unresolved",),
        )
        write_arena_go_session_probe(database_url, result)
        return result

    require_positions = _env_bool(env_map, "ARENA_GO_SESSION_PROBE_REQUIRE_POSITIONS", True)
    if require_positions:
        positions_response = gateway.process(
            _arena_go_request(
                "get_positions",
                env_map,
                now,
                payload={"portfolio": bot_name, "bot": bot_name},
            )
        )
        if positions_response.status not in {"success", "partial_success"}:
            result = ArenaGoSessionProbeResult(
                market_session_status="unknown",
                reason="arena_go_get_positions_failed",
                source="arena_go_session_probe",
                as_of=now,
                bot_name=bot_name,
                errors=tuple(positions_response.errors or ("get_positions_failed",)),
            )
            write_arena_go_session_probe(database_url, result)
            return result

    result = ArenaGoSessionProbeResult(
        market_session_status="open",
        reason="arena_go_broker_session_probe_success",
        source="arena_go_session_probe",
        as_of=now,
        bot_name=bot_name,
        warnings=("market_session_status_provider_driven",),
    )
    write_arena_go_session_probe(database_url, result)
    return result


def read_recent_arena_go_session_probe(
    database_url: str,
    *,
    max_age_seconds: float,
) -> dict[str, Any] | None:
    if not database_url:
        return None
    try:
        import psycopg
    except Exception:
        return None
    query = """
        SELECT created_at, payload
          FROM audit.audit_record
         WHERE event_type = %s
           AND object_ref = %s
         ORDER BY created_at DESC
         LIMIT 1
    """
    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (PROBE_EVENT_TYPE, PROBE_OBJECT_REF))
                row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    created_at = row[0]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - created_at > timedelta(seconds=max(1.0, max_age_seconds)):
        return None
    payload = dict(row[1] or {})
    status = str(payload.get("market_session_status") or "").strip().lower()
    if status not in VALID_SESSION_STATUSES:
        return None
    payload["market_session_status"] = status
    payload["created_at"] = created_at
    return payload


def latest_market_closed_response(database_url: str, *, max_age_seconds: float) -> datetime | None:
    try:
        import psycopg
    except Exception:
        return None
    query = """
        SELECT COALESCE(last_update_at, submitted_at)
          FROM orders.execution_result
         WHERE (
                errors && ARRAY['market_closed']::text[]
             OR payload ->> 'market_session_status' = 'closed'
         )
           AND COALESCE(last_update_at, submitted_at) >= now() - (%s::text || ' seconds')::interval
         ORDER BY COALESCE(last_update_at, submitted_at) DESC
         LIMIT 1
    """
    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (max(1, int(max_age_seconds)),))
                row = cur.fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    value = row[0]
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def write_arena_go_session_probe(database_url: str, result: ArenaGoSessionProbeResult) -> None:
    try:
        import psycopg
    except Exception:
        return
    severity = "info" if result.market_session_status == "open" else "warning"
    reason_codes = tuple(
        dict.fromkeys(
            (
                f"market_session_status:{result.market_session_status}",
                result.reason,
                *result.warnings,
                *result.errors,
            )
        )
    )
    payload = {
        "market_session_status": result.market_session_status,
        "agent_runtime_phase": "trading_session" if result.market_session_status == "open" else "off_market"
        if result.market_session_status in {"closed", "premarket", "postmarket"}
        else "degraded",
        "reason": result.reason,
        "source": result.source,
        "as_of": to_utc_iso(result.as_of),
        "bot_name": result.bot_name,
        "warnings": list(result.warnings),
        "errors": list(result.errors),
    }
    query = """
        INSERT INTO audit.audit_record (
          module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            query,
            (
                PROBE_MODULE_NAME,
                severity,
                PROBE_EVENT_TYPE,
                "ArenaGo sandbox session probe completed",
                "market_session",
                PROBE_OBJECT_REF,
                list(reason_codes),
                json.dumps(payload, ensure_ascii=True, default=str),
            ),
        )


def _arena_go_request(
    request_type: str,
    env: Mapping[str, str],
    now: datetime,
    *,
    payload: Mapping[str, Any],
) -> ExternalRequest:
    bucket_seconds = max(1, int(_env_float(env, "ARENA_GO_SESSION_PROBE_INTERVAL_SECONDS", 120.0)))
    bucket = int(now.timestamp() // bucket_seconds)
    identity = _stable_id("arena_go_session_probe", {"request_type": request_type, "bucket": bucket, "payload": dict(payload)})
    return ExternalRequest(
        request_id=identity,
        caller_module=PROBE_MODULE_NAME,
        provider="arena_go",
        request_type=request_type,
        universe_id=env.get("SELECTED_UNIVERSE_ID") or "moex_top20_manual",
        instrument_ids=(),
        payload={**dict(payload), "probe_type": "market_session_availability"},
        cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
        timeout_ms=int(_env_float(env, "ARENA_GO_SESSION_PROBE_TIMEOUT_MS", 10000.0)),
        retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
        idempotency_key=f"{identity}:idem",
    )


def _response_items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    data = payload.get("data")
    if isinstance(data, Mapping):
        payload = data
    for key in ("items", "bots", "positions", "trades", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return tuple(dict(item) for item in value if isinstance(item, Mapping))
    if any(key in payload for key in ("name", "bot", "cash_balance")):
        return (dict(payload),)
    return ()


def _resolve_bot_name(bots: tuple[Mapping[str, Any], ...], env: Mapping[str, str]) -> str:
    names = [str(bot.get("name") or bot.get("bot") or "").strip() for bot in bots]
    names = [name for name in names if name]
    preferred = (env.get("ARENA_GO_BOT_NAME", ""), env.get("ARENA_GO_PORTFOLIO", ""))
    for candidate in preferred:
        text = str(candidate or "").strip()
        if text and text in names:
            return text
    for candidate in preferred:
        text = str(candidate or "").strip().lower()
        if text and text not in PLACEHOLDER_BOT_NAMES:
            for name in names:
                if name.lower() == text:
                    return name
    return names[0] if len(names) == 1 else ""


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:32]}"


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, str(default)))
    except (TypeError, ValueError):
        return default
