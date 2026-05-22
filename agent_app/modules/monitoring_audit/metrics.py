from __future__ import annotations

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


def system_uptime(uptime_seconds: float | None, total_observation_seconds: float | None) -> float:
    if uptime_seconds is None or total_observation_seconds is None or total_observation_seconds <= 0:
        return 0.0
    return _clip(float(uptime_seconds) / float(total_observation_seconds))


def module_latency_ms(started_at: str | None, finished_at: str | None) -> float:
    if not started_at or not finished_at:
        return 0.0
    started = parse_utc_iso(started_at)
    finished = parse_utc_iso(finished_at)
    return max(0.0, (finished - started).total_seconds() * 1000.0)


def module_error_rate(failed_module_jobs: int, total_module_jobs: int) -> float:
    if total_module_jobs <= 0:
        return 0.0
    return _clip(failed_module_jobs / total_module_jobs)


def data_staleness_seconds(now_ts: str, latest_record_timestamp: str | None) -> float:
    if not latest_record_timestamp:
        return 0.0
    return max(0.0, (parse_utc_iso(now_ts) - parse_utc_iso(latest_record_timestamp)).total_seconds())


def decision_count(decision_records: int) -> int:
    return max(0, int(decision_records))


def risk_rejection_rate(rejected_risk_checks: int, total_risk_checks: int) -> float:
    if total_risk_checks <= 0:
        return 0.0
    return _clip(rejected_risk_checks / total_risk_checks)


def execution_error_rate(failed_execution_results: int, total_execution_attempts: int) -> float:
    if total_execution_attempts <= 0:
        return 0.0
    return _clip(failed_execution_results / total_execution_attempts)


def llm_cost_units(cost_units: list[float] | tuple[float, ...]) -> float:
    return sum(max(0.0, float(value)) for value in cost_units)


def gateway_error_rate(failed_external_requests: int, total_external_requests: int) -> float:
    if total_external_requests <= 0:
        return 0.0
    return _clip(failed_external_requests / total_external_requests)


def alert_count(alerts_created: int) -> int:
    return max(0, int(alerts_created))


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))
