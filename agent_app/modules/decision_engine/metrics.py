from __future__ import annotations

from math import floor
from statistics import mean
from typing import Iterable, Mapping


def clip(value: float | None, lower: float = 0.0, upper: float = 1.0) -> float:
    if value is None:
        return lower
    return min(float(upper), max(float(lower), float(value)))


def signed_clip(value: float | None, bound: float = 1.0) -> float:
    if value is None:
        return 0.0
    limit = abs(float(bound)) or 1.0
    return min(limit, max(-limit, float(value)))


def directional_feature_score(direction: str, normalized_feature: float, *, centered: bool = True) -> float:
    if not centered:
        value = signed_clip(normalized_feature)
        if direction == "negative":
            return -value
        if direction == "nonlinear":
            return value if value >= 0.5 else -value
        return value
    value = clip(normalized_feature)
    centered_value = (value - 0.5) * 2.0
    if direction == "negative":
        return -centered_value
    if direction == "nonlinear":
        return abs(centered_value) if value >= 0.5 else -abs(centered_value)
    return centered_value


def feature_contribution(
    normalized_feature: float | None,
    weight: float | None,
    direction: str,
    confidence: float | None,
    *,
    centered: bool = True,
) -> float:
    return directional_feature_score(direction, normalized_feature, centered=centered) * float(weight or 0.0) * clip(confidence)


def expected_edge_score(contributions: Mapping[str, float]) -> float:
    return signed_clip(sum(float(value) for value in contributions.values()))


def weighted_average(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    numerator = 0.0
    denominator = 0.0
    for name, value in values.items():
        weight = abs(float(weights.get(name, 0.0)))
        if weight <= 0:
            continue
        numerator += clip(value) * weight
        denominator += weight
    if denominator <= 0:
        return 0.0
    return clip(numerator / denominator)


def decision_confidence_score(
    coverage_ratio: float | None,
    feature_confidences: Iterable[float | None],
    weights_profile_confidence: float | None,
) -> float:
    confidences = tuple(clip(value) for value in feature_confidences if value is not None)
    mean_confidence = mean(confidences) if confidences else 0.0
    return clip(clip(coverage_ratio) * mean_confidence * clip(weights_profile_confidence))


def threshold_margin(expected_edge: float, action_threshold: float) -> float:
    return abs(float(expected_edge)) - abs(float(action_threshold))


def target_position_pct(
    expected_edge: float,
    risk_score: float,
    max_position_pct: float,
) -> float:
    if expected_edge <= 0:
        return 0.0
    raw_target = float(expected_edge) * clip(1.0 - risk_score) * float(max_position_pct)
    return clip(raw_target, 0.0, max_position_pct)


def signed_target_position_pct(
    expected_edge: float,
    risk_score: float,
    max_position_pct: float,
) -> float:
    if expected_edge == 0:
        return 0.0
    sign = 1.0 if expected_edge > 0 else -1.0
    raw_target = abs(float(expected_edge)) * clip(1.0 - risk_score) * float(max_position_pct)
    return sign * clip(raw_target, 0.0, max_position_pct)


def target_quantity(
    *,
    target_position_value: float | None,
    latest_price: float | None,
    quantity_step: float = 1.0,
) -> float:
    if target_position_value is None or latest_price is None or latest_price <= 0:
        return 0.0
    step = float(quantity_step) if quantity_step and quantity_step > 0 else 1.0
    units = floor((float(target_position_value) / float(latest_price)) / step) * step
    return max(0.0, units)


def portfolio_concentration_risk(gross_exposure: float | None, equity: float | None) -> float:
    if gross_exposure is None:
        return 0.0
    exposure = float(gross_exposure)
    if 0.0 <= exposure <= 2.0:
        return clip(exposure)
    if equity is None or equity <= 0:
        return 0.0
    return clip(exposure / float(equity))


def data_quality_penalty(data_quality_score: float | None) -> float:
    return clip(1.0 - clip(data_quality_score))
