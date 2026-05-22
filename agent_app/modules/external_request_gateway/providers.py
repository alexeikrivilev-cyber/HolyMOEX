from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode, urljoin

from agent_app.contracts.unified_objects import ExternalRequest

from .repository import ProviderConfig


ARENA_GO_ENDPOINTS = {
    "submit_order": ("POST", "/submit_order"),
    "get_trades": ("GET", "/trades/{portfolio}"),
    "get_positions": ("GET", "/positions/{portfolio}"),
    "get_bots": ("GET", "/bots"),
}

MOEX_REQUEST_TYPES = {"market_data", "orderbook", "trades", "instruments"}
MOEX_DEFAULT_PATHS = {
    "instruments": "/engines/stock/markets/shares/boards/{board}/securities.json",
    "market_data": "/engines/stock/markets/shares/boards/{board}/securities/{secid}/candles.json",
    "trades": "/engines/stock/markets/shares/boards/{board}/securities/{secid}/trades.json",
    "orderbook": "/engines/stock/markets/shares/boards/{board}/securities/{secid}/orderbook.json",
}
TEXT_PROVIDER_DEFAULT_PATHS = {
    "text_search": "/search",
    "text_fetch": "/fetch",
}
MACRO_PROVIDER_DEFAULT_PATHS = {
    "macro_series": "/series",
    "text_search": "/search",
    "text_fetch": "/fetch",
}
GENERIC_HTTP_PROVIDERS = {
    "moex_iss",
    "moex_fast",
    "news_api",
    "issuer_disclosure",
    "macro_api",
    "broker_api",
}

ARENA_GO_ERROR_CODES = {
    "ERROR: MARKET CLOSED": "market_closed",
    "ERROR: NOT VALID SECID": "invalid_instrument",
    "ERROR: INSUFFICIENT CASH": "insufficient_cash",
}


@dataclass(frozen=True)
class ProviderHttpRequest:
    provider: str
    request_type: str
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    json_payload: Mapping[str, Any] | None = None
    timeout_ms: int = 0


@dataclass(frozen=True)
class ProviderHttpResponse:
    status_code: int
    body: Mapping[str, Any] | list[Any] | str | None
    headers: Mapping[str, str] = field(default_factory=dict)
    provider_tracking_id: str = ""

    def __post_init__(self) -> None:
        if not self.provider_tracking_id:
            object.__setattr__(self, "provider_tracking_id", _tracking_id(self.headers))


