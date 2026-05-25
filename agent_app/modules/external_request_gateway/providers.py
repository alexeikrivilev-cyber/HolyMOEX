from __future__ import annotations

import csv
import io
import json
import os
import re
import xml.etree.ElementTree as ET
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlencode, urljoin

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
PROHIBITED_POLZA_MODELS = {"deepseek/" + "deepseek-v4" + "-pro"}


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
        headers.setdefault("Accept", "application/json, application/rss+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5")
        headers.setdefault("User-Agent", "HolyMOEX/1.0 public-data-gateway")
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
                parsed_body = _parse_json_body(raw_body, response_headers)
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
                body=_parse_json_body(raw_body, response_headers),
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
        path = path_template.format(portfolio=quote(self._arena_go_portfolio_name(payload, config), safe=""))
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
        if request.request_type == "models":
            return ProviderHttpRequest(
                provider=request.provider,
                request_type=request.request_type,
                method="GET",
                url=self._url(config, "/models"),
                headers=self._auth_headers(config),
                json_payload=None,
                timeout_ms=request.timeout_ms,
            )
        if request.request_type != "llm_completion":
            raise ProviderNormalizationError(f"unsupported polza_ai request_type: {request.request_type}")
        payload = dict(request.payload)
        default_model = self._polza_model_for_task(payload, config)
        payload.setdefault("model", default_model)
        payload["model"] = _safe_polza_model(str(payload.get("model") or ""), default_model)
        payload["model_id"] = _safe_polza_model(str(payload.get("model_id") or payload.get("model") or ""), default_model)
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
        if request.request_type in {"market_data", "trades"}:
            time_range = request.payload.get("time_range")
            if isinstance(time_range, Mapping):
                if time_range.get("from_ts"):
                    query.setdefault("from", str(time_range["from_ts"])[:10])
                if time_range.get("to_ts"):
                    query.setdefault("till", str(time_range["to_ts"])[:10])
        if request.request_type == "market_data":
            timeframe = str(request.payload.get("timeframe") or _first(request.payload.get("timeframes")) or "")
            if timeframe:
                query.setdefault("interval", _moex_interval(timeframe))
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
            url += self._encoded_query(
                self._query_params(request, include_payload=True),
                separator="&" if "?" in url else "?",
            )
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

    def _arena_go_portfolio_name(self, payload: Mapping[str, Any], config: ProviderConfig) -> str:
        candidates = (
            payload.get("portfolio"),
            payload.get("bot"),
            self._env_config_value(config, "portfolio_env"),
            self._env_config_value(config, "bot_name_env"),
        )
        placeholders = {"", "arena_go_default", "mybot", "mytradingbot", "portfolio"}
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text and text.lower() not in placeholders:
                return text
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return "arena_go_default"

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
        auth_sources = [str(config.auth_value_source)]
        fallback_sources = config.config_payload.get("auth_fallback_value_sources")
        if isinstance(fallback_sources, (list, tuple)):
            auth_sources.extend(str(item) for item in fallback_sources if str(item or ""))
        auth_value = ""
        for source in auth_sources:
            auth_value = self.env.get(source, "")
            if auth_value:
                break
        if not auth_value:
            return {}
        auth_scheme = str(config.config_payload.get("auth_scheme", "")).strip()
        if auth_scheme and not auth_value.startswith(f"{auth_scheme} "):
            auth_value = f"{auth_scheme} {auth_value}"
        return {config.auth_header: auth_value}

    def _polza_model_for_task(self, payload: Mapping[str, Any], config: ProviderConfig) -> str:
        task_type = str(payload.get("task_type") or "").strip()
        fast_tasks = {
            "event_extraction",
            "sentiment_scoring",
            "news_classification",
            "entity_matching",
            "duplicate_preclassification",
            "disclosure_classification",
            "short_news_summarization",
            "raw_text_relevance_filtering",
        }
        reasoning_tasks = {
            "report_extraction",
            "earnings_analysis",
            "dividend_extraction",
            "macro_text_analysis",
            "complex_corporate_action_interpretation",
            "multi_source_event_synthesis",
            "validation_research_commentary",
            "strategy_risk_explanation",
        }
        if task_type in fast_tasks:
            return _safe_polza_model(
                self.env.get("POLZA_FAST_MODEL") or str(config.config_payload.get("fast_model") or ""),
                "deepseek/deepseek-v4-flash",
            )
        if task_type in reasoning_tasks:
            return _safe_polza_model(
                self.env.get("POLZA_REASONING_MODEL") or str(config.config_payload.get("reasoning_model") or ""),
                "qwen/qwen3.6-35b-a3b",
            )
        return _safe_polza_model(
            self.env.get("POLZA_DEFAULT_MODEL")
            or str(config.config_payload.get("default_model") or ""),
            "qwen/qwen3.6-35b-a3b",
        )

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
        time_range = payload.get("time_range")
        if isinstance(time_range, Mapping):
            from_ts = str(time_range.get("from_ts") or "")
            to_ts = str(time_range.get("to_ts") or "")
            path_values.update(
                {
                    "from_date": from_ts[:10],
                    "to_date": to_ts[:10],
                    "from_ddmmyyyy": _ddmmyyyy(from_ts),
                    "to_ddmmyyyy": _ddmmyyyy(to_ts),
                    "from_ddmmyyyy_dot": _ddmmyyyy(from_ts, "."),
                    "to_ddmmyyyy_dot": _ddmmyyyy(to_ts, "."),
                }
            )
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
        if request.request_type == "macro_series" and (payload.get("endpoint") or payload.get("path")):
            return query
        if request.provider in {"moex_iss", "moex_fast"} and request.request_type in {"market_data", "trades", "orderbook"}:
            if len(request.instrument_ids) != 1:
                raise ProviderNormalizationError(f"{request.provider} {request.request_type} requires exactly one instrument_id")
            if not (payload.get("secid") or payload.get("security") or payload.get("ticker")):
                raise ProviderNormalizationError(f"{request.provider} {request.request_type} requires explicit payload.secid")
            if not (payload.get("board_id") or payload.get("board")):
                raise ProviderNormalizationError(f"{request.provider} {request.request_type} requires explicit payload.board_id")
            time_range = payload.get("time_range")
            if request.request_type in {"market_data", "trades"} and not (
                isinstance(time_range, Mapping) and time_range.get("from_ts") and time_range.get("to_ts")
            ):
                raise ProviderNormalizationError(f"{request.provider} {request.request_type} requires explicit time_range.from_ts/to_ts")
            if request.request_type == "market_data" and not (payload.get("timeframe") or _first(payload.get("timeframes"))):
                raise ProviderNormalizationError(f"{request.provider} market_data requires explicit payload.timeframe")
            return query
        if request.instrument_ids:
            query.setdefault("instrument_ids", ",".join(request.instrument_ids))
            query.setdefault("securities", ",".join(_strip_moex_prefix(item) for item in request.instrument_ids))
        if request.universe_id:
            query.setdefault("universe_id", request.universe_id)
        if include_payload:
            for key, value in payload.items():
                if key in {
                    "path",
                    "endpoint",
                    "method",
                    "body",
                    "json",
                    "headers",
                    "query_params",
                    "source_url",
                    "series_name",
                    "unit",
                    "confidence_score",
                    "quality_flags",
                    "macro_refs",
                    "index_refs",
                    "sector_refs",
                    "event_refs",
                    "windows",
                }:
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

    def _encoded_query(self, query: Mapping[str, str], separator: str = "?") -> str:
        clean_query = {key: value for key, value in query.items() if value not in {"", "None"}}
        if not clean_query:
            return ""
        return separator + urlencode(clean_query)


