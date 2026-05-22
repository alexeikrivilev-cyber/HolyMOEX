from __future__ import annotations

from datetime import date, datetime
from statistics import median
from typing import Iterable, Mapping


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    denominator = float(denominator)
    if denominator == 0:
        return None
    return float(numerator) / denominator


def surprise(actual: float | None, expected: float | None) -> float | None:
    if actual is None or expected is None:
        return None
    expected = float(expected)
    if expected == 0:
        return None
    return (float(actual) - expected) / abs(expected)


def margin_surprise(actual: float | None, expected: float | None) -> float | None:
    if actual is None or expected is None:
        return None
    return float(actual) - float(expected)


def expected_dividend_yield(
    expected_dividend_per_share: float | None,
    price: float | None,
) -> float | None:
    return safe_divide(expected_dividend_per_share, price)


def dividend_surprise(
    announced_dividend: float | None,
    expected_dividend: float | None,
) -> float | None:
    return surprise(announced_dividend, expected_dividend)


def days_to_record_date(as_of_date: date, record_date: date | None) -> int | None:
    if record_date is None:
        return None
    return (record_date - as_of_date).days


def historical_gap_size(gap_returns: Iterable[float | None]) -> float | None:
    values = tuple(abs(float(value)) for value in gap_returns if value is not None)
    if not values:
        return None
    return median(values)


def gap_close_probability_20d(closed_within_20d: Iterable[bool | None]) -> float | None:
    values = tuple(value for value in closed_within_20d if value is not None)
    if not values:
        return None
    return sum(1 for value in values if value) / len(values)


def gap_close_speed_median(days_to_close: Iterable[int | float | None]) -> float | None:
    values = tuple(float(value) for value in days_to_close if value is not None)
    if not values:
        return None
    return median(values)


def expected_gap_risk(
    dividend_yield: float | None,
    close_probability_20d: float | None,
    volatility_adjustment: float | None = None,
) -> float | None:
    if dividend_yield is None or close_probability_20d is None:
        return None
    adjustment = 1.0 if volatility_adjustment is None else max(0.0, float(volatility_adjustment))
    return float(dividend_yield) * (1.0 - clip_required(close_probability_20d)) * adjustment


def payout_ratio(dividends_total: float | None, denominator: float | None) -> float | None:
    return safe_divide(dividends_total, denominator)


def dividend_carry_score(
    dividend_yield: float | None,
    dividend_probability: float | None,
    gap_risk: float | None,
    transaction_cost: float | None,
) -> float | None:
    if dividend_yield is None or dividend_probability is None:
        return None
    return (
        float(dividend_yield) * clip_required(dividend_probability)
        - float(gap_risk or 0.0)
        - float(transaction_cost or 0.0)
    )


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


def clip(value: float | None, lower: float = 0.0, upper: float = 1.0) -> float | None:
    if value is None:
        return None
    return min(float(upper), max(float(lower), float(value)))


def clip_required(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(float(upper), max(float(lower), float(value)))


def signed_to_unit(value: float | None, expected_abs_bound: float = 1.0) -> float | None:
    if value is None:
        return None
    bound = abs(float(expected_abs_bound)) or 1.0
    return clip((float(value) + bound) / (2.0 * bound), 0.0, 1.0)


def z_to_unit(value: float | None) -> float | None:
    if value is None:
        return None
    return clip((float(value) + 3.0) / 6.0, 0.0, 1.0)


def parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
