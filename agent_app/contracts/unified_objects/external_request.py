from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .module_job import ContractValidationError, parse_utc_iso


VALID_EXTERNAL_PROVIDERS = {
    "moex_iss",
    "moex_fast",
    "broker_api",
    "news_api",
    "issuer_disclosure",
    "macro_api",
    "internal_cache",
    "arena_go",
    "polza_ai",
}

VALID_REQUEST_TYPES = {
    "market_data",
    "orderbook",
    "trades",
    "instruments",
    "orders",
    "portfolio",
    "text_search",
    "text_fetch",
    "llm_completion",
    "models",
    "macro_series",
    "submit_order",
    "get_trades",
    "get_positions",
    "get_bots",
}

VALID_EXTERNAL_RESPONSE_STATUSES = {
    "success",
    "partial_success",
    "failed",
    "timeout",
    "rate_limited",
}


@dataclass(frozen=True)
class CachePolicy:
    use_cache: bool = True
    max_age_seconds: int = 0
    write_cache: bool = True

    def __post_init__(self) -> None:
        if self.max_age_seconds < 0:
            raise ContractValidationError("cache_policy.max_age_seconds must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_cache": self.use_cache,
            "max_age_seconds": self.max_age_seconds,
            "write_cache": self.write_cache,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "CachePolicy":
        payload = payload or {}
        return cls(
            use_cache=bool(payload.get("use_cache", True)),
            max_age_seconds=int(payload.get("max_age_seconds") or 0),
            write_cache=bool(payload.get("write_cache", True)),
        )


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    backoff_ms: int = 0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ContractValidationError("retry_policy.max_retries must be non-negative")
        if self.backoff_ms < 0:
            raise ContractValidationError("retry_policy.backoff_ms must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "backoff_ms": self.backoff_ms,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "RetryPolicy":
        payload = payload or {}
        return cls(
            max_retries=int(payload.get("max_retries") or 0),
            backoff_ms=int(payload.get("backoff_ms") or 0),
        )


@dataclass(frozen=True)
class ExternalRequest:
    request_id: str
    caller_module: str
    provider: str
    request_type: str
    universe_id: str | None = None
    instrument_ids: tuple[str, ...] = field(default_factory=tuple)
    payload: Mapping[str, Any] = field(default_factory=dict)
    cache_policy: CachePolicy = field(default_factory=CachePolicy)
    timeout_ms: int = 0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        required = {
            "request_id": self.request_id,
            "caller_module": self.caller_module,
            "provider": self.provider,
            "request_type": self.request_type,
            "idempotency_key": self.idempotency_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ContractValidationError(f"external_request missing required fields: {missing}")
        if self.provider not in VALID_EXTERNAL_PROVIDERS:
            raise ContractValidationError(f"invalid external_request.provider: {self.provider}")
        if self.request_type not in VALID_REQUEST_TYPES:
            raise ContractValidationError(f"invalid external_request.request_type: {self.request_type}")
        if self.timeout_ms < 0:
            raise ContractValidationError("external_request.timeout_ms must be non-negative")
        if not isinstance(self.cache_policy, CachePolicy):
            raise ContractValidationError("cache_policy must be a CachePolicy")
        if not isinstance(self.retry_policy, RetryPolicy):
            raise ContractValidationError("retry_policy must be a RetryPolicy")

    @property
    def cache_key(self) -> str:
        if self.provider == "polza_ai" and self.request_type == "llm_completion":
            payload_map = dict(self.payload)
            key_payload = {
                "provider": self.provider,
                "request_type": self.request_type,
                "universe_id": self.universe_id,
                "instrument_ids": self.instrument_ids,
                "content_hash": payload_map.get("content_hash"),
                "task_type": payload_map.get("task_type"),
                "prompt_version": payload_map.get("prompt_version") or payload_map.get("llm_prompt_version"),
                "model_id": payload_map.get("model") or payload_map.get("model_id"),
                "event_ontology_version": payload_map.get("event_ontology_version"),
            }
            encoded = json.dumps(key_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            return f"external_request:{hashlib.sha256(encoded).hexdigest()}"
        payload = {
            "provider": self.provider,
            "request_type": self.request_type,
            "universe_id": self.universe_id,
            "instrument_ids": self.instrument_ids,
            "payload": self.payload,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return f"external_request:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "caller_module": self.caller_module,
            "provider": self.provider,
            "request_type": self.request_type,
            "universe_id": self.universe_id,
            "instrument_ids": list(self.instrument_ids),
            "payload": dict(self.payload),
            "cache_policy": self.cache_policy.to_dict(),
            "timeout_ms": self.timeout_ms,
            "retry_policy": self.retry_policy.to_dict(),
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExternalRequest":
        if "external_request" in payload and isinstance(payload["external_request"], Mapping):
            payload = payload["external_request"]  # type: ignore[assignment]
        return cls(
            request_id=str(payload.get("request_id", "")),
            caller_module=str(payload.get("caller_module", "")),
            provider=str(payload.get("provider", "")),
            request_type=str(payload.get("request_type", "")),
            universe_id=payload.get("universe_id"),
            instrument_ids=tuple(payload.get("instrument_ids") or ()),
            payload=payload.get("payload") or {},
            cache_policy=CachePolicy.from_dict(payload.get("cache_policy")),
            timeout_ms=int(payload.get("timeout_ms") or 0),
            retry_policy=RetryPolicy.from_dict(payload.get("retry_policy")),
            idempotency_key=str(payload.get("idempotency_key", "")),
        )


@dataclass(frozen=True)
class ExternalResponse:
    request_id: str
    provider: str
    status: str
    data_ref: str
    received_at: str
    latency_ms: int
    cost_units: float = 0.0
    cache_hit: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    provider_tracking_id: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = {
            "request_id": self.request_id,
            "provider": self.provider,
            "status": self.status,
            "received_at": self.received_at,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ContractValidationError(f"external_response missing required fields: {missing}")
        if self.status not in VALID_EXTERNAL_RESPONSE_STATUSES:
            raise ContractValidationError(f"invalid external_response.status: {self.status}")
        if self.latency_ms < 0:
            raise ContractValidationError("external_response.latency_ms must be non-negative")
        if self.cost_units < 0:
            raise ContractValidationError("external_response.cost_units must be non-negative")
        parse_utc_iso(self.received_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider": self.provider,
            "status": self.status,
            "data_ref": self.data_ref,
            "received_at": self.received_at,
            "latency_ms": self.latency_ms,
            "cost_units": self.cost_units,
            "cache_hit": self.cache_hit,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "provider_tracking_id": self.provider_tracking_id,
        }

    def to_payload_dict(self) -> dict[str, Any]:
        return {**self.to_dict(), "data": dict(self.data)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExternalResponse":
        if "external_response" in payload and isinstance(payload["external_response"], Mapping):
            payload = payload["external_response"]  # type: ignore[assignment]
        return cls(
            request_id=str(payload.get("request_id", "")),
            provider=str(payload.get("provider", "")),
            status=str(payload.get("status", "")),
            data_ref=str(payload.get("data_ref", "")),
            received_at=str(payload.get("received_at", "")),
            latency_ms=int(payload.get("latency_ms") or 0),
            cost_units=float(payload.get("cost_units") or 0.0),
            cache_hit=bool(payload.get("cache_hit", False)),
            warnings=tuple(payload.get("warnings") or ()),
            errors=tuple(payload.get("errors") or ()),
            provider_tracking_id=str(payload.get("provider_tracking_id", "")),
            data=payload.get("data") or {},
        )