def normalize_provider_response(
    request: ExternalRequest,
    provider_response: ProviderHttpResponse,
) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
    if request.provider == "arena_go":
        return _normalize_arena_go_response(request, provider_response)
    if request.provider == "polza_ai":
        return _normalize_polza_ai_response(request, provider_response)
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
    request: ExternalRequest,
    provider_response: ProviderHttpResponse,
) -> tuple[str, Mapping[str, Any], tuple[str, ...], tuple[str, ...]]:
    status = _generic_status(provider_response.status_code)
    body = _mapping_body(provider_response.body)
    if status != "success":
        return status, body, (), tuple(_response_errors(body))
    if request.request_type == "models":
        models = body.get("data") or body.get("models") or body.get("items")
        if not isinstance(models, list):
            return "failed", body, (), ("models_output_missing",)
        return "success", {"provider": "polza_ai", "request_type": "models", "models": models}, (), ()
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
    for key in ("items", "data", "results", "documents", "points"):
        value = body.get(key)
        if isinstance(value, list):
            return [_coerce_item(item) for item in value]
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


def _parse_json_body(
    raw_body: bytes,
    headers: Mapping[str, str] | None = None,
) -> Mapping[str, Any] | list[Any] | str | None:
    if not raw_body:
        return None
    text = _decode_body(raw_body, headers)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cbr = _parse_cbr_xml_body(text)
        if cbr is not None:
            return cbr
        feed = _parse_feed_body(text)
        if feed is not None:
            return feed
        csv_payload = _parse_csv_series_body(text)
        if csv_payload is not None:
            return csv_payload
        html_payload = _parse_html_body(text)
        return html_payload if html_payload is not None else text


