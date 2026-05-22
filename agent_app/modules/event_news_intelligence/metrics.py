from __future__ import annotations

import math
from typing import Mapping

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


def clip(value: float | None, lower: float = 0.0, upper: float = 1.0) -> float | None:
    if value is None:
        return None
    return min(float(upper), max(float(lower), float(value)))


def clip_required(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(float(upper), max(float(lower), float(value)))


def normalize_sentiment(value: float | None) -> float | None:
    if value is None:
        return None
    return clip((float(value) + 1.0) / 2.0, 0.0, 1.0)


def signed_score(value: float | None) -> float | None:
    if value is None:
        return None
    return clip(float(value), -1.0, 1.0)


def weighted_average(values: Mapping[str, float | None], weights: Mapping[str, float]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for name, weight in weights.items():
        value = values.get(name)
        if value is None or weight == 0:
            continue
        numerator += float(value) * float(weight)
        denominator += abs(float(weight))
    if denominator <= 0:
        return None
    return numerator / denominator


def event_confidence_score(
    source_credibility_score: float | None,
    issuer_relevance_score: float | None,
    extraction_confidence: float | None,
) -> float:
    score = weighted_average(
        {
            "source_credibility_score": source_credibility_score,
            "issuer_relevance_score": issuer_relevance_score,
            "extraction_confidence": extraction_confidence,
        },
        {
            "source_credibility_score": 0.3,
            "issuer_relevance_score": 0.4,
            "extraction_confidence": 0.3,
        },
    )
    return clip_required(0.0 if score is None else score)


def event_decay_score(event_ts: str, as_of_ts: str, half_life_seconds: int) -> float:
    if half_life_seconds <= 0:
        return 0.0
    age_seconds = max(0.0, (parse_utc_iso(as_of_ts) - parse_utc_iso(event_ts)).total_seconds())
    return clip_required(math.exp(-age_seconds / float(half_life_seconds)))


def event_reaction(return_after_event: float | None, market_return_after_event: float | None) -> float | None:
    if return_after_event is None or market_return_after_event is None:
        return None
    return float(return_after_event) - float(market_return_after_event)


def abnormal_return_after_event(actual_return: float | None, expected_beta_adjusted_return: float | None) -> float | None:
    if actual_return is None or expected_beta_adjusted_return is None:
        return None
    return float(actual_return) - float(expected_beta_adjusted_return)


def abnormal_volume_after_event(volume_after_event: float | None, average_volume_same_window: float | None) -> float | None:
    if volume_after_event is None or average_volume_same_window in (None, 0):
        return None
    return float(volume_after_event) / float(average_volume_same_window) - 1.0


def underreaction_score(
    sentiment_score: float | None,
    materiality_score: float | None,
    event_reaction_z: float | None,
) -> float | None:
    if sentiment_score is None or materiality_score is None or event_reaction_z is None:
        return None
    positive_event_strength = max(0.0, float(sentiment_score)) * clip_required(float(materiality_score))
    return clip(positive_event_strength * max(0.0, 1.0 - abs(float(event_reaction_z))))


def overreaction_score(event_reaction_z: float | None, materiality_score: float | None) -> float | None:
    if event_reaction_z is None or materiality_score is None:
        return None
    return clip(abs(float(event_reaction_z)) * (1.0 - clip_required(float(materiality_score))))
