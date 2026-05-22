from __future__ import annotations

from datetime import date, datetime
from typing import Mapping


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    denominator = float(denominator)
    if denominator == 0:
        return None
    return float(numerator) / denominator


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


def adjustment_factor_from_prices(
    adjusted_price: float | None,
    raw_price: float | None,
) -> float | None:
    factor = safe_divide(adjusted_price, raw_price)
    if factor is None or factor <= 0:
        return None
    return factor


def dividend_adjustment_factor(
    dividend_per_share: float | None,
    reference_price: float | None,
) -> float | None:
    if dividend_per_share is None or reference_price is None:
        return None
    reference_price = float(reference_price)
    if reference_price <= 0:
        return None
    factor = (reference_price - float(dividend_per_share)) / reference_price
    if factor <= 0:
        return None
    return factor


def split_adjustment_factor(new_to_old_ratio: float | None) -> float | None:
    if new_to_old_ratio is None or new_to_old_ratio <= 0:
        return None
    return 1.0 / float(new_to_old_ratio)


def buyback_intensity(
    buyback_value_period: float | None,
    free_float_market_cap: float | None,
) -> float | None:
    return safe_divide(buyback_value_period, free_float_market_cap)


def free_float_change(
    free_float_current: float | None,
    free_float_previous: float | None,
) -> float | None:
    if free_float_current is None or free_float_previous is None:
        return None
    return float(free_float_current) - float(free_float_previous)


def additional_supply_risk_score(
    new_shares_expected: float | None,
    current_shares_outstanding: float | None,
) -> float | None:
    return safe_divide(new_shares_expected, current_shares_outstanding)


def corporate_action_pressure_score(
    buyback_value: float | None,
    additional_supply_risk: float | None,
    tradability_change_impact: float | None,
    weights: Mapping[str, float],
) -> float | None:
    return weighted_average(
        {
            "buyback_intensity": buyback_value,
            "additional_supply_risk_score": None
            if additional_supply_risk is None
            else -float(additional_supply_risk),
            "tradability_change_impact": tradability_change_impact,
        },
        weights,
    )


def parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
