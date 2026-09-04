"""Gross and post-tax performance metrics, plus buy-and-hold benchmark."""
from __future__ import annotations

import pandas as pd

from .engine import SimulationResult


def _cagr(initial: float, final: float, days: int) -> float:
    if days <= 0 or initial <= 0:
        return 0.0
    years = days / 365.0
    return ((final / initial) ** (1 / years) - 1) * 100


def _max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return abs(drawdown.min()) * 100


def _longest_flat_streak(dates: pd.Index, active_dates: set) -> int:
    longest = current = 0
    for d in dates:
        if d in active_dates:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def compute_metrics(result: SimulationResult) -> dict:
    equity = result.equity_curve["equity"]
    days = (equity.index[-1] - equity.index[0]).days
    initial, final = result.capital, float(equity.iloc[-1])

    trades = result.trades
    total_tax = result.ledger.total_tax()
    tax_split = result.ledger.tax_by_class()
    final_post_tax = final - total_tax

    active_dates = set(result.buy_dates) | set(trades["sell_date"]) if not trades.empty else set(result.buy_dates)
    max_deployed_pct = (
        (trades["qty"] * trades["buy_price"]).sum() + sum(l.lot_value for l in result.grid.open_lots.values())
    )

    win_rate = (trades["gross_pnl"] > 0).mean() * 100 if not trades.empty else 0.0

    return {
        "absolute_return_pct": (final - initial) / initial * 100,
        "cagr_pct": _cagr(initial, final, days),
        "post_tax_absolute_return_pct": (final_post_tax - initial) / initial * 100,
        "post_tax_cagr_pct": _cagr(initial, final_post_tax, days),
        "max_drawdown_pct": _max_drawdown(equity),
        "max_capital_deployed_pct": min(100.0, max_deployed_pct / initial * 100) if initial else 0.0,
        "num_round_trip_trades": len(trades),
        "win_rate_pct": win_rate,
        "avg_holding_days": trades["holding_days"].mean() if not trades.empty else 0.0,
        "dead_days_pct": (1 - len(active_dates) / len(equity)) * 100 if len(equity) else 0.0,
        "longest_flat_streak_days": _longest_flat_streak(equity.index, active_dates),
        "capital_exhaustion_events": len(result.exhausted_events),
        "capital_exhaustion_depth_pct": (
            max(d for _, d in result.exhausted_events) if result.exhausted_events else None
        ),
        "total_tax": total_tax,
        "stcg_tax": tax_split["STCG"],
        "ltcg_tax": tax_split["LTCG"],
    }


def benchmark_buy_hold(ohlc: pd.DataFrame, capital: float, ltcg_rate: float) -> dict:
    closes = ohlc["Close"]
    first, last = float(closes.iloc[0]), float(closes.iloc[-1])
    days = (closes.index[-1] - closes.index[0]).days
    gross_final = capital * (last / first)
    gross_pnl = gross_final - capital
    tax = gross_pnl * ltcg_rate / 100 if gross_pnl > 0 else 0.0
    post_tax_final = gross_final - tax
    return {
        "absolute_return_pct": (gross_final - capital) / capital * 100,
        "cagr_pct": _cagr(capital, gross_final, days),
        "post_tax_absolute_return_pct": (post_tax_final - capital) / capital * 100,
        "post_tax_cagr_pct": _cagr(capital, post_tax_final, days),
        "tax": tax,
    }
