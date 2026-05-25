from __future__ import annotations

from math import floor


def clip(value: float | None, lower: float = 0.0, upper: float = 1.0) -> float:
    if value is None:
        return lower
    return min(float(upper), max(float(lower), float(value)))


def safe_ratio(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def portfolio_exposure_after_trade(
    current_gross_exposure: float | None,
    proposed_trade_value: float | None,
    portfolio_equity: float | None,
) -> float:
    if portfolio_equity is None or portfolio_equity <= 0:
        return 0.0
    current = float(current_gross_exposure or 0.0)
    current_value = current * float(portfolio_equity) if 0.0 <= current <= 2.0 else current
    return max(0.0, safe_ratio(current_value + float(proposed_trade_value or 0.0), portfolio_equity))


def instrument_exposure_after_trade(
    current_instrument_value: float | None,
    proposed_trade_value: float | None,
    portfolio_equity: float | None,
) -> float:
    return max(0.0, safe_ratio(float(current_instrument_value or 0.0) + float(proposed_trade_value or 0.0), portfolio_equity))


def daily_loss_usage(daily_pnl: float | None, max_daily_loss_limit: float | None) -> float:
    if max_daily_loss_limit is None or max_daily_loss_limit <= 0:
        return 1.0 if daily_pnl is not None and daily_pnl < 0 else 0.0
    return safe_ratio(abs(min(float(daily_pnl or 0.0), 0.0)), max_daily_loss_limit)


def drawdown_usage(current_drawdown: float | None, max_drawdown_limit: float | None) -> float:
    if max_drawdown_limit is None or max_drawdown_limit <= 0:
        return 1.0 if current_drawdown is not None and current_drawdown < 0 else 0.0
    return safe_ratio(abs(float(current_drawdown or 0.0)), max_drawdown_limit)


def liquidity_limit_usage(
    estimated_order_slippage_bps: float | None,
    max_allowed_slippage_bps: float | None,
) -> float:
    return safe_ratio(estimated_order_slippage_bps, max_allowed_slippage_bps)


def slippage_limit_usage(
    estimated_slippage_bps: float | None,
    order_max_slippage_bps: float | None,
) -> float:
    return safe_ratio(estimated_slippage_bps, order_max_slippage_bps)


def risk_budget_usage(used_risk_budget: float | None, total_risk_budget: float | None) -> float:
    return safe_ratio(used_risk_budget, total_risk_budget)


def floor_quantity(value: float | None, step: float = 1.0) -> float:
    if value is None or value <= 0:
        return 0.0
    quantity_step = step if step > 0 else 1.0
    return max(0.0, floor(float(value) / quantity_step) * quantity_step)


def quantity_from_value(order_value: float | None, price: float | None, step: float = 1.0) -> float:
    if order_value is None or price is None or price <= 0:
        return 0.0
    return floor_quantity(float(order_value) / float(price), step)
