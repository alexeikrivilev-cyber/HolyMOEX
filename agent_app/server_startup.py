from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg

from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, ModuleJob, RetryPolicy, TimeRange
from agent_app.contracts.unified_objects.module_job import to_utc_iso, utc_now
from agent_app.modules.external_request_gateway.repository import PostgresExternalRequestGatewayRepository
from agent_app.modules.external_request_gateway.service import ExternalRequestGatewayService
from agent_app.modules.portfolio_state.repository import PostgresPortfolioStateRepository, stable_record_id
from agent_app.modules.portfolio_state.service import PortfolioStateService
from agent_app.runtime_calendar import current_market_session


ALLOWED_TICKERS = (
    "LKOH",
    "SBER",
    "ROSN",
    "GAZP",
    "VTBR",
    "YDEX",
    "PLZL",
    "T",
    "NVTK",
    "X5",
    "GMKN",
    "MGNT",
    "ALRS",
    "AFLT",
    "CHMF",
    "NLMK",
    "MOEX",
    "SNGSP",
    "MTSS",
    "PIKK",
)
PLACEHOLDER_BOT_NAMES = {"", "arena_go_default", "mybot", "mytradingbot", "portfolio", "replace_with_exact_bots_name_from_get_bots"}
READINESS_VIEWS = (
    "audit.database_readiness_check",
    "audit.metric_weights_readiness_check",
    "audit.live_trading_readiness_check",
    "audit.allowed_universe_readiness_check",
)


class StartupPreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class BotIdentity:
    name: str
    cash_balance: float | None
    source: str


@dataclass(frozen=True)
class StartupResult:
    readiness_passed: bool
    arena_go_token_source: str
    arena_go_token_masked: str
    bot_name: str
    portfolio_name: str
    broker_sync_status: str
    market_session_status: str
    agent_runtime_phase: str
    market_session_reason: str
    readiness_rows: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "readiness_passed": self.readiness_passed,
            "arena_go_token_source": self.arena_go_token_source,
            "arena_go_token_masked": self.arena_go_token_masked,
            "bot_name": self.bot_name,
            "portfolio_name": self.portfolio_name,
            "broker_sync_status": self.broker_sync_status,
            "market_session_status": self.market_session_status,
            "agent_runtime_phase": self.agent_runtime_phase,
            "market_session_reason": self.market_session_reason,
            "readiness_rows": list(self.readiness_rows),
        }


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise StartupPreflightError("DATABASE_URL is required")
    return value


def _token_source() -> tuple[str, str]:
    sandbox_token = os.getenv("SANDBOX_API_KEY", "").strip()
    if sandbox_token:
        return "SANDBOX_API_KEY", sandbox_token
    fallback_token = os.getenv("ARENA_GO_TOKEN", "").strip()
    if fallback_token and _arena_go_token_fallback_allowed():
        return "ARENA_GO_TOKEN_FALLBACK", fallback_token
    return "", ""


def _arena_go_token_fallback_allowed() -> bool:
    if os.getenv("ALLOW_ARENA_GO_TOKEN_FALLBACK", "").lower() in {"1", "true", "yes"}:
        return True
    app_env = os.getenv("APP_ENV", "").strip().lower()
    return app_env in {"local", "dev", "development", "test"}


