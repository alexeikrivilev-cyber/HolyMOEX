from __future__ import annotations

import math
from collections.abc import Mapping as AbcMapping
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    quantity: float

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(frozen=True)
class OrderBookSnapshot:
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0


@dataclass(frozen=True)
class SlippageEstimate:
    buy_slippage_bps: float | None
    sell_slippage_bps: float | None
    conservative_slippage_bps: float | None
    filled: bool


def normalize_order_book_side(levels: object, side: str) -> tuple[OrderBookLevel, ...]:
    parsed: list[OrderBookLevel] = []
    if not isinstance(levels, AbcSequence) or isinstance(levels, (str, bytes)):
        return ()
    for item in levels:
        level = _parse_level(item)
        if level is not None:
            parsed.append(level)
    reverse = side == "bid"
    return tuple(sorted(parsed, key=lambda level: level.price, reverse=reverse))


def build_order_book_snapshot(bids: object, asks: object) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        bids=normalize_order_book_side(bids, "bid"),
        asks=normalize_order_book_side(asks, "ask"),
    )


def compute_bid_ask_spread(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is None or best_ask is None:
        return None
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    mid_price = (best_ask + best_bid) / 2.0
    if mid_price <= 0:
        return None
    return (best_ask - best_bid) / mid_price * 10000.0


def compute_order_book_depth(snapshot: OrderBookSnapshot, bps_band: float) -> float | None:
    mid_price = snapshot.mid_price
    if mid_price is None or mid_price <= 0:
        return None
    bid_floor = mid_price * (1.0 - bps_band / 10000.0)
    ask_ceiling = mid_price * (1.0 + bps_band / 10000.0)
    bid_depth = sum(level.quantity for level in snapshot.bids if level.price >= bid_floor)
    ask_depth = sum(level.quantity for level in snapshot.asks if level.price <= ask_ceiling)
    return bid_depth + ask_depth


def compute_depth_notional(snapshot: OrderBookSnapshot, bps_band: float) -> tuple[float, float] | None:
    mid_price = snapshot.mid_price
    if mid_price is None or mid_price <= 0:
        return None
    bid_floor = mid_price * (1.0 - bps_band / 10000.0)
    ask_ceiling = mid_price * (1.0 + bps_band / 10000.0)
    bid_notional = sum(level.notional for level in snapshot.bids if level.price >= bid_floor)
    ask_notional = sum(level.notional for level in snapshot.asks if level.price <= ask_ceiling)
    return bid_notional, ask_notional


def estimate_slippage(snapshot: OrderBookSnapshot, notional: float) -> SlippageEstimate:
    mid_price = snapshot.mid_price
    if mid_price is None or mid_price <= 0 or notional <= 0:
        return SlippageEstimate(None, None, None, False)
    buy_average = _simulate_average_price(snapshot.asks, notional)
    sell_average = _simulate_average_price(snapshot.bids, notional)
    buy_bps = None if buy_average is None else (buy_average - mid_price) / mid_price * 10000.0
    sell_bps = None if sell_average is None else (mid_price - sell_average) / mid_price * 10000.0
    available = tuple(value for value in (buy_bps, sell_bps) if value is not None)
    conservative = max(available) if available else None
    return SlippageEstimate(
        buy_slippage_bps=buy_bps,
        sell_slippage_bps=sell_bps,
        conservative_slippage_bps=conservative,
        filled=buy_bps is not None and sell_bps is not None,
    )


def compute_amihud_illiquidity(first_price: float | None, last_price: float | None, turnover: float) -> float | None:
    if first_price is None or last_price is None or first_price <= 0 or turnover <= 0:
        return None
    return abs(last_price / first_price - 1.0) / turnover


def compute_order_book_imbalance(snapshot: OrderBookSnapshot, bps_band: float) -> float | None:
    mid_price = snapshot.mid_price
    if mid_price is None or mid_price <= 0:
        return None
    bid_floor = mid_price * (1.0 - bps_band / 10000.0)
    ask_ceiling = mid_price * (1.0 + bps_band / 10000.0)
    bid_depth = sum(level.quantity for level in snapshot.bids if level.price >= bid_floor)
    ask_depth = sum(level.quantity for level in snapshot.asks if level.price <= ask_ceiling)
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return (bid_depth - ask_depth) / total


def compute_order_flow_imbalance(
    previous_snapshot: OrderBookSnapshot | None,
    current_snapshot: OrderBookSnapshot,
    bps_band: float,
) -> float | None:
    if previous_snapshot is None:
        return None
    previous_depth = _side_depths(previous_snapshot, bps_band)
    current_depth = _side_depths(current_snapshot, bps_band)
    if previous_depth is None or current_depth is None:
        return None
    previous_bid, previous_ask = previous_depth
    current_bid, current_ask = current_depth
    buy_order_flow = max(current_bid - previous_bid, 0.0) + max(previous_ask - current_ask, 0.0)
    sell_order_flow = max(current_ask - previous_ask, 0.0) + max(previous_bid - current_bid, 0.0)
    total = buy_order_flow + sell_order_flow
    if total <= 0:
        return 0.0
    return (buy_order_flow - sell_order_flow) / total


def classify_aggressive_trade(
    price: float | None,
    best_bid: float | None,
    best_ask: float | None,
    explicit_side: str | None = None,
) -> str | None:
    side = (explicit_side or "").strip().lower()
    if side in {"buy", "b", "bid", "aggressive_buy"}:
        return "buy"
    if side in {"sell", "s", "ask", "aggressive_sell"}:
        return "sell"
    if price is None:
        return None
    if best_ask is not None and price >= best_ask:
        return "buy"
    if best_bid is not None and price <= best_bid:
        return "sell"
    return None


def compute_trade_imbalance(aggressive_buy_volume: float, aggressive_sell_volume: float, total_volume: float) -> float | None:
    if total_volume <= 0:
        return None
    return (aggressive_buy_volume - aggressive_sell_volume) / total_volume


def compute_aggressive_ratio(volume: float, total_volume: float) -> float | None:
    if total_volume <= 0:
        return None
    return volume / total_volume


def compute_quote_velocity(quote_update_count: int, window_seconds: float) -> float | None:
    if quote_update_count < 0 or window_seconds <= 0:
        return None
    return quote_update_count / window_seconds


def compute_spread_widening_flag(current_spread_bps: float | None, historical_spreads_bps: Iterable[float]) -> float | None:
    if current_spread_bps is None:
        return None
    values = tuple(float(value) for value in historical_spreads_bps if value >= 0)
    if len(values) < 2:
        return 0.0
    return 1.0 if current_spread_bps > percentile(values, 0.95) else 0.0


def percentile_rank(value: float, values: Iterable[float]) -> float | None:
    sorted_values = sorted(float(item) for item in values)
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return 1.0
    less_or_equal = sum(1 for item in sorted_values if item <= value)
    return (less_or_equal - 1) / (len(sorted_values) - 1)


def zscore(value: float, values: Iterable[float]) -> float | None:
    series = tuple(float(item) for item in values)
    if len(series) < 2:
        return None
    std = pstdev(series)
    if std <= 0:
        return 0.0
    return (value - mean(series)) / std


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


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    lower_weight = upper - position
    upper_weight = position - lower
    return sorted_values[lower] * lower_weight + sorted_values[upper] * upper_weight


def _parse_level(item: object) -> OrderBookLevel | None:
    price: object
    quantity: object
    if isinstance(item, AbcMapping):
        price = item.get("price") or item.get("p")
        quantity = item.get("quantity") or item.get("qty") or item.get("volume") or item.get("q")
    elif isinstance(item, AbcSequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
        price = item[0]
        quantity = item[1]
    else:
        return None
    try:
        parsed_price = float(price)
        parsed_quantity = float(quantity)
    except (TypeError, ValueError):
        return None
    if parsed_price <= 0 or parsed_quantity <= 0:
        return None
    return OrderBookLevel(parsed_price, parsed_quantity)


def _simulate_average_price(levels: tuple[OrderBookLevel, ...], target_notional: float) -> float | None:
    remaining_notional = target_notional
    filled_quantity = 0.0
    spent_notional = 0.0
    for level in levels:
        level_notional = level.notional
        if level_notional <= 0:
            continue
        consumed_notional = min(remaining_notional, level_notional)
        consumed_quantity = consumed_notional / level.price
        spent_notional += consumed_notional
        filled_quantity += consumed_quantity
        remaining_notional -= consumed_notional
        if remaining_notional <= 1e-9:
            break
    if remaining_notional > 1e-9 or filled_quantity <= 0:
        return None
    return spent_notional / filled_quantity


def _side_depths(snapshot: OrderBookSnapshot, bps_band: float) -> tuple[float, float] | None:
    mid_price = snapshot.mid_price
    if mid_price is None or mid_price <= 0:
        return None
    bid_floor = mid_price * (1.0 - bps_band / 10000.0)
    ask_ceiling = mid_price * (1.0 + bps_band / 10000.0)
    bid_depth = sum(level.quantity for level in snapshot.bids if level.price >= bid_floor)
    ask_depth = sum(level.quantity for level in snapshot.asks if level.price <= ask_ceiling)
    return bid_depth, ask_depth
