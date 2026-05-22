from __future__ import annotations

from datetime import datetime

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


def pipeline_latency_ms(started_at: str, finished_at: str) -> int:
    started = parse_utc_iso(started_at)
    finished = parse_utc_iso(finished_at)
    return int((finished - started).total_seconds() * 1000)


def module_success_rate(statuses: list[str]) -> float:
    if not statuses:
        return 0.0
    return statuses.count("success") / len(statuses)


def module_failure_rate(statuses: list[str]) -> float:
    if not statuses:
        return 0.0
    return statuses.count("failed") / len(statuses)


def retry_count(job_id: str, attempted_job_ids: list[str]) -> int:
    return sum(1 for attempted_job_id in attempted_job_ids if attempted_job_id == job_id)


def dependency_wait_time_ms(module_start_allowed_at: str, dependency_ready_at: str) -> int:
    start_allowed = parse_utc_iso(module_start_allowed_at)
    dependency_ready = parse_utc_iso(dependency_ready_at)
    return int((start_allowed - dependency_ready).total_seconds() * 1000)


def stale_dependency_count(ttl_statuses: list[str]) -> int:
    return sum(1 for ttl_status in ttl_statuses if ttl_status in {"stale", "expired"})


def utc_timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)

