from __future__ import annotations

from statistics import median, pstdev
from typing import Iterable, Mapping


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    denominator = float(denominator)
    if denominator == 0:
        return None
    return float(numerator) / denominator


def relative_to_history(current: float | None, history: Iterable[float | None]) -> float | None:
    values = _clean_values(history)
    if current is None or len(values) < 2:
        return None
    std = pstdev(values)
    if std <= 0:
        return 0.0
    return (float(current) - median(values)) / std


def relative_to_sector(current: float | None, sector_values: Iterable[float | None]) -> float | None:
    values = _clean_values(sector_values)
    if current is None or not values:
        return None
    return float(current) - median(values)


def growth_yoy(current: float | None, previous_year: float | None) -> float | None:
    if previous_year is None or previous_year == 0:
        return None
    ratio = safe_divide(current, previous_year)
    return None if ratio is None else ratio - 1.0


def margin_change(current_margin: float | None, previous_year_margin: float | None) -> float | None:
    if current_margin is None or previous_year_margin is None:
        return None
    return float(current_margin) - float(previous_year_margin)


def zscore(value: float | None, values: Iterable[float | None]) -> float | None:
    clean_values = _clean_values(values)
    if value is None or len(clean_values) < 2:
        return None
    std = pstdev(clean_values)
    if std <= 0:
        return 0.0
    return (float(value) - sum(clean_values) / len(clean_values)) / std


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


def clip(value: float, lower: float, upper: float) -> float:
    return min(float(upper), max(float(lower), float(value)))


def z_to_unit(value: float | None) -> float | None:
    if value is None:
        return None
    return clip((float(value) + 3.0) / 6.0, 0.0, 1.0)


def _clean_values(values: Iterable[float | None]) -> tuple[float, ...]:
    return tuple(float(value) for value in values if value is not None)
