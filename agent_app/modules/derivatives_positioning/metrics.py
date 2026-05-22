from __future__ import annotations

import math
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Iterable, Mapping, Sequence


ANNUALIZATION_DAYS = 252


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


def signed_to_unit(value: float | None, expected_abs_bound: float = 1.0) -> float | None:
    if value is None:
        return None
    bound = abs(float(expected_abs_bound)) or 1.0
    return clip((float(value) + bound) / (2.0 * bound), 0.0, 1.0)


def check_derivatives_liquidity(
    turnover: float | None,
    open_interest: float | None,
    min_turnover: float,
    min_open_interest: float,
) -> bool:
    if turnover is None or open_interest is None:
        return False
    return float(turnover) >= float(min_turnover) and float(open_interest) >= float(min_open_interest)


def compute_futures_basis(futures_price: float | None, spot_price: float | None) -> float | None:
    if futures_price is None or spot_price is None or spot_price <= 0:
        return None
    return (float(futures_price) - float(spot_price)) / float(spot_price)


def compute_basis_change(current_basis: float | None, previous_basis: float | None) -> float | None:
    if current_basis is None or previous_basis is None:
        return None
    return float(current_basis) - float(previous_basis)


def compute_open_interest_change(
    current_open_interest: float | None,
    previous_open_interest: float | None,
) -> float | None:
    if current_open_interest is None or previous_open_interest is None or previous_open_interest <= 0:
        return None
    return float(current_open_interest) / float(previous_open_interest) - 1.0


def compute_volume_oi_ratio(derivative_volume: float | None, open_interest: float | None) -> float | None:
    return safe_divide(derivative_volume, open_interest)


def compute_implied_volatility_if_available(
    provider_iv: float | None,
    *,
    option_price: float | None = None,
    spot_price: float | None = None,
    strike_price: float | None = None,
    time_to_expiry_years: float | None = None,
    risk_free_rate: float = 0.0,
    option_type: str = "call",
    dividend_yield: float = 0.0,
) -> float | None:
    if provider_iv is not None and provider_iv > 0:
        return float(provider_iv)
    return solve_black_scholes_implied_volatility(
        option_price=option_price,
        spot_price=spot_price,
        strike_price=strike_price,
        time_to_expiry_years=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        option_type=option_type,
        dividend_yield=dividend_yield,
    )


def compute_iv_rv_spread(implied_volatility: float | None, realized_volatility: float | None) -> float | None:
    if implied_volatility is None or realized_volatility is None:
        return None
    return float(implied_volatility) - float(realized_volatility)


def compute_put_call_ratio(
    put_value: float | None,
    call_value: float | None,
) -> float | None:
    return safe_divide(put_value, call_value)


def compute_options_skew(
    put_25_delta_iv: float | None,
    call_25_delta_iv: float | None,
) -> float | None:
    if put_25_delta_iv is None or call_25_delta_iv is None:
        return None
    return float(put_25_delta_iv) - float(call_25_delta_iv)


def compute_derivatives_pressure_score(
    component_z_scores: Mapping[str, float | None],
    active_weights: Mapping[str, float],
) -> float | None:
    return weighted_average(component_z_scores, active_weights)


def compute_realized_volatility(prices: Sequence[float], window: int = 20) -> float | None:
    parsed = tuple(float(price) for price in prices if price is not None and float(price) > 0)
    if len(parsed) <= window:
        return None
    returns = []
    for previous, current in zip(parsed, parsed[1:], strict=False):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    if len(returns) < window:
        return None
    return pstdev(returns[-window:]) * math.sqrt(ANNUALIZATION_DAYS)


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


def solve_black_scholes_implied_volatility(
    *,
    option_price: float | None,
    spot_price: float | None,
    strike_price: float | None,
    time_to_expiry_years: float | None,
    risk_free_rate: float = 0.0,
    option_type: str = "call",
    dividend_yield: float = 0.0,
) -> float | None:
    if (
        option_price is None
        or spot_price is None
        or strike_price is None
        or time_to_expiry_years is None
        or option_price <= 0
        or spot_price <= 0
        or strike_price <= 0
        or time_to_expiry_years <= 0
    ):
        return None

    option_kind = option_type.lower()
    if option_kind not in {"call", "put"}:
        return None

    lower = 1e-6
    upper = 5.0
    target = float(option_price)
    low_price = black_scholes_price(
        spot_price=spot_price,
        strike_price=strike_price,
        time_to_expiry_years=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        volatility=lower,
        option_type=option_kind,
        dividend_yield=dividend_yield,
    )
    high_price = black_scholes_price(
        spot_price=spot_price,
        strike_price=strike_price,
        time_to_expiry_years=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        volatility=upper,
        option_type=option_kind,
        dividend_yield=dividend_yield,
    )
    if low_price is None or high_price is None or not (low_price <= target <= high_price):
        return None

    for _ in range(80):
        mid = (lower + upper) / 2.0
        price = black_scholes_price(
            spot_price=spot_price,
            strike_price=strike_price,
            time_to_expiry_years=time_to_expiry_years,
            risk_free_rate=risk_free_rate,
            volatility=mid,
            option_type=option_kind,
            dividend_yield=dividend_yield,
        )
        if price is None:
            return None
        if abs(price - target) < 1e-8:
            return mid
        if price < target:
            lower = mid
        else:
            upper = mid
    return (lower + upper) / 2.0


def black_scholes_price(
    *,
    spot_price: float,
    strike_price: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    volatility: float,
    option_type: str,
    dividend_yield: float = 0.0,
) -> float | None:
    if spot_price <= 0 or strike_price <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
        return None
    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (
        math.log(spot_price / strike_price)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_expiry_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    discounted_spot = spot_price * math.exp(-dividend_yield * time_to_expiry_years)
    discounted_strike = strike_price * math.exp(-risk_free_rate * time_to_expiry_years)
    if option_type == "call":
        return discounted_spot * _normal_cdf(d1) - discounted_strike * _normal_cdf(d2)
    if option_type == "put":
        return discounted_strike * _normal_cdf(-d2) - discounted_spot * _normal_cdf(-d1)
    return None


def years_to_expiry(expiry_date: object, as_of_ts: str) -> float | None:
    expiry = _parse_date(expiry_date)
    as_of = _parse_date(as_of_ts)
    if expiry is None or as_of is None:
        return None
    days = (expiry - as_of).days
    if days <= 0:
        return None
    return days / 365.0


def _parse_date(value: object) -> date | None:
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


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))
