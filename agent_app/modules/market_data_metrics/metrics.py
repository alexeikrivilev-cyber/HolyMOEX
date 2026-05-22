from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Iterable


def compute_returns(prices: tuple[float, ...], periods: int) -> float | None:
    if periods <= 0 or len(prices) <= periods:
        return None
    previous = prices[-1 - periods]
    current = prices[-1]
    if previous <= 0:
        return None
    return current / previous - 1.0


def compute_log_returns(prices: tuple[float, ...]) -> float | None:
    if len(prices) < 2 or prices[-2] <= 0 or prices[-1] <= 0:
        return None
    return math.log(prices[-1] / prices[-2])


def compute_intraday_return(current_price: float, session_open: float) -> float | None:
    if session_open <= 0:
        return None
    return current_price / session_open - 1.0


def compute_rolling_momentum(prices: tuple[float, ...], periods: int) -> float | None:
    returns = _rolling_returns(prices, periods)
    if len(returns) < 2:
        return None
    return zscore(returns[-1], returns)


def compute_momentum_acceleration(momentum_5d: float | None, momentum_20d: float | None) -> float | None:
    if momentum_5d is None or momentum_20d is None:
        return None
    return momentum_5d - momentum_20d


def compute_trend_slope(log_prices: tuple[float, ...]) -> float | None:
    if len(log_prices) < 2:
        return None
    x_values = tuple(float(index) for index in range(len(log_prices)))
    x_mean = mean(x_values)
    y_mean = mean(log_prices)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator == 0:
        return None
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, log_prices, strict=True))
    return numerator / denominator


def compute_trend_t_stat(log_prices: tuple[float, ...]) -> float | None:
    if len(log_prices) < 3:
        return None
    slope = compute_trend_slope(log_prices)
    if slope is None:
        return None
    x_values = tuple(float(index) for index in range(len(log_prices)))
    x_mean = mean(x_values)
    y_mean = mean(log_prices)
    intercept = y_mean - slope * x_mean
    residuals = tuple(y - (intercept + slope * x) for x, y in zip(x_values, log_prices, strict=True))
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator <= 0:
        return None
    residual_variance = sum(residual ** 2 for residual in residuals) / (len(log_prices) - 2)
    standard_error = math.sqrt(residual_variance / denominator)
    if standard_error <= 0:
        return None
    return slope / standard_error


def compute_distance_to_high(current_price: float, highs: tuple[float, ...], window: int) -> float | None:
    window_highs = tuple(value for value in highs[-window:] if value > 0)
    if not window_highs:
        return None
    high_value = max(window_highs)
    if high_value <= 0:
        return None
    return current_price / high_value - 1.0


def compute_distance_to_low(current_price: float, lows: tuple[float, ...], window: int) -> float | None:
    window_lows = tuple(value for value in lows[-window:] if value > 0)
    if not window_lows:
        return None
    low_value = min(window_lows)
    if low_value <= 0:
        return None
    return current_price / low_value - 1.0


def compute_gap_open_pct(open_price: float, previous_close: float) -> float | None:
    if previous_close <= 0:
        return None
    return open_price / previous_close - 1.0


def compute_gap_persistence_score(
    current_price: float,
    open_price: float,
    previous_close: float,
) -> float | None:
    gap = open_price - previous_close
    if gap == 0:
        return 0.0
    return clip((current_price - open_price) / abs(gap), -1.0, 1.0)


def compute_vwap_deviation(price: float, vwap_value: float) -> float | None:
    if vwap_value <= 0:
        return None
    return (price - vwap_value) / vwap_value


def compute_excess_return(instrument_return: float | None, index_return: float | None) -> float | None:
    if instrument_return is None or index_return is None:
        return None
    return instrument_return - index_return


def percentile_rank(value: float, universe_values: Iterable[float]) -> float | None:
    values = sorted(float(item) for item in universe_values)
    if not values:
        return None
    if len(values) == 1:
        return 1.0
    less_or_equal = sum(1 for item in values if item <= value)
    return (less_or_equal - 1) / (len(values) - 1)


def zscore(value: float, values: tuple[float, ...]) -> float | None:
    if len(values) < 2:
        return None
    std = pstdev(values)
    if std <= 0:
        return 0.0
    return (value - mean(values)) / std


def clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _rolling_returns(prices: tuple[float, ...], periods: int) -> tuple[float, ...]:
    returns = []
    for index in range(periods, len(prices)):
        previous = prices[index - periods]
        current = prices[index]
        if previous > 0:
            returns.append(current / previous - 1.0)
    return tuple(returns)