def _decode_body(raw_body: bytes, headers: Mapping[str, str] | None) -> str:
    charset = _charset_from_headers(headers or {})
    encodings = [charset] if charset else []
    encodings.extend(["utf-8-sig", "windows-1251"])
    for encoding in encodings:
        try:
            return raw_body.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw_body.decode("utf-8-sig", errors="replace")


def _charset_from_headers(headers: Mapping[str, str]) -> str:
    content_type = ""
    for key, value in headers.items():
        if key.lower() == "content-type":
            content_type = str(value)
            break
    for part in content_type.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "charset":
            return value.strip().strip('"')
    return ""


def _parse_feed_body(text: str) -> Mapping[str, Any] | None:
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return None
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError:
        return None

    root_name = _local_xml_name(root.tag)
    if root_name == "rss":
        channel = _first_xml_child(root, "channel")
        if channel is None:
            return None
        items = [_rss_item_payload(item) for item in _xml_children(channel, "item")]
        return {
            "format": "rss",
            "title": _xml_text(channel, "title"),
            "items": [item for item in items if item],
        }
    if root_name == "feed":
        items = [_atom_entry_payload(entry) for entry in _xml_children(root, "entry")]
        return {
            "format": "atom",
            "title": _xml_text(root, "title"),
            "items": [item for item in items if item],
        }
    return None


def _parse_cbr_xml_body(text: str) -> Mapping[str, Any] | None:
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return None
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError:
        return None
    if _local_xml_name(root.tag) != "ValCurs":
        return None
    points = []
    for record in _xml_children(root, "Record"):
        raw_date = record.attrib.get("Date") or ""
        numeric_value = _numeric_text(_xml_text(record, "Value"))
        nominal_value = _numeric_text(_xml_text(record, "Nominal")) or 1.0
        if raw_date and numeric_value is not None:
            points.append({"point_ts": _iso_date_from_ddmmyyyy(raw_date), "value": numeric_value / nominal_value})
    return {"format": "cbr_xml_dynamic", "points": points} if points else None


def _parse_csv_series_body(text: str) -> Mapping[str, Any] | None:
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    if "," not in first_line:
        return None
    reader = csv.DictReader(io.StringIO(text))
    points = []
    for row in reader:
        date_value = row.get("DATE") or row.get("date") or row.get("Date")
        numeric_value = _numeric_text(row.get("VALUE") or row.get("value") or row.get("Value"))
        if date_value and numeric_value is not None:
            points.append({"point_ts": f"{str(date_value)[:10]}T00:00:00Z", "value": numeric_value})
    return {"format": "csv_series", "points": points} if points else None


def _rss_item_payload(item: ET.Element) -> Mapping[str, Any]:
    return {
        "title": _xml_text(item, "title"),
        "url": _xml_text(item, "link"),
        "body": _xml_text(item, "description"),
        "published_at": _xml_text(item, "pubDate"),
        "source_ref": _xml_text(item, "guid") or _xml_text(item, "link"),
        "source": _xml_text(item, "source"),
    }


def _atom_entry_payload(entry: ET.Element) -> Mapping[str, Any]:
    return {
        "title": _xml_text(entry, "title"),
        "url": _atom_link(entry),
        "body": _xml_text(entry, "summary") or _xml_text(entry, "content"),
        "published_at": _xml_text(entry, "published") or _xml_text(entry, "updated"),
        "source_ref": _xml_text(entry, "id") or _atom_link(entry),
    }


def _xml_text(parent: ET.Element, name: str) -> str:
    child = _first_xml_child(parent, name)
    if child is None or child.text is None:
        return ""
    return " ".join(child.text.split())