def _mask_token(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_bot(bots: Sequence[Mapping[str, Any]]) -> BotIdentity:
    names = [str(bot.get("name") or bot.get("bot") or "").strip() for bot in bots]
    names = [name for name in names if name]
    preferred = (os.getenv("ARENA_GO_BOT_NAME", ""), os.getenv("ARENA_GO_PORTFOLIO", ""))
    for candidate in preferred:
        text = str(candidate or "").strip()
        if text and text in names:
            bot = next(item for item in bots if str(item.get("name") or item.get("bot") or "").strip() == text)
            return BotIdentity(name=text, cash_balance=_float_or_none(bot.get("cash_balance") or bot.get("cash")), source="exact_env")
    for candidate in preferred:
        text = str(candidate or "").strip().lower()
        if text and text not in PLACEHOLDER_BOT_NAMES:
            for bot in bots:
                name = str(bot.get("name") or bot.get("bot") or "").strip()
                if name.lower() == text:
                    return BotIdentity(name=name, cash_balance=_float_or_none(bot.get("cash_balance") or bot.get("cash")), source="case_insensitive_env")
    if len(names) == 1:
        bot = next(item for item in bots if str(item.get("name") or item.get("bot") or "").strip() == names[0])
        return BotIdentity(name=names[0], cash_balance=_float_or_none(bot.get("cash_balance") or bot.get("cash")), source="single_bot_auto")
    raise StartupPreflightError("ArenaGo bot could not be resolved from /api/bots; set ARENA_GO_BOT_NAME to an exact bots[].name")


def _gateway(database_url: str) -> ExternalRequestGatewayService:
    return ExternalRequestGatewayService(repository=PostgresExternalRequestGatewayRepository(database_url))


def _arena_go_bots(database_url: str) -> BotIdentity:
    request = ExternalRequest(
        request_id=stable_record_id("startup_arena_go_get_bots", {"ts": to_utc_iso(utc_now())}),
        caller_module="Server Startup",
        provider="arena_go",
        request_type="get_bots",
        universe_id="moex_top20_manual",
        instrument_ids=(),
        payload={},
        cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
        timeout_ms=int(os.getenv("ARENA_GO_TIMEOUT_MS", "10000")),
        retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
        idempotency_key=stable_record_id("startup_get_bots_idem", {"ts": to_utc_iso(utc_now())}),
    )
    response = _gateway(database_url).process(request)
    if response.status not in {"success", "partial_success"}:
        raise StartupPreflightError(f"ArenaGo get_bots failed: {response.errors}")
    bots = _response_items(response.data or {})
    if not bots:
        raise StartupPreflightError("ArenaGo get_bots returned no bots")
    identity = _resolve_bot(bots)
    if identity.cash_balance is None:
        raise StartupPreflightError("ArenaGo bot resolved, but cash_balance is missing")
    return identity


def _sync_portfolio(database_url: str, bot_name: str) -> str:
    now = to_utc_iso(utc_now())
    broker_snapshot_ref = "startup:arena_go"
    price_snapshot_ref = "startup:latest_market_prices"
    os.environ["ARENA_GO_BOT_NAME"] = bot_name
    os.environ["ARENA_GO_PORTFOLIO"] = bot_name
    job = ModuleJob(
        job_id=stable_record_id("startup_portfolio_sync_job", {"as_of_ts": now, "bot": bot_name}),
        module_name="Portfolio State Module",
        contour="execution_contour",
        trigger_type="scheduled",
        universe_id="moex_top20_manual",
        instrument_ids=tuple(f"moex:{ticker}" for ticker in ALLOWED_TICKERS),
        horizons=("intraday",),
        time_range=TimeRange(from_ts=now, to_ts=now),
        input_refs=(broker_snapshot_ref, price_snapshot_ref),
        config_ref="portfolio_state:startup_live_sandbox:v1",
        run_mode="live_trading",
        idempotency_key=stable_record_id("startup_portfolio_sync_idem", {"as_of_ts": now, "bot": bot_name}),
    )
    service = PortfolioStateService(
        repository=PostgresPortfolioStateRepository(database_url),
        gateway=_gateway(database_url),
        config={"arena_go_bot_name": bot_name, "arena_go_portfolio": bot_name},
    )
    result = service.process(
        {
            "portfolio_update_request": {
                "portfolio_id": bot_name,
                "fill_report_refs": [],
                "broker_snapshot_ref": broker_snapshot_ref,
                "price_snapshot_ref": price_snapshot_ref,
                "run_mode": "live_trading",
                "as_of_ts": now,
            }
        },
        job,
    )
    if result.module_job_result.status == "success":
        return "success"
    if result.module_job_result.status == "partial_success":
        warnings = set(result.module_job_result.warnings)
        critical_warnings = {
            "broker_reconciliation_failed",
            "stale_portfolio_detected",
        }
        if not warnings.intersection(critical_warnings) and result.portfolio_snapshot.portfolio_id == bot_name:
            return "success"
    return result.module_job_result.status


def _readiness_rows(database_url: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    with psycopg.connect(database_url) as conn:
        for view_name in READINESS_VIEWS:
            try:
                result = conn.execute(
                    f"SELECT check_name, status, observed_value, expected_value, details FROM {view_name} ORDER BY check_name"
                )
                for check_name, status, observed_value, expected_value, details in result.fetchall():
                    rows.append(
                        {
                            "view": view_name,
                            "check_name": str(check_name),
                            "status": str(status),
                            "observed_value": str(observed_value),
                            "expected_value": str(expected_value),
                            "details": details,
                        }
                    )
            except Exception as error:
                rows.append(
                    {
                        "view": view_name,
                        "check_name": "view_query",
                        "status": "fail",
                        "observed_value": str(error),
                        "expected_value": "queryable readiness view",
                        "details": {},
                    }
                )
    return tuple(rows)


def _write_runtime_env(
    path: Path,
    *,
    bot_name: str,
    readiness_passed: bool,
    market_session_status: str,
    agent_runtime_phase: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                f"export ARENA_GO_BOT_NAME={_shell_quote(bot_name)}",
                f"export ARENA_GO_PORTFOLIO={_shell_quote(bot_name)}",
                f"export LIVE_READINESS_PASSED={_shell_quote('true' if readiness_passed else 'false')}",
                f"export STARTUP_MARKET_SESSION_STATUS={_shell_quote(market_session_status)}",
                f"export STARTUP_AGENT_RUNTIME_PHASE={_shell_quote(agent_runtime_phase)}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _audit_startup(database_url: str, result: StartupResult, reason_codes: Sequence[str]) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO audit.audit_record (
              module_name, severity, event_type, message, object_type, object_ref, reason_codes, payload
            ) VALUES (
              'Server Startup',
              %s,
              'automatic_live_trading_startup_preflight',
              %s,
              'runtime',
              'automatic_live_trading',
              %s,
              %s::jsonb
            )
            """,
            (
                "info" if result.readiness_passed else "warning",
                "Startup preflight completed for ArenaGo sandbox automatic live trading",
                list(reason_codes),
                json.dumps(result.to_dict(), ensure_ascii=True, default=str),
            ),
        )


def run_startup_preflight(database_url: str, runtime_env_file: Path, *, skip_external: bool = False) -> StartupResult:
    token_env, token = _token_source()
    if not token and not skip_external:
        raise StartupPreflightError(
            "SANDBOX_API_KEY is required for ArenaGo sandbox in server mode; "
            "ARENA_GO_TOKEN fallback requires APP_ENV=local/dev/test or ALLOW_ARENA_GO_TOKEN_FALLBACK=true"
        )
    if token_env == "ARENA_GO_TOKEN_FALLBACK":
        print("warning: using ARENA_GO_TOKEN fallback; prefer SANDBOX_API_KEY for server deployment")

    bot_name = os.getenv("ARENA_GO_BOT_NAME", "").strip() or os.getenv("ARENA_GO_PORTFOLIO", "").strip()
    broker_sync_status = "skipped"
    reason_codes: list[str] = []
    if skip_external:
        reason_codes.append("external_preflight_skipped")
        bot_name = bot_name if bot_name.lower() not in PLACEHOLDER_BOT_NAMES else "startup_external_skip"
    else:
        identity = _arena_go_bots(database_url)
        bot_name = identity.name
        broker_sync_status = _sync_portfolio(database_url, bot_name)
        if broker_sync_status != "success":
            reason_codes.append("portfolio_sync_failed")

    readiness_rows = _readiness_rows(database_url)
    readiness_passed = bool(readiness_rows) and all(row["status"] == "pass" for row in readiness_rows)
    readiness_passed = readiness_passed and (skip_external or broker_sync_status == "success") and bool(bot_name)
    if not readiness_passed:
        reason_codes.append("readiness_not_passed")

    market_session = current_market_session()
    _write_runtime_env(
        runtime_env_file,
        bot_name=bot_name,
        readiness_passed=readiness_passed,
        market_session_status=market_session.market_session_status,
        agent_runtime_phase=market_session.agent_runtime_phase,
    )
    result = StartupResult(
        readiness_passed=readiness_passed,
        arena_go_token_source=token_env or "missing",
        arena_go_token_masked=_mask_token(token),
        bot_name=bot_name,
        portfolio_name=bot_name,
        broker_sync_status=broker_sync_status,
        market_session_status=market_session.market_session_status,
        agent_runtime_phase=market_session.agent_runtime_phase,
        market_session_reason=market_session.reason,
        readiness_rows=readiness_rows,
    )
    _audit_startup(database_url, result, reason_codes)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HolyMOEX automatic live trading startup preflight")
    parser.add_argument("--runtime-env-file", default=os.getenv("RUNTIME_ENV_FILE", "/data/runtime/arena_go.env"))
    parser.add_argument("--skip-external", action="store_true", default=os.getenv("STARTUP_SKIP_EXTERNAL", "").lower() in {"1", "true", "yes"})
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_startup_preflight(_database_url(), Path(args.runtime_env_file), skip_external=bool(args.skip_external))
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2, default=str))
    return 0 if result.readiness_passed or bool(args.skip_external) else 2


if __name__ == "__main__":
    raise SystemExit(main())
