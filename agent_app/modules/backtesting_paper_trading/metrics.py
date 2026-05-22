from __future__ import annotations

from math import sqrt
from statistics import pstdev
from typing import Iterable


TRADING_DAYS_PER_YEAR = 252


def total_return(starting_equity: float | None, ending_equity: float | None) -> float:
    if starting_equity is None or ending_equity is None or starting_equity <= 0:
        return 0.0
    return float(ending_equity) / float(starting_equity) - 1.0


def annualized_return(total_ret: float | None, trading_days: int) -> float:
    if total_ret is None or trading_days <= 0:
        return 0.0
    base = 1.0 + float(total_ret)
    if base <= 0:
        return -1.0
    return base ** (TRADING_DAYS_PER_YEAR / trading_days) - 1.0


def max_drawdown(equity_curve: Iterable[float]) -> float:
    peak: float | None = None
    worst = 0.0
    for equity in equity_curve:
        value = float(equity)
        peak = value if peak is None else max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def daily_returns(equity_curve: Iterable[float]) -> tuple[float, ...]:
    values = [float(item) for item in equity_curve]
    returns: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous > 0:
            returns.append(current / previous - 1.0)
    return tuple(returns)


def volatility(returns: Iterable[float]) -> float:
    values = tuple(float(item) for item in returns)
    if len(values) < 2:
        return 0.0
    return pstdev(values) * sqrt(TRADING_DAYS_PER_YEAR)


def downside_volatility(returns: Iterable[float]) -> float:
    downside = tuple(min(0.0, float(item)) for item in returns)
    if len(downside) < 2:
        return 0.0
    return pstdev(downside) * sqrt(TRADING_DAYS_PER_YEAR)


def sharpe_ratio(annualized_ret: float | None, vol: float | None, risk_free_rate: float = 0.0) -> float:
    if annualized_ret is None or vol is None or vol <= 0:
        return 0.0
    return (float(annualized_ret) - risk_free_rate) / float(vol)


def sortino_ratio(annualized_ret: float | None, downside_vol: float | None, risk_free_rate: float = 0.0) -> float:
    if annualized_ret is None or downside_vol is None or downside_vol <= 0:
        return 0.0
    return (float(annualized_ret) - risk_free_rate) / float(downside_vol)


def turnover(trade_values: Iterable[float], equity_curve: Iterable[float]) -> float:
    equities = [float(item) for item in equity_curve]
    if not equities:
        return 0.0
    average_equity = sum(equities) / len(equities)
    if average_equity <= 0:
        return 0.0
    return sum(abs(float(value)) for value in trade_values) / average_equity


def win_rate(trade_pnls: Iterable[float]) -> float:
    values = tuple(float(item) for item in trade_pnls)
    if not values:
        return 0.0
    wins = sum(1 for value in values if value > 0)
    return wins / len(values)


def profit_factor(trade_pnls: Iterable[float]) -> float:
    gross_profit = sum(max(0.0, float(item)) for item in trade_pnls)
    gross_loss = sum(min(0.0, float(item)) for item in trade_pnls)
    if gross_loss == 0:
        return gross_profit if gross_profit > 0 else 0.0
    return gross_profit / abs(gross_loss)


def avg_slippage_bps(slippages: Iterable[float]) -> float:
    values = tuple(float(item) for item in slippages)
    if not values:
        return 0.0
    return sum(values) / len(values)


def fees_total(fees: Iterable[float]) -> float:
    return sum(float(item) for item in fees)


def capacity_estimate(
    average_daily_turnover: float | None,
    max_slippage_bps: float | None,
    model_slippage_bps: float | None,
    starting_capital: float | None,
) -> float:
    if average_daily_turnover is None or average_daily_turnover <= 0:
        return float(starting_capital or 0.0)
    if max_slippage_bps is None or max_slippage_bps <= 0:
        return float(starting_capital or 0.0)
    if model_slippage_bps is None or model_slippage_bps <= 0:
        return float(average_daily_turnover)
    return float(average_daily_turnover) * float(max_slippage_bps) / float(model_slippage_bps)


def feature_leakage_flag(leakage_detected: bool) -> int:
    return 1 if leakage_detected else 0
