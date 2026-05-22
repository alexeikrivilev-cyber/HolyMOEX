from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any, Mapping


WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u0400-\u04FF]+")


def text_items_fetched(new_raw_text_items: int) -> int:
    return max(0, int(new_raw_text_items))


def duplicate_ratio(duplicate_text_items: int, fetched_text_items: int) -> float:
    if fetched_text_items <= 0:
        return 0.0
    return _clip01(duplicate_text_items / fetched_text_items)


def scheduled_discovery_coverage_ratio(
    active_instruments_with_discovery_request: int,
    active_instruments_total: int,
) -> float:
    if active_instruments_total <= 0:
        return 0.0
    return _clip01(active_instruments_with_discovery_request / active_instruments_total)


def text_search_requests_created(external_request_types: Iterable[str]) -> int:
    return sum(1 for request_type in external_request_types if request_type == "text_search")


def instrument_mapping_confidence(
    alias_match_score: float,
    ticker_match_score: float = 0.0,
    issuer_match_score: float = 0.0,
    request_context_score: float = 0.0,
) -> float:
    return _clip01(max(alias_match_score, ticker_match_score, issuer_match_score, request_context_score))


def source_credibility_score(source_type: str, source_registry: Mapping[str, float]) -> float:
    return _clip01(float(source_registry.get(source_type, source_registry.get("default", 0.5))))


def relevance_score(entity_match: float, topic_match: float, source_credibility: float) -> float:
    return _clip01(0.5 * entity_match + 0.3 * topic_match + 0.2 * source_credibility)


def routing_latency_ms(raw_text_received_at_ms: int, routing_finished_at_ms: int) -> int:
    return max(0, routing_finished_at_ms - raw_text_received_at_ms)


def content_hash(title: str, body: str) -> str:
    normalized = normalize_text(f"{title}\n{body}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    text = str(value or "").casefold().replace("\u0451", "\u0435")
    return " ".join(WORD_RE.findall(text))


def token_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def detect_language(title: str, body: str) -> str:
    text = f"{title} {body}"
    if not text.strip():
        return "unknown"
    cyrillic = sum(1 for char in text if "\u0400" <= char <= "\u04FF")
    latin = sum(1 for char in text if "a" <= char.casefold() <= "z")
    if cyrillic > latin:
        return "ru"
    if latin > 0:
        return "en"
    return "unknown"


def _tokens(value: str) -> Iterable[str]:
    return (token for token in normalize_text(value).split(" ") if token)


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
