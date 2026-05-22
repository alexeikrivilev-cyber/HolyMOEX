from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Iterable, Mapping, Sequence


ANNUALIZATION_DAYS = 252


def compute_log_return_series(prices: Sequence[float]) -> tuple[float, ...]:
    returns: list[float] = []
    parsed = tuple(float(price) for price in prices)
    for previous, current in zip(parsed, parsed[1:], strict=False):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    return tuple(returns)


def compute_realized_volatility(log_returns: Sequence[float], window: int) -> float | None:
    values = _last_window(_coerce_return_series(log_returns), window)
    if values is None:
        return None
    return pstdev(values) * math.sqrt(ANNUALIZATION_DAYS)


def compute_intraday_range(high_price: float | None, low_price: float | None, previous_close: float | None) -> float | None:
    if high_price is None or low_price is None or previous_close is None:
        return None
    if previous_close <= 0 or high_price < low_price:
        return None
    return (high_price - low_price) / previous_close


def compute_atr(
    high_prices: Sequence[float],
    low_prices: Sequence[float],
    close_prices: Sequence[float],
    window: int = 14,
) -> float | None:
    if window <= 0:
        return None
    highs = tuple(float(value) for value in high_prices)
    lows = tuple(float(value) for value in low_prices)
    closes = tuple(float(value) for value in close_prices)
    if len(highs) != len(lows) or len(highs) != len(closes) or len(closes) <= window:
        return None
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]
        if previous_close <= 0 or high < low:
            continue
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    values = _last_window(true_ranges, window)
    if values is None:
        return None
    return mean(values)


def compute_downside_volatility(log_returns: Sequence[float], window: int) -> float | None:
    values = _last_window(_coerce_return_series(log_returns), window)
    if values is None:
        return None
    downside = tuple(min(value, 0.0) for value in values)
    return pstdev(downside) * math.sqrt(ANNUALIZATION_DAYS)


def compute_jump_risk(
    latest_log_return: float | None,
    historical_log_returns: Sequence[float],
    window: int,
    k: float = 3.0,
) -> float | None:
    if latest_log_return is None or k <= 0:
        return None
    values = _last_window(historical_log_returns, window)
    if values is None:
        return None
    volatility = pstdev(values)
    threshold = k * volatility
    absolute_return = abs(float(latest_log_return))
    if threshold <= 0:
        return 1.0 if absolute_return > 0 else 0.0
    if absolute_return > threshold:
        return 1.0
    return clip(absolute_return / threshold, 0.0, 1.0)


def compute_gap_open_pct(open_price: float | None, previous_close: float | None) -> float | None:
    if open_price is None or previous_close is None or previous_close <= 0:
        return None
    return open_price / previous_close - 1.0


def compute_gap_risk(abs_gap_open_pct: float | None, gap_history: Iterable[float]) -> float | None:
    if abs_gap_open_pct is None:
        return None
    return percentile_rank(abs_gap_open_pct, gap_history)


def compute_max_drawdown(prices: Sequence[float], window: int = 60) -> float | None:
    values = _last_window(prices, window)
    if values is None:
        return None
    peak = values[0]
    max_drawdown = 0.0
    for price in values:
        if price <= 0:
            return None
        peak = max(peak, price)
        if peak > 0:
            max_drawdown = min(max_drawdown, price / peak - 1.0)
    return max_drawdown


def compute_recovery_ratio(prices: Sequence[float], window: int = 60) -> float | None:
    values = _last_window(prices, window)
    if values is None:
        return None
    if any(price <= 0 for price in values):
        return None
    low_index = min(range(len(values)), key=lambda index: values[index])
    pre_drawdown_high = max(values[: low_index + 1])
    drawdown_low = values[low_index]
    current_price = values[-1]
    denominator = pre_drawdown_high - drawdown_low
    if denominator <= 0:
        return 1.0
    return clip((current_price - drawdown_low) / denominator, 0.0, 1.0)


def compute_beta_to_market(
    instrument_returns: Sequence[float],
    market_returns: Sequence[float],
    window: int | None = None,
) -> float | None:
    x_values, y_values = _aligned_windows(market_returns, instrument_returns, window)
    if x_values is None or y_values is None:
        return None
    market_variance = _population_variance(x_values)
    if market_variance <= 0:
        return None
    return _population_covariance(y_values, x_values) / market_variance


