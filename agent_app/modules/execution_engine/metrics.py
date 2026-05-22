from __future__ import annotations

from datetime import datetime
from math import floor
from typing import Iterable, Mapping


def submitted_order_count(statuses: Iterable[str]) -> int:
    accepted = {"submitted", "partially_filled", "filled"}
    return sum(1 for status in statuses if status in accepted)


def fill_ratio(filled_quantity: float | None, submitted_quantity: float | None) -> float:
    if filled_quantity is None or submitted_quantity is None or submitted_quantity <= 0:
        return 0.0
    return max(0.0, float(filled_quantity) / float(submitted_quantity))


def avg_fill_price(fills: Iterable[Mapping[str, float | int | None]]) -> float | None:
    total_value = 0.0
    total_quantity = 0.0
    for fill in fills:
        quantity = fill.get("fill_quantity")
        if quantity is None:
            quantity = fill.get("filled_quantity")
        price = fill.get("fill_price")
        if quantity is None or price is None or float(quantity) <= 0:
            continue
        total_quantity += float(quantity)
        total_value += float(price) * float(quantity)
    if total_quantity <= 0:
        return None
    return total_value / total_quantity


def slippage_bps(side: str, average_fill_price: float | None, reference_price: float | None) -> float | None:
    if average_fill_price is None or reference_price is None or reference_price <= 0:
        return None
    if side == "buy":
        return (float(average_fill_price) - float(reference_price)) / float(reference_price) * 10_000.0
    if side == "sell":
        return (float(reference_price) - float(average_fill_price)) / float(reference_price) * 10_000.0
    return None


def fee_estimate(fill_quantity: float | None, fill_price: float | None, fee_rate_bps: float | None) -> float:
    if fill_quantity is None or fill_price is None or fee_rate_bps is None:
        return 0.0
    if fill_quantity <= 0 or fill_price <= 0 or fee_rate_bps <= 0:
        return 0.0
    return float(fill_quantity) * float(fill_price) * float(fee_rate_bps) / 10_000.0


def time_to_fill_ms(submitted_at: datetime | None, last_fill_at: datetime | None) -> int | None:
    if submitted_at is None or last_fill_at is None:
        return None
    return max(0, int((last_fill_at - submitted_at).total_seconds() * 1000))


def cancelled_order_count(statuses: Iterable[str]) -> int:
    return sum(1 for status in statuses if status in {"cancelled", "expired"})


def rejected_order_count(statuses: Iterable[str]) -> int:
    return sum(1 for status in statuses if status in {"rejected", "failed"})


def partial_fill_ratio(statuses: Iterable[str]) -> float:
    status_list = list(statuses)
    submitted = submitted_order_count(status_list)
    if submitted <= 0:
        return 0.0
    partial = sum(1 for status in status_list if status == "partially_filled")
    return partial / submitted


def arena_go_quantity(order_quantity: float | None, quantity_mode: str, lot_size: int | None) -> int:
    if order_quantity is None or order_quantity <= 0:
        return 0
    if quantity_mode == "lots":
        safe_lot_size = lot_size if lot_size and lot_size > 0 else 1
        return max(0, floor(float(order_quantity) / safe_lot_size))
    return max(0, floor(float(order_quantity)))