class HttpTransport(Protocol):
    def execute(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        ...


class UrllibHttpTransport:
    def execute(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        body_bytes: bytes | None = None
        headers = dict(request.headers)
        if request.json_payload is not None:
            body_bytes = json.dumps(request.json_payload, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        http_request = urllib.request.Request(
            request.url,
            data=body_bytes,
            headers=headers,
            method=request.method,
        )
        timeout = request.timeout_ms / 1000 if request.timeout_ms else None
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw_body = response.read()
                response_headers = dict(response.headers.items())
                parsed_body = _parse_json_body(raw_body)
                return ProviderHttpResponse(
                    status_code=response.status,
                    body=parsed_body,
                    headers=response_headers,
                    provider_tracking_id=_tracking_id(response_headers),
                )
        except urllib.error.HTTPError as error:
            raw_body = error.read()
            response_headers = dict(error.headers.items()) if error.headers else {}
            return ProviderHttpResponse(
                status_code=error.code,
                body=_parse_json_body(raw_body),
                headers=response_headers,
                provider_tracking_id=_tracking_id(response_headers),
            )
        except TimeoutError:
            return ProviderHttpResponse(status_code=408, body={"error": "timeout"})
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            status_code = 408 if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower() else 599
            return ProviderHttpResponse(status_code=status_code, body={"error": str(reason)})


class ProviderRequestNormalizer:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = env if env is not None else os.environ

    def normalize(self, request: ExternalRequest, config: ProviderConfig) -> ProviderHttpRequest:
        if request.provider == "arena_go":
            return self._arena_go_request(request, config)
        if request.provider == "polza_ai":
            return self._polza_ai_request(request, config)
        if request.provider in {"moex_iss", "moex_fast"}:
            return self._moex_request(request, config)
        if request.provider in {"news_api", "issuer_disclosure", "macro_api", "broker_api"}:
            return self._generic_rest_request(request, config)
        if request.provider == "internal_cache":
            raise ProviderNormalizationError("internal_cache is served from Request Cache Store, not HTTP transport")
        raise ProviderNormalizationError(f"unsupported provider for HTTP normalization: {request.provider}")

    def _arena_go_request(self, request: ExternalRequest, config: ProviderConfig) -> ProviderHttpRequest:
        endpoint = ARENA_GO_ENDPOINTS.get(request.request_type)
        if endpoint is None:
            raise ProviderNormalizationError(f"unsupported arena_go request_type: {request.request_type}")
        method, path_template = endpoint
        payload = dict(request.payload)
        if request.request_type == "submit_order":
            payload = self._arena_go_submit_order_payload(payload, config)
        path = path_template.format(
            portfolio=payload.get("portfolio")
            or self._env_config_value(config, "portfolio_env")
            or "arena_go_default"
        )
        return ProviderHttpRequest(
            provider=request.provider,
            request_type=request.request_type,
            method=method,
            url=self._url(config, path),
            headers=self._auth_headers(config),
            json_payload=payload if method == "POST" else None,
            timeout_ms=request.timeout_ms,
        )

    def _polza_ai_request(self, request: ExternalRequest, config: ProviderConfig) -> ProviderHttpRequest:
        if request.request_type != "llm_completion":
            raise ProviderNormalizationError(f"unsupported polza_ai request_type: {request.request_type}")
        payload = dict(request.payload)
        default_model = self._env_config_value(config, "default_model_env") or "deepseek/deepseek-v4-pro"
        payload.setdefault("model", default_model)
        payload.setdefault("temperature", 0)
        payload.setdefault("response_format", {"type": "json_object"})
        payload.setdefault("messages", [])
        if payload.get("response_format") != {"type": "json_object"}:
            raise ProviderNormalizationError("polza_ai llm_completion requires response_format json_object")
        return ProviderHttpRequest(
            provider=request.provider,
            request_type=request.request_type,
            method="POST",
            url=self._url(config, "/chat/completions"),
            headers=self._auth_headers(config),
            json_payload=payload,
            timeout_ms=request.timeout_ms,
        )

    def _moex_request(self, request: ExternalRequest, config: ProviderConfig) -> ProviderHttpRequest:
        if request.request_type not in MOEX_REQUEST_TYPES:
            raise ProviderNormalizationError(f"unsupported {request.provider} request_type: {request.request_type}")
        path = self._configured_path(request, config, MOEX_DEFAULT_PATHS)
        query = self._query_params(request, include_payload=True)
        query.setdefault("iss.meta", "off")
        if request.request_type == "market_data":
            time_range = request.payload.get("time_range")
            if isinstance(time_range, Mapping):
                if time_range.get("from_ts"):
                    query.setdefault("from", str(time_range["from_ts"])[:10])
                if time_range.get("to_ts"):
                    query.setdefault("till", str(time_range["to_ts"])[:10])
            timeframe = _first(request.payload.get("timeframes"))
            if timeframe:
                query.setdefault("interval", timeframe)
        return ProviderHttpRequest(
            provider=request.provider,
            request_type=request.request_type,
            method="GET",
            url=self._url(config, self._format_path(path, request)) + self._encoded_query(query),
            headers=self._auth_headers(config),
            json_payload=None,
            timeout_ms=request.timeout_ms,
        )

    def _generic_rest_request(self, request: ExternalRequest, config: ProviderConfig) -> ProviderHttpRequest:
        payload = dict(request.payload)
        method = str(payload.get("method") or self._method_for_request(config, request.request_type)).upper()
        path = self._configured_path(request, config, self._default_paths_for_provider(request.provider))
        formatted_path = self._format_path(path, request)
        headers = self._auth_headers(config)
        json_payload: Mapping[str, Any] | None = None
        url = self._url(config, formatted_path)
        if method == "GET":
            url += self._encoded_query(self._query_params(request, include_payload=True))
        else:
            body = payload.get("body") or payload.get("json") or payload
            if not isinstance(body, Mapping):
                raise ProviderNormalizationError(f"{request.provider} request body must be an object")
            json_payload = body
        return ProviderHttpRequest(
            provider=request.provider,
            request_type=request.request_type,
            method=method,
            url=url,
            headers=headers,
            json_payload=json_payload,
            timeout_ms=request.timeout_ms,
        )

    def _arena_go_submit_order_payload(
        self,
        payload: dict[str, Any],
        config: ProviderConfig,
    ) -> dict[str, Any]:
        bot = payload.get("bot") or self._env_config_value(config, "bot_name_env")
        if bot:
            payload["bot"] = bot
        required = ("direction", "secid", "quantity", "bot")
        missing = [field_name for field_name in required if payload.get(field_name) in (None, "")]
        if missing:
            raise ProviderNormalizationError(f"arena_go submit_order missing fields: {missing}")
        if payload["direction"] not in {"B", "S"}:
            raise ProviderNormalizationError("arena_go submit_order.direction must be B or S")
        if float(payload["quantity"]) <= 0:
            raise ProviderNormalizationError("arena_go submit_order.quantity must be positive")
        return payload

    def _url(self, config: ProviderConfig, path: str) -> str:
        base_url = ""
        if config.base_url_env:
            base_url = self.env.get(config.base_url_env, "")
        base_url = base_url or config.default_base_url or ""
        if not base_url:
            raise ProviderNormalizationError(f"provider base URL is not configured: {config.provider}")
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))

    def _auth_headers(self, config: ProviderConfig) -> dict[str, str]:
        if not config.auth_header or not config.auth_value_source:
            return {}
        auth_value = self.env.get(config.auth_value_source, "")
        if not auth_value:
            return {}
        auth_scheme = str(config.config_payload.get("auth_scheme", "")).strip()
        if auth_scheme and not auth_value.startswith(f"{auth_scheme} "):
            auth_value = f"{auth_scheme} {auth_value}"
        return {config.auth_header: auth_value}

    def _env_config_value(self, config: ProviderConfig, payload_key: str) -> str:
        env_name = config.config_payload.get(payload_key)
        if not env_name:
            return ""
        return self.env.get(str(env_name), "")

    def _configured_path(
        self,
        request: ExternalRequest,
        config: ProviderConfig,
        defaults: Mapping[str, str],
    ) -> str:
        payload = request.payload
        direct_path = payload.get("path") or payload.get("endpoint")
        if direct_path:
            return str(direct_path)
        configured_paths = config.config_payload.get("request_paths")
        if isinstance(configured_paths, Mapping) and configured_paths.get(request.request_type):
            return str(configured_paths[request.request_type])
        default_path = defaults.get(request.request_type)
        if default_path:
            return default_path
        raise ProviderNormalizationError(f"provider path is not configured: {request.provider}/{request.request_type}")

    def _format_path(self, path: str, request: ExternalRequest) -> str:
        payload = request.payload
        secid = str(
            payload.get("secid")
            or payload.get("security")
            or payload.get("ticker")
            or _first(request.instrument_ids)
            or ""
        )
        path_values = {
            "board": str(payload.get("board") or payload.get("board_id") or "TQBR"),
            "secid": _strip_moex_prefix(secid),
            "portfolio": str(payload.get("portfolio") or ""),
            "series_id": str(payload.get("series_id") or _first(payload.get("series_ids")) or ""),
            "document_id": str(payload.get("document_id") or payload.get("id") or ""),
        }
        try:
            return path.format(**path_values)
        except KeyError as error:
            raise ProviderNormalizationError(f"missing path placeholder value: {error}") from error

    def _method_for_request(self, config: ProviderConfig, request_type: str) -> str:
        methods = config.config_payload.get("request_methods")
        if isinstance(methods, Mapping) and methods.get(request_type):
            return str(methods[request_type])
        return "GET"

    def _default_paths_for_provider(self, provider: str) -> Mapping[str, str]:
        if provider in {"news_api", "issuer_disclosure"}:
            return TEXT_PROVIDER_DEFAULT_PATHS
        if provider == "macro_api":
            return MACRO_PROVIDER_DEFAULT_PATHS
        return {}

    def _query_params(self, request: ExternalRequest, *, include_payload: bool) -> dict[str, str]:
        query: dict[str, str] = {}
        payload = request.payload
        query_payload = payload.get("query_params")
        if isinstance(query_payload, Mapping):
            query.update(_flatten_query(query_payload))
        if request.instrument_ids:
            query.setdefault("instrument_ids", ",".join(request.instrument_ids))
            query.setdefault("securities", ",".join(_strip_moex_prefix(item) for item in request.instrument_ids))
        if request.universe_id:
            query.setdefault("universe_id", request.universe_id)
        if include_payload:
            for key, value in payload.items():
                if key in {"path", "endpoint", "method", "body", "json", "headers", "query_params"}:
                    continue
                if key == "query" and isinstance(value, Mapping):
                    query.update(_flatten_query(value))
                    continue
                if key == "time_range" and isinstance(value, Mapping):
                    if value.get("from_ts"):
                        query.setdefault("from_ts", str(value["from_ts"]))
                    if value.get("to_ts"):
                        query.setdefault("to_ts", str(value["to_ts"]))
                    continue
                if _is_query_scalar(value):
                    query.setdefault(key, _query_value(value))
        return query

    def _encoded_query(self, query: Mapping[str, str]) -> str:
        clean_query = {key: value for key, value in query.items() if value not in {"", "None"}}
        if not clean_query:
            return ""
        return "?" + urlencode(clean_query)


def normalize_provider_response(
    request: ExternalRequest,
    provider_response: ProviderHttpResponse,
) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
    if request.provider == "arena_go":
        return _normalize_arena_go_response(request, provider_response)
    if request.provider == "polza_ai":
        return _normalize_polza_ai_response(provider_response)
    if request.provider in GENERIC_HTTP_PROVIDERS:
        return _normalize_generic_http_response(request, provider_response)
    return _generic_status(provider_response.status_code), _mapping_body(provider_response.body), (), ()


class ProviderNormalizationError(ValueError):
    """Raised when provider-specific transport mapping violates the contract."""


def _normalize_arena_go_response(
    request: ExternalRequest,
    provider_response: ProviderHttpResponse,
) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
    body = _mapping_body(provider_response.body)
    status = _generic_status(provider_response.status_code)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    message = str(body.get("message") or body.get("error") or "")
    if body.get("success") is False or message.startswith("ERROR:"):
        status = "failed"
        error_code = arena_go_error_code(message)
        errors = (error_code,) if error_code else (message or "arena_go_error",)
    if request.request_type == "submit_order":
        data = {
            "provider": "arena_go",
            "request_type": "submit_order",
            "status": status,
            "data": {
                "success": bool(body.get("success", status == "success")),
                "message": str(body.get("message", "")),
                "order_value": float(body.get("order_value") or 0.0),
                "price": float(body.get("price") or 0.0),
                "quantity": int(body.get("quantity") or 0),
                "remaining_cash": float(body.get("remaining_cash") or 0.0),
            },
            "errors": list(errors),
        }
        return status, data, warnings, errors
    return status, body, warnings, errors


def _normalize_polza_ai_response(
    provider_response: ProviderHttpResponse,
) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
    status = _generic_status(provider_response.status_code)
    body = _mapping_body(provider_response.body)
    if status != "success":
        return status, body, (), tuple(_response_errors(body))
    content = _extract_polza_content(body)
    if content is None:
        return "failed", body, (), ("llm_json_output_missing",)
    if isinstance(content, str):
        try:
            content_payload = json.loads(content)
        except json.JSONDecodeError:
            return "failed", body, (), ("llm_free_form_output",)
    elif isinstance(content, Mapping):
        content_payload = dict(content)
    else:
        return "failed", body, (), ("llm_json_output_missing",)
    required = {"schema_version", "model_id", "model_version", "task_type", "items"}
    missing = sorted(required - set(content_payload))
    if missing:
        return "failed", content_payload, (), (f"llm_output_missing_fields:{','.join(missing)}",)
    return "success", content_payload, tuple(content_payload.get("warnings") or ()), ()


def _normalize_generic_http_response(
    request: ExternalRequest,
    provider_response: ProviderHttpResponse,
) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
    status = _generic_status(provider_response.status_code)
    body = _mapping_body(provider_response.body)
    if status != "success":
        return status, _provider_envelope(request, body), (), tuple(_response_errors(body))
    return status, _provider_envelope(request, body), (), ()


def arena_go_error_code(message: str) -> str:
    for raw_error, error_code in ARENA_GO_ERROR_CODES.items():
        if raw_error in message:
            return error_code
    if "HAS REACHED DAILY TRADE LIMIT" in message:
        return "daily_trade_limit_reached"
    return ""


def _generic_status(status_code: int) -> str:
    if status_code == 408:
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if status_code == 206:
        return "partial_success"
    if 200 <= status_code < 300:
        return "success"
    return "failed"


def _mapping_body(body: Mapping[str, Any] | list[Any] | str | None) -> Mapping[str, Any]:
    if isinstance(body, Mapping):
        return body
    if isinstance(body, list):
        return {"items": body}
    if isinstance(body, str):
        return {"message": body}
    return {}


def _provider_envelope(request: ExternalRequest, body: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "provider": request.provider,
        "request_type": request.request_type,
        "items": _extract_items(body, request.request_type),
        "raw": body,
    }


def _extract_items(body: Mapping[str, Any], request_type: str) -> list[Mapping[str, Any]]:
    for key in ("items", "data", "results", "documents", "points"):
        value = body.get(key)
        if isinstance(value, list):
            return [_coerce_item(item) for item in value]
    table_keys_by_type = {
        "market_data": ("candles", "marketdata", "securities"),
        "trades": ("trades",),
        "orderbook": ("orderbook", "orderbooks", "marketdepth"),
        "instruments": ("securities", "boards", "marketdata"),
        "macro_series": ("series", "points", "data"),
        "text_search": ("items", "documents", "results", "news"),
        "text_fetch": ("document", "item"),
    }
    for key in table_keys_by_type.get(request_type, ()):
        value = body.get(key)
        records = _records_from_provider_value(value)
        if records:
            return records
    records = _records_from_provider_value(body)
    return records if records else [dict(body)] if body else []


def _records_from_provider_value(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [_coerce_item(item) for item in value]
    if isinstance(value, Mapping):
        columns = value.get("columns")
        rows = value.get("data")
        if isinstance(columns, list) and isinstance(rows, list):
            records: list[Mapping[str, Any]] = []
            for row in rows:
                if isinstance(row, list):
                    records.append({str(column).lower(): item for column, item in zip(columns, row)})
            return records
        if any(key in value for key in ("items", "data", "results")):
            for key in ("items", "data", "results"):
                records = _records_from_provider_value(value.get(key))
                if records:
                    return records
        if value:
            return [dict(value)]
    return []


def _coerce_item(item: Any) -> Mapping[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    return {"value": item}


def _response_errors(body: Mapping[str, Any]) -> list[str]:
    errors = body.get("errors")
    if isinstance(errors, list):
        return [str(error) for error in errors]
    error = body.get("error") or body.get("message")
    return [str(error)] if error else []


def _extract_polza_content(body: Mapping[str, Any]) -> Any:
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping) and "content" in message:
                return message["content"]
            if "content" in first:
                return first["content"]
    if "content" in body:
        return body["content"]
    return body if "schema_version" in body else None


def _parse_json_body(raw_body: bytes) -> Mapping[str, Any] | list[Any] | str | None:
    if not raw_body:
        return None
    text = raw_body.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _flatten_query(payload: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): _query_value(value) for key, value in payload.items() if _is_query_scalar(value)}


def _is_query_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool)) or (
        isinstance(value, (list, tuple)) and all(isinstance(item, (str, int, float, bool)) for item in value)
    )


def _query_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text


def _tracking_id(headers: Mapping[str, str]) -> str:
    for key in ("x-request-id", "x-correlation-id", "x-trace-id", "request-id"):
        for header_name, value in headers.items():
            if header_name.lower() == key:
                return value
    return ""
