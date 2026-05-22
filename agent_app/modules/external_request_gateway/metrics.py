from __future__ import annotations


def provider_latency_ms(request_sent_at_ms: int, response_received_at_ms: int) -> int:
    return max(0, response_received_at_ms - request_sent_at_ms)


def provider_error_rate(total_external_requests: int, failed_external_requests: int) -> float:
    if total_external_requests <= 0:
        return 0.0
    return failed_external_requests / total_external_requests


def cache_hit_rate(cache_eligible_requests: int, cache_hit_requests: int) -> float:
    if cache_eligible_requests <= 0:
        return 0.0
    return cache_hit_requests / cache_eligible_requests


def rate_limit_events(statuses: list[str]) -> int:
    return statuses.count("rate_limited")


def request_cost_units(provider_reported_cost: float | None, internal_estimate: float = 0.0) -> float:
    if provider_reported_cost is not None:
        return max(0.0, provider_reported_cost)
    return max(0.0, internal_estimate)


def timeout_count(elapsed_times_ms: list[int], timeout_ms: int) -> int:
    return sum(1 for elapsed_ms in elapsed_times_ms if elapsed_ms > timeout_ms)


def retry_count(request_id: str, attempted_request_ids: list[str]) -> int:
    return sum(1 for attempted_request_id in attempted_request_ids if attempted_request_id == request_id)
