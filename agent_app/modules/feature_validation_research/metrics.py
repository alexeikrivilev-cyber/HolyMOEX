from __future__ import annotations

from math import log
from statistics import mean, pstdev
from typing import Iterable, Mapping, Sequence


def clip(value: float | None, lower: float = 0.0, upper: float = 1.0) -> float:
    if value is None:
        return lower
    return min(float(upper), max(float(lower), float(value)))


def finite_values(values: Iterable[float | None]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        parsed = float(value)
        if parsed == parsed and parsed not in (float("inf"), float("-inf")):
            result.append(parsed)
    return tuple(result)


def pearson_correlation(left: Iterable[float | None], right: Iterable[float | None]) -> float | None:
    pairs = [
        (float(left_value), float(right_value))
        for left_value, right_value in zip(left, right)
        if left_value is not None and right_value is not None
    ]
    if len(pairs) < 2:
        return None
    left_values = tuple(item[0] for item in pairs)
    right_values = tuple(item[1] for item in pairs)
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum((x_value - left_mean) * (y_value - right_mean) for x_value, y_value in pairs)
    left_denominator = sum((x_value - left_mean) ** 2 for x_value in left_values) ** 0.5
    right_denominator = sum((y_value - right_mean) ** 2 for y_value in right_values) ** 0.5
    denominator = left_denominator * right_denominator
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def rank_values(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        return ()
    sorted_pairs = sorted((float(value), index) for index, value in enumerate(values))
    ranks = [0.0 for _ in values]
    position = 0
    while position < len(sorted_pairs):
        next_position = position + 1
        while next_position < len(sorted_pairs) and sorted_pairs[next_position][0] == sorted_pairs[position][0]:
            next_position += 1
        rank = (position + next_position - 1) / 2.0
        for _, original_index in sorted_pairs[position:next_position]:
            ranks[original_index] = rank
        position = next_position
    if len(ranks) == 1:
        return (1.0,)
    return tuple(rank / (len(ranks) - 1) for rank in ranks)


def spearman_correlation(left: Iterable[float | None], right: Iterable[float | None]) -> float | None:
    pairs = [
        (float(left_value), float(right_value))
        for left_value, right_value in zip(left, right)
        if left_value is not None and right_value is not None
    ]
    if len(pairs) < 2:
        return None
    return pearson_correlation(rank_values(tuple(item[0] for item in pairs)), rank_values(tuple(item[1] for item in pairs)))


def bucket_returns(
    feature_values: Sequence[float],
    forward_returns: Sequence[float],
    bucket_count: int = 5,
) -> tuple[dict[str, float], dict[str, float]]:
    if not feature_values or len(feature_values) != len(forward_returns):
        return {}, {}
    ranks = rank_values(tuple(float(value) for value in feature_values))
    buckets: dict[int, list[float]] = {index: [] for index in range(bucket_count)}
    for rank, target in zip(ranks, forward_returns):
        bucket_index = min(bucket_count - 1, int(rank * bucket_count))
        buckets[bucket_index].append(float(target))
    hit_rate = {
        f"q{index + 1}": sum(1 for value in values if value > 0) / len(values)
        for index, values in buckets.items()
        if values
    }
    returns = {
        f"q{index + 1}": mean(values)
        for index, values in buckets.items()
        if values
    }
    return hit_rate, returns


def rolling_rank_ic(
    feature_values: Sequence[float],
    forward_returns: Sequence[float],
    window_size: int | None = None,
) -> tuple[float, ...]:
    if len(feature_values) != len(forward_returns) or len(feature_values) < 3:
        return ()
    window = window_size or max(3, len(feature_values) // 5)
    if window > len(feature_values):
        return ()
    values: list[float] = []
    for start in range(0, len(feature_values) - window + 1):
        rank_ic = spearman_correlation(feature_values[start : start + window], forward_returns[start : start + window])
        if rank_ic is not None:
            values.append(rank_ic)
    return tuple(values)


def stability_score(rolling_values: Iterable[float | None]) -> float:
    values = finite_values(rolling_values)
    if not values:
        return 0.0
    average = mean(values)
    if abs(average) <= 1e-12:
        return 0.0
    if len(values) == 1:
        return 1.0
    return clip(1.0 - pstdev(values) / abs(average))


def feature_decay(ic_by_target: Mapping[str, float | None]) -> float:
    values = tuple(abs(float(value)) for value in ic_by_target.values() if value is not None)
    if not values:
        return 0.0
    initial = values[0]
    if initial <= 1e-12:
        return 0.0
    half_level = initial / 2.0
    for index, value in enumerate(values[1:], start=1):
        if value <= half_level:
            return float(index)
    return float(len(values))


def turnover_impact_from_rank_paths(rank_paths: Mapping[str, Sequence[float]]) -> float:
    changes: list[float] = []
    for path in rank_paths.values():
        parsed = tuple(float(value) for value in path)
        for previous, current in zip(parsed, parsed[1:]):
            changes.append(abs(current - previous))
    if not changes:
        return 0.0
    return clip(mean(changes))


def population_stability_index(
    baseline_values: Sequence[float],
    current_values: Sequence[float],
    bucket_count: int = 10,
) -> float:
    if len(baseline_values) < 2 or len(current_values) < 2:
        return 0.0
    baseline = sorted(float(value) for value in baseline_values)
    current = tuple(float(value) for value in current_values)
    edges = [baseline[0]]
    for index in range(1, bucket_count):
        position = int((len(baseline) - 1) * index / bucket_count)
        edges.append(baseline[position])
    edges.append(baseline[-1])
    psi = 0.0
    epsilon = 1e-6
    for lower, upper in zip(edges, edges[1:]):
        if lower == upper:
            continue
        baseline_share = sum(1 for value in baseline if lower <= value <= upper) / len(baseline)
        current_share = sum(1 for value in current if lower <= value <= upper) / len(current)
        baseline_share = max(epsilon, baseline_share)
        current_share = max(epsilon, current_share)
        psi += (current_share - baseline_share) * log(current_share / baseline_share)
    return max(0.0, psi)


def variance(values: Iterable[float | None]) -> float:
    parsed = finite_values(values)
    if len(parsed) < 2:
        return 0.0
    average = mean(parsed)
    return sum((value - average) ** 2 for value in parsed) / len(parsed)


def mean_optional(values: Iterable[float | None], default: float = 0.0) -> float:
    parsed = finite_values(values)
    return mean(parsed) if parsed else default