def _atom_link(entry: ET.Element) -> str:
    for child in _xml_children(entry, "link"):
        href = child.attrib.get("href")
        if href:
            return str(href)
        if child.text:
            return " ".join(child.text.split())
    return ""


def _first_xml_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local_xml_name(child.tag) == name:
            return child
    return None


def _xml_children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _local_xml_name(child.tag) == name]


def _local_xml_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _parse_html_body(text: str) -> Mapping[str, Any] | None:
    stripped = text.lstrip()
    if not stripped.lower().startswith(("<!doctype html", "<html")):
        return None
    parser = _TextHtmlParser()
    parser.feed(stripped)
    title = parser.title.strip()
    body = " ".join(parser.text_parts)
    body = " ".join(unescape(body).split())
    if len(body) > 12000:
        body = body[:12000]
    if not title and not body:
        return None
    points = _parse_html_numeric_points(body)
    return {
        "format": "html",
        "title": title,
        "body": body,
        "points": points,
        "items": [
            {
                "title": title,
                "body": body,
            }
        ],
    }


class _TextHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
            return
        if self._skip_depth:
            return
        self.text_parts.append(text)


def _parse_html_numeric_points(body: str) -> list[Mapping[str, Any]]:
    if "0,25 0,5 0,75 1 2 3 5 7 10 15 20 30" in body:
        zcyc_points = _parse_cbr_zcyc_points(body)
        if zcyc_points:
            return zcyc_points
    points = []
    pattern = re.compile(r"(\d{2}\.\d{2}\.\d{4})\D{0,80}([+-]?\d+(?:[\s\u00a0]\d{3})*(?:[,.]\d+)?)")
    for match in pattern.finditer(body):
        value = _numeric_text(match.group(2))
        if value is not None:
            points.append({"point_ts": _iso_date_from_ddmmyyyy(match.group(1)), "value": value})
    return points[:500]


def _parse_cbr_zcyc_points(body: str) -> list[Mapping[str, Any]]:
    number = r"([+-]?\d+(?:[\s\u00a0]\d{3})*(?:[,.]\d+)?)"
    pattern = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+" + r"\s+".join(number for _ in range(12)))
    points: list[Mapping[str, Any]] = []
    maturity_keys = (
        "ofz_025y",
        "ofz_05y",
        "ofz_075y",
        "ofz_1y",
        "ofz_2y",
        "ofz_3y",
        "ofz_5y",
        "ofz_7y",
        "ofz_10y",
        "ofz_15y",
        "ofz_20y",
        "ofz_30y",
    )
    for match in pattern.finditer(body):
        values = [_numeric_text(value) for value in match.groups()[1:]]
        if any(value is None for value in values):
            continue
        point = {"point_ts": _iso_date_from_ddmmyyyy(match.group(1)), "maturity_source": "cbr_zcyc"}
        point.update({key: value for key, value in zip(maturity_keys, values, strict=False) if value is not None})
        points.append(point)
    return points[:500]


def _numeric_text(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if text.strip() in {"", "."}:
            return None
        return float(text)
    except ValueError:
        return None


def _iso_date_from_ddmmyyyy(value: str) -> str:
    parts = str(value).split(".")
    if len(parts) != 3:
        return str(value)
    return f"{parts[2]}-{parts[1]}-{parts[0]}T00:00:00Z"


def _ddmmyyyy(value: str, separator: str = "/") -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}{separator}{text[5:7]}{separator}{text[0:4]}"
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


def _moex_interval(timeframe: str) -> str:
    return {
        "1m": "1",
        "5m": "5",
        "10m": "10",
        "15m": "15",
        "1h": "60",
        "1d": "24",
        "daily": "24",
    }.get(str(timeframe), str(timeframe))


def _strip_moex_prefix(value: str) -> str:
    text = str(value or "")
    return text.split(":", 1)[1] if text.startswith("moex:") else text


def _safe_polza_model(model_id: str | None, fallback: str) -> str:
    candidate = str(model_id or "").strip()
    if not candidate or candidate in PROHIBITED_POLZA_MODELS:
        return fallback
    return candidate


def _tracking_id(headers: Mapping[str, str]) -> str:
    for key in ("x-request-id", "x-correlation-id", "x-trace-id", "request-id"):
        for header_name, value in headers.items():
            if header_name.lower() == key:
                return value
    return ""
