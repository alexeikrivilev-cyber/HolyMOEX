from __future__ import annotations

from typing import Iterable, Mapping


def position_market_value(quantity: float | None, market_price: float | None) -> float:
    if quantity is None or market_price is None:
        return 0.0
    return float(quantity) * float(market_price)


def equity_value(cash: float | None, position_values: Iterable[float]) -> float:
    return float(cash or 0.0) + sum(float(value) for value in position_values)


def gross_exposure(position_values: Iterable[float], equity: float | None) -> float:
    if equity is None or equity <= 0:
        return 0.0
    return sum(abs(float(value)) for value in position_values) / float(equity)


def net_exposure(position_values: Iterable[float], equity: float | None) -> float:
    if equity is None or equity <= 0:
        return 0.0
    return sum(float(value) for value in position_values) / float(equity)


def instrument_exposure(position_value: float | None, equity: float | None) -> float:
    if position_value is None or equity is None or equity <= 0:
        return 0.0
    return float(position_value) / float(equity)


def sector_exposure(position_payloads: Iterable[Mapping[str, object]], equity: float | None) -> dict[str, float]:
    if equity is None or equity <= 0:
        return {}
    totals: dict[str, float] = {}
    for payload in position_payloads:
        sector = str(payload.get("sector") or "").strip()
        if not sector:
            continue
        value = _float(payload.get("market_value")) or 0.0
        totals[sector] = totals.get(sector, 0.0) + value
    return {sector: value / float(equity) for sector, value in sorted(totals.items())}


def unrealized_pnl(quantity: float | None, average_price: float | None, market_price: float | None) -> float:
    if quantity is None or average_price is None or market_price is None:
        return 0.0
    if quantity >= 0:
        return (float(market_price) - float(average_price)) * float(quantity)
    return (float(average_price) - float(market_price)) * abs(float(quantity))


def daily_pnl(current_equity: float | None, start_of_day_equity: float | None) -> float:
    if current_equity is None or start_of_day_equity is None:
        return 0.0
    return float(current_equity) - float(start_of_day_equity)


def drawdown(equity: float | None, rolling_peak_equity: float | None) -> float:
    if equity is None or rolling_peak_equity is None or rolling_peak_equity <= 0:
        return 0.0
    return float(equity) / float(rolling_peak_equity) - 1.0


def available_risk_budget(total_risk_budget: float | None, used_risk_budget: float | None) -> float:
    return max(0.0, float(total_risk_budget or 0.0) - float(used_risk_budget or 0.0))


def apply_fill_to_position(
    current_quantity: float,
    current_average_price: float | None,
    fill_side: str,
    fill_quantity: float,
    fill_price: float,
    fees: float,
) -> tuple[float, float | None, float]:
    if fill_quantity <= 0 or fill_price <= 0:
        return current_quantity, current_average_price, 0.0

    average = float(current_average_price or fill_price)
    if fill_side == "buy":
        return _apply_buy(current_quantity, average, fill_quantity, fill_price, fees)
    if fill_side == "sell":
        return _apply_sell(current_quantity, average, fill_quantity, fill_price, fees)
    return current_quantity, current_average_price, 0.0


def _apply_buy(
    current_quantity: float,
    average_price: float,
    fill_quantity: float,
    fill_price: float,
    fees: float,
) -> tuple[float, float | None, float]:
    if current_quantity >= 0:
        new_quantity = current_quantity + fill_quantity
        new_average = ((current_quantity * average_price) + (fill_quantity * fill_price)) / new_quantity
        return new_quantity, new_average, 0.0

    cover_quantity = min(abs(current_quantity), fill_quantity)
    realized = (average_price - fill_price) * cover_quantity - fees
    remaining_short = abs(current_quantity) - cover_quantity
    new_long = fill_quantity - cover_quantity
    if remaining_short > 0:
        return -remaining_short, average_price, realized
    if new_long > 0:
        return new_long, fill_price, realized
    return 0.0, None, realized


def _apply_sell(
    current_quantity: float,
    average_price: float,
    fill_quantity: float,
    fill_price: float,
    fees: float,
) -> tuple[float, float | None, float]:
    if current_quantity <= 0:
        new_short_abs = abs(current_quantity) + fill_quantity
        new_average = ((abs(current_quantity) * average_price) + (fill_quantity * fill_price)) / new_short_abs
        return -new_short_abs, new_average, 0.0

    close_quantity = min(current_quantity, fill_quantity)
    realized = (fill_price - average_price) * close_quantity - fees
    remaining_long = current_quantity - close_quantity
    new_short = fill_quantity - close_quantity
    if remaining_long > 0:
        return remaining_long, average_price, realized
    if new_short > 0:
        return -new_short, fill_price, realized
    return 0.0, None, realized


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
