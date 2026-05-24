from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ExternalRequest, ExternalResponse, ModuleJob
from agent_app.contracts.unified_objects.module_job import ContractValidationError, to_utc_iso, utc_now

from .providers import (
    HttpTransport,
    ProviderHttpResponse,
    ProviderNormalizationError,
    ProviderRequestNormalizer,
    UrllibHttpTransport,
    normalize_provider_response,
)
from .repository import (
    ExternalRequestGatewayRepository,
    InMemoryExternalRequestGatewayRepository,
    ProviderConfig,
    sanitize_payload,
)


class ExternalRequestGatewayError(ValueError):
    """Raised internally when a gateway action would violate module docs."""


@dataclass(frozen=True)
class GatewayExecutionResult:
    response: ExternalResponse
    request_log_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_response": self.response.to_dict(),
            "request_log_ref": self.request_log_ref,
        }


class ExternalRequestGatewayService:
    module_name = "External Request Gateway Module"
    _llm_call_timestamps: dict[str, deque[float]] = defaultdict(deque)

    def __init__(
        self,
        repository: ExternalRequestGatewayRepository | None = None,
        transport: HttpTransport | None = None,
        normalizer: ProviderRequestNormalizer | None = None,
    ) -> None:
        self.repository = repository or InMemoryExternalRequestGatewayRepository()
        self.transport = transport or UrllibHttpTransport()
        self.normalizer = normalizer or ProviderRequestNormalizer()

    def process(
        self,
        external_request: ExternalRequest | Mapping[str, Any],
        job: ModuleJob | None = None,
    ) -> ExternalResponse:
        return self.execute(external_request, job=job).response

    def run(
        self,
        external_request: ExternalRequest | Mapping[str, Any],
        job: ModuleJob | None = None,
    ) -> ExternalResponse:
        return self.process(external_request, job=job)

    def execute(
        self,
        external_request: ExternalRequest | Mapping[str, Any],
        job: ModuleJob | None = None,
    ) -> GatewayExecutionResult:
        started_at = time.perf_counter()
        try:
            request = self._coerce_request(external_request)
        except Exception as error:
            return GatewayExecutionResult(
                response=self._invalid_request_response(external_request, started_at, error),
                request_log_ref="",
            )
        self.repository.save_external_request(request)

        try:
            self._validate_optional_module_job(job)
            config = self.validate_provider_access(request)
            if not self.apply_llm_throttle(request):
                response = self._failure_response(
                    request=request,
                    started_at=started_at,
                    status="rate_limited",
                    errors=("llm_throttled",),
                    data={
                        "error_code": "llm_throttled",
                        "task_type": request.payload.get("task_type"),
                        "model_id": request.payload.get("model") or request.payload.get("model_id"),
                    },
                )
                return self._persist_and_return(request, response)
            if not self.apply_rate_limit(request):
                response = self._failure_response(
                    request=request,
                    started_at=started_at,
                    status="rate_limited",
                    errors=("rate_limited",),
                    data={"error_code": "rate_limited"},
                )
                return self._persist_and_return(request, response)

            idempotent_response = self._idempotent_submit_order_response(request, started_at)
            if idempotent_response is not None:
                return self._persist_and_return(request, idempotent_response)

            cached_response = self.check_cache(request, started_at)
            if cached_response is not None:
                return self._persist_and_return(request, cached_response)
            if request.provider == "internal_cache":
                response = self._failure_response(
                    request=request,
                    started_at=started_at,
                    status="failed",
                    errors=("internal_cache_miss",),
                    data={"error_code": "internal_cache_miss"},
                )
                return self._persist_and_return(request, response)

            provider_response, retry_count = self._execute_with_retry(request, config)
            return self.return_external_response(
                request=request,
                provider_response=provider_response,
                started_at=started_at,
                retry_count=retry_count,
            )
        except (ExternalRequestGatewayError, ProviderNormalizationError, ContractValidationError) as error:
            response = self._failure_response(
                request=request,
                started_at=started_at,
                status="failed",
                errors=(str(error),),
                data={"error_code": "gateway_validation_failed"},
            )
            return self._persist_and_return(request, response)
        except Exception as error:
            response = self._failure_response(
                request=request,
                started_at=started_at,
                status="failed",
                errors=(str(error),),
                data={"error_code": "gateway_unhandled_provider_error"},
            )
            return self._persist_and_return(request, response)

    def validate_provider_access(self, request: ExternalRequest) -> ProviderConfig:
        config = self.repository.load_provider_config(request.provider)
        if config is None:
            raise ExternalRequestGatewayError(f"provider is not configured: {request.provider}")
        if not config.enabled:
            raise ExternalRequestGatewayError(f"provider is disabled: {request.provider}")
        allowed_request_types = config.config_payload.get("allowed_request_types")
        if isinstance(allowed_request_types, (list, tuple, set)):
            allowed = {str(request_type) for request_type in allowed_request_types}
            if request.request_type not in allowed:
                raise ExternalRequestGatewayError(
                    f"request_type is not allowed for provider: {request.provider}/{request.request_type}"
                )
        return config

    def apply_rate_limit(self, request: ExternalRequest) -> bool:
        return self.repository.record_rate_limit_event(request.provider, request.request_type, utc_now())

    def apply_llm_throttle(self, request: ExternalRequest) -> bool:
        if request.provider != "polza_ai" or request.request_type != "llm_completion":
            return True
        if not _env_bool("LLM_ENABLED", True):
            return False
        now = time.time()
        key = str(request.payload.get("model") or request.payload.get("model_id") or "default")
        timestamps = self._llm_call_timestamps[key]
        while timestamps and now - timestamps[0] > 86_400:
            timestamps.popleft()
        min_gap = _env_float("LLM_MIN_SECONDS_BETWEEN_CALLS", 0.0)
        if min_gap > 0 and timestamps and now - timestamps[-1] < min_gap:
            return False
        limits = (
            (60.0, _env_int("LLM_MAX_CALLS_PER_MINUTE", 0)),
            (3600.0, _env_int("LLM_MAX_CALLS_PER_HOUR", 60)),
            (86_400.0, _env_int("LLM_MAX_CALLS_PER_DAY", 500)),
        )
        for window_seconds, limit in limits:
            if limit > 0 and sum(1 for ts in timestamps if now - ts <= window_seconds) >= limit:
                return False
        timestamps.append(now)
        return True

    def check_cache(self, request: ExternalRequest, started_at: float) -> ExternalResponse | None:
        if not request.cache_policy.use_cache:
            return None
        cached = self.repository.get_cached_response(request.cache_key, utc_now())
        if cached is None:
            return None
        return ExternalResponse(
            request_id=request.request_id,
            provider=request.provider,
            status="success",
            data_ref=cached.data_ref,
            received_at=to_utc_iso(utc_now()),
            latency_ms=self._elapsed_ms(started_at),
            cost_units=0.0,
            cache_hit=True,
            warnings=("cache_hit",),
            errors=(),
            provider_tracking_id="",
            data=cached.payload,
        )

    def return_external_response(
        self,
        request: ExternalRequest,
        provider_response: ProviderHttpResponse,
        started_at: float,
        retry_count: int,
    ) -> GatewayExecutionResult:
        status, data, warnings, errors = normalize_provider_response(request, provider_response)
        warnings = tuple(warnings) + self._retry_warning(retry_count)
        received_at = to_utc_iso(utc_now())
        data_ref = (
            self.repository.write_raw_data(request, data, received_at)
            if status in {"success", "partial_success"}
            else f"request_logs.external_response:{request.request_id}"
        )
        response = ExternalResponse(
            request_id=request.request_id,
            provider=request.provider,
            status=status,
            data_ref=data_ref,
            received_at=received_at,
            latency_ms=self._elapsed_ms(started_at),
            cost_units=self._request_cost_units(request, provider_response, data),
            cache_hit=False,
            warnings=warnings,
            errors=errors,
            provider_tracking_id=provider_response.provider_tracking_id,
            data=data,
        )
        if status in {"success", "partial_success"} and request.cache_policy.write_cache:
            self.repository.save_cache_entry(request, response, data, utc_now())
        return self._persist_and_return(request, response)

    def normalize_provider_request(self, request: ExternalRequest, config: ProviderConfig):
        return self.normalizer.normalize(request, config)

    def normalize_provider_response(
        self,
        request: ExternalRequest,
        provider_response: ProviderHttpResponse,
    ) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
        return normalize_provider_response(request, provider_response)

    def write_request_log(self, request: ExternalRequest, response: ExternalResponse) -> str:
        return self.repository.write_request_log(request, response)

    def _execute_with_retry(
        self,
        request: ExternalRequest,
        config: ProviderConfig,
    ) -> tuple[ProviderHttpResponse, int]:
        attempts = request.retry_policy.max_retries + 1
        last_response: ProviderHttpResponse | None = None
        retry_count = 0
        for attempt in range(attempts):
            provider_request = self.normalize_provider_request(request, config)
            last_response = self.transport.execute(provider_request)
            if last_response.status_code not in {408, 429} and last_response.status_code < 500:
                return last_response, retry_count
            if attempt < attempts - 1:
                retry_count += 1
                if request.retry_policy.backoff_ms:
                    time.sleep(request.retry_policy.backoff_ms / 1000)
        if last_response is None:
            raise ExternalRequestGatewayError("provider request was not executed")
        return last_response, retry_count

    def _idempotent_submit_order_response(
        self,
        request: ExternalRequest,
        started_at: float,
    ) -> ExternalResponse | None:
        if request.request_type != "submit_order":
            return None
        existing = self.repository.get_response_by_idempotency_key(
            request.provider,
            request.request_type,
            request.idempotency_key,
        )
        if existing is None:
            return None
        return ExternalResponse(
            request_id=request.request_id,
            provider=request.provider,
            status=existing.status,
            data_ref=existing.data_ref,
            received_at=to_utc_iso(utc_now()),
            latency_ms=self._elapsed_ms(started_at),
            cost_units=0.0,
            cache_hit=True,
            warnings=("idempotency_key_reused",),
            errors=existing.errors,
            provider_tracking_id=existing.provider_tracking_id,
            data=existing.data,
        )

    def _persist_and_return(
        self,
        request: ExternalRequest,
        response: ExternalResponse,
    ) -> GatewayExecutionResult:
        safe_response = ExternalResponse(
            request_id=response.request_id,
            provider=response.provider,
            status=response.status,
            data_ref=response.data_ref,
            received_at=response.received_at,
            latency_ms=response.latency_ms,
            cost_units=response.cost_units,
            cache_hit=response.cache_hit,
            warnings=response.warnings,
            errors=response.errors,
            provider_tracking_id=response.provider_tracking_id,
            data=sanitize_payload(response.data),
        )
        self.repository.save_external_response(request, safe_response)
        request_log_ref = self.write_request_log(request, safe_response)
        return GatewayExecutionResult(response=safe_response, request_log_ref=request_log_ref)

    def _validate_optional_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            return
        if job.module_name != self.module_name:
            raise ExternalRequestGatewayError("gateway service module_job has invalid module_name")
        if job.contour != "service_contour":
            raise ExternalRequestGatewayError("gateway module_job.contour must be service_contour")
        if not job.idempotency_key:
            raise ExternalRequestGatewayError("gateway module_job.idempotency_key is required")

    def _coerce_request(self, payload: ExternalRequest | Mapping[str, Any]) -> ExternalRequest:
        if isinstance(payload, ExternalRequest):
            return payload
        return ExternalRequest.from_dict(payload)

    def _failure_response(
        self,
        request: ExternalRequest,
        started_at: float,
        status: str,
        errors: tuple[str, ...],
        data: Mapping[str, Any],
    ) -> ExternalResponse:
        return ExternalResponse(
            request_id=request.request_id,
            provider=request.provider,
            status=status,
            data_ref=f"request_logs.external_response:{request.request_id}",
            received_at=to_utc_iso(utc_now()),
            latency_ms=self._elapsed_ms(started_at),
            cost_units=0.0,
            cache_hit=False,
            warnings=(),
            errors=errors,
            provider_tracking_id="",
            data=data,
        )

    def _invalid_request_response(
        self,
        payload: ExternalRequest | Mapping[str, Any],
        started_at: float,
        error: Exception,
    ) -> ExternalResponse:
        if isinstance(payload, ExternalRequest):
            request_id = payload.request_id
            provider = payload.provider
        else:
            nested = payload.get("external_request") if isinstance(payload.get("external_request"), Mapping) else payload
            request_id = str(nested.get("request_id") or "invalid_external_request")  # type: ignore[union-attr]
            provider = str(nested.get("provider") or "unknown")  # type: ignore[union-attr]
        return ExternalResponse(
            request_id=request_id,
            provider=provider,
            status="failed",
            data_ref=f"request_logs.external_response:{request_id}",
            received_at=to_utc_iso(utc_now()),
            latency_ms=self._elapsed_ms(started_at),
            cost_units=0.0,
            cache_hit=False,
            warnings=(),
            errors=(str(error),),
            provider_tracking_id="",
            data={"error_code": "invalid_external_request"},
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    def _request_cost_units(
        self,
        request: ExternalRequest,
        provider_response: ProviderHttpResponse,
        data: Mapping[str, Any],
    ) -> float:
        if isinstance(data.get("cost_units"), (int, float)):
            return float(data["cost_units"])
        cost_header = next(
            (
                value
                for header, value in provider_response.headers.items()
                if header.lower() in {"x-cost-units", "x-request-cost"}
            ),
            None,
        )
        if cost_header is not None:
            try:
                return float(cost_header)
            except ValueError:
                return 0.0
        if request.request_type == "llm_completion":
            usage = data.get("usage")
            if isinstance(usage, Mapping):
                return float(usage.get("total_tokens") or 0.0)
        return 0.0

    def _retry_warning(self, retry_count: int) -> tuple[str, ...]:
        if retry_count <= 0:
            return ()
        return (f"retry_count:{retry_count}",)


def response_received_at_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
