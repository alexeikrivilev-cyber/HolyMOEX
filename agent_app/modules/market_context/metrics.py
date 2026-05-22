from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Iterable, Mapping, Sequence


ANNUALIZATION_DAYS = 252


def compute_return(values: Sequence[float], periods: int) -> float | None:
    if periods <= 0:
        return None
    parsed = tuple(float(value) for value in values if value is not None)
    if len(parsed) <= periods:
        return None
    previous = parsed[-periods - 1]
    current = parsed[-1]
    if previous <= 0 or current <= 0:
        return None
    return current / previous - 1.0


def compute_log_return_series(values: Sequence[float]) -> tuple[float, ...]:
    parsed = tuple(float(value) for value in values)
    returns: list[float] = []
    for previous, current in zip(parsed, parsed[1:], strict=False):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    return tuple(returns)


def compute_realized_volatility(log_returns: Sequence[float], window: int) -> float | None:
    if window <= 1:
        return None
    values = tuple(float(value) for value in log_returns)
    if len(values) < window:
        return None
    return pstdev(values[-window:]) * math.sqrt(ANNUALIZATION_DAYS)


def rolling_realized_volatility(log_returns: Sequence[float], window: int) -> tuple[float, ...]:
    values: list[float] = []
    parsed = tuple(float(value) for value in log_returns)
    for end_index in range(window, len(parsed) + 1):
        value = compute_realized_volatility(parsed[:end_index], window)
        if value is not None:
            values.append(value)
    return tuple(values)


def market_breadth(latest_returns: Mapping[str, float]) -> float | None:
    if not latest_returns:
        return None
    advancing = sum(1 for value in latest_returns.values() if value > 0)
    return advancing / len(latest_returns)


def average_pairwise_correlation(return_series: Sequence[Sequence[float]], window: int) -> float | None:
    series = [tuple(float(value) for value in item[-window:]) for item in return_series if len(item) >= window]
    correlations: list[float] = []
    for left_index, left in enumerate(series):
        for right in series[left_index + 1 :]:
            correlation = compute_correlation(left, right)
            if correlation is not None:
                correlations.append(correlation)
    if not correlations:
        return None
    return mean(correlations)


def compute_correlation(first: Sequence[float], second: Sequence[float]) -> float | None:
    length = min(len(first), len(second))
    if length < 2:
        return None
    x_values = tuple(float(value) for value in first[-length:])
    y_values = tuple(float(value) for value in second[-length:])
    x_std = pstdev(x_values)
    y_std = pstdev(y_values)
    if x_std <= 0 or y_std <= 0:
        return None
    return clip(_population_covariance(x_values, y_values) / (x_std * y_std), -1.0, 1.0)


def percentile_rank(value: float, values: Iterable[float]) -> float | None:
    parsed = sorted(float(item) for item in values)
    if not parsed:
        return None
    if len(parsed) == 1:
        return 1.0
    less_or_equal = sum(1 for item in parsed if item <= value)
    return (less_or_equal - 1) / (len(parsed) - 1)


def zscore(value: float, values: Iterable[float]) -> float | None:
    parsed = tuple(float(item) for item in values)
    if len(parsed) < 2:
        return None
    std = pstdev(parsed)
    if std <= 0:
        return 0.0
    return (float(value) - mean(parsed)) / std


def zscore_latest(values: Sequence[float]) -> float | None:
    parsed = tuple(float(value) for value in values)
    if len(parsed) < 2:
        return None
    return zscore(parsed[-1], parsed)


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
    return min(upper, max(lower, float(value)))


def z_to_unit(value: float | None) -> float | None:
    if value is None:
        return None
    return clip((float(value) + 3.0) / 6.0, 0.0, 1.0)


def classify_volatility_regime(volatility_percentile: float | None) -> str:
    if volatility_percentile is None:
        return "normal"
    if volatility_percentile < 0.25:
        return "low"
    if volatility_percentile < 0.75:
        return "normal"
    if volatility_percentile < 0.90:
        return "high"
    return "extreme"


def classify_correlation_regime(average_correlation: float | None) -> str:
    if average_correlation is None:
        return "normal"
    absolute = abs(average_correlation)
    if absolute < 0.30:
        return "low"
    if absolute < 0.70:
        return "normal"
    return "high"


def classify_liquidity_regime(turnover_percentile: float | None, stress: bool = False) -> str:
    if stress:
        return "stressed"
    if turnover_percentile is None:
        return "normal"
    if turnover_percentile < 0.20:
        return "thin"
    return "normal"


def classify_market_regime(
    *,
    market_return_z: float | None,
    market_breadth_value: float | None,
    volatility_regime: str,
) -> str:
    if market_return_z is None and market_breadth_value is None:
        return "unknown"
    if volatility_regime in {"high", "extreme"} and (
        (market_return_z is not None and market_return_z < -1.0)
        or (market_breadth_value is not None and market_breadth_value < 0.35)
    ):
        return "stress"
    if market_return_z is not None and market_return_z > 0.75 and (
        market_breadth_value is None or market_breadth_value >= 0.55
    ):
        return "trend"
    if market_return_z is not None and market_return_z > 0.25 and (
        market_breadth_value is not None and market_breadth_value >= 0.60
    ):
        return "recovery"
    return "range"


def regime_numeric_value(regime: str) -> float:
    values = {
        "unknown": 0.0,
        "range": 0.25,
        "recovery": 0.60,
        "trend": 0.75,
        "stress": 1.0,
        "low": 0.25,
        "normal": 0.50,
        "high": 0.75,
        "extreme": 1.0,
        "thin": 0.75,
        "stressed": 1.0,
    }
    return values.get(regime, 0.0)


def _population_covariance(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        return 0.0
    first_mean = mean(first)
    second_mean = mean(second)
    return sum((x - first_mean) * (y - second_mean) for x, y in zip(first, second, strict=True)) / len(first)