def compute_correlation(
    instrument_returns: Sequence[float],
    factor_returns: Sequence[float],
    window: int | None = None,
) -> float | None:
    x_values, y_values = _aligned_windows(instrument_returns, factor_returns, window)
    if x_values is None or y_values is None:
        return None
    x_std = pstdev(x_values)
    y_std = pstdev(y_values)
    if x_std <= 0 or y_std <= 0:
        return None
    return clip(_population_covariance(x_values, y_values) / (x_std * y_std), -1.0, 1.0)


def compute_rolling_correlations(
    instrument_returns: Sequence[float],
    factor_returns: Mapping[str, Sequence[float]],
    window: int,
) -> dict[str, float]:
    correlations: dict[str, float] = {}
    for factor_name, returns in factor_returns.items():
        value = compute_correlation(instrument_returns, returns, window)
        if value is not None:
            correlations[factor_name] = value
    return correlations


def compute_systematic_risk_share(
    instrument_returns: Sequence[float],
    factor_returns: Sequence[Sequence[float]],
    window: int | None = None,
) -> float | None:
    y_values = tuple(float(value) for value in instrument_returns)
    x_columns = [tuple(float(value) for value in factor) for factor in factor_returns]
    if not y_values or not x_columns:
        return None
    length = min(len(y_values), *(len(column) for column in x_columns))
    if window is not None:
        length = min(length, window)
    if length <= len(x_columns) + 1:
        return None
    y = y_values[-length:]
    columns = [column[-length:] for column in x_columns]
    matrix = [[1.0, *(column[row_index] for column in columns)] for row_index in range(length)]
    coefficients = _least_squares(matrix, y)
    if coefficients is None:
        return None
    fitted = tuple(sum(coef * value for coef, value in zip(coefficients, row, strict=True)) for row in matrix)
    y_mean = mean(y)
    total_sum_squares = sum((value - y_mean) ** 2 for value in y)
    if total_sum_squares <= 0:
        return None
    residual_sum_squares = sum((actual - predicted) ** 2 for actual, predicted in zip(y, fitted, strict=True))
    return clip(1.0 - residual_sum_squares / total_sum_squares, 0.0, 1.0)


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
    return min(upper, max(lower, value))


def _last_window(values: Sequence[float], window: int) -> tuple[float, ...] | None:
    if window <= 0:
        return None
    parsed = tuple(float(value) for value in values)
    if len(parsed) < window:
        return None
    return parsed[-window:]


def _coerce_return_series(values: Sequence[float]) -> tuple[float, ...]:
    parsed = tuple(float(value) for value in values)
    if len(parsed) >= 2 and all(value > 0 for value in parsed) and max(parsed) > 2.0:
        return compute_log_return_series(parsed)
    return parsed


def _aligned_windows(
    first: Sequence[float],
    second: Sequence[float],
    window: int | None,
) -> tuple[tuple[float, ...], tuple[float, ...]] | tuple[None, None]:
    length = min(len(first), len(second))
    if window is not None:
        length = min(length, window)
    if length < 2:
        return None, None
    return tuple(float(value) for value in first[-length:]), tuple(float(value) for value in second[-length:])


def _population_variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    values_mean = mean(values)
    return sum((value - values_mean) ** 2 for value in values) / len(values)


def _population_covariance(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        return 0.0
    first_mean = mean(first)
    second_mean = mean(second)
    return sum((x - first_mean) * (y - second_mean) for x, y in zip(first, second, strict=True)) / len(first)


def _least_squares(matrix: Sequence[Sequence[float]], y_values: Sequence[float]) -> tuple[float, ...] | None:
    if not matrix or len(matrix) != len(y_values):
        return None
    column_count = len(matrix[0])
    xtx = [[0.0 for _ in range(column_count)] for _ in range(column_count)]
    xty = [0.0 for _ in range(column_count)]
    for row, y_value in zip(matrix, y_values, strict=True):
        if len(row) != column_count:
            return None
        for row_index in range(column_count):
            xty[row_index] += row[row_index] * y_value
            for column_index in range(column_count):
                xtx[row_index][column_index] += row[row_index] * row[column_index]
    return _solve_linear_system(xtx, xty)


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> tuple[float, ...] | None:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row_index: abs(augmented[row_index][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        for item_index in range(column, size + 1):
            augmented[column][item_index] /= pivot_value
        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column]
            for item_index in range(column, size + 1):
                augmented[row_index][item_index] -= factor * augmented[column][item_index]
    return tuple(augmented[index][size] for index in range(size))
