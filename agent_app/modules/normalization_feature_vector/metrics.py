from __future__ import annotations

from statistics import mean, pstdev
from typing import Iterable, Sequence


def clip(value: float | None, lower: float, upper: float) -> float | None:
    if value is None:
        return None
    return min(float(upper), max(float(lower), float(value)))


def percentile_rank(value: float | None, values: Iterable[float | None]) -> float | None:
    if value is None:
        return None
    parsed = sorted(float(item) for item in values if item is not None)
    if not parsed:
        return None
    if len(parsed) == 1:
        return 1.0
    less_or_equal = sum(1 for item in parsed if item <= float(value))
    return (less_or_equal - 1) / (len(parsed) - 1)


def zscore(value: float | None, values: Iterable[float | None]) -> float | None:
    if value is None:
        return None
    parsed = tuple(float(item) for item in values if item is not None)
    if len(parsed) < 2:
        return None
    std = pstdev(parsed)
    if std <= 0:
        return 0.0
    return (float(value) - mean(parsed)) / std


def signed_to_unit(value: float | None, expected_abs_bound: float = 3.0) -> float | None:
    if value is None:
        return None
    bound = abs(float(expected_abs_bound)) or 1.0
    return clip((float(value) + bound) / (2.0 * bound), 0.0, 1.0)


def percentile_value(values: Sequence[float], percentile: float) -> float | None:
    parsed = sorted(float(item) for item in values)
    if not parsed:
        return None
    if len(parsed) == 1:
        return parsed[0]
    pct = min(1.0, max(0.0, float(percentile)))
    position = pct * (len(parsed) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(parsed) - 1)
    weight = position - lower_index
    return parsed[lower_index] * (1.0 - weight) + parsed[upper_index] * weight


def winsorize(
    value: float | None,
    values: Iterable[float | None],
    lower_percentile: float = 0.01,
    upper_percentile: float = 0.99,
) -> float | None:
    if value is None:
        return None
    parsed = tuple(float(item) for item in values if item is not None)
    if not parsed:
        return float(value)
    lower = percentile_value(parsed, lower_percentile)
    upper = percentile_value(parsed, upper_percentile)
    if lower is None or upper is None:
        return float(value)
    return clip(float(value), lower, upper)


def coverage_ratio(available_features: int, required_features: int) -> float:
    if required_features <= 0:
        return 0.0
    return min(1.0, max(0.0, float(available_features) / float(required_features)))


def fresh_feature_ratio(fresh_features: int, available_features: int) -> float:
    if available_features <= 0:
        return 0.0
    return min(1.0, max(0.0, float(fresh_features) / float(available_features)))
