"""Day-by-day simulation loop: drives the grid state machine over daily closes.

No look-ahead: each day's decisions use only that day's close and prior state.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .grid import Grid
from .ledger import Ledger


@dataclass
class SimulationResult:
    symbol: str
    capital: float
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    exhausted_events: list[tuple[object, float]]
    buy_dates: list[object]
    ledger: Ledger
    grid: Grid


def run(
    symbol: str,
    ohlc: pd.DataFrame,
    capital: float,
    step: float,
    target: float,
    lot_size: float,
    mode: str = "classic",
    dynamic_weights: list[float] | None = None,
    max_deploy: float = 100.0,
    idle_yield: float = 0.0,
    anchor_mode: str = "first_close",
    brokerage: float = 0.0,
    stcg_rate: float = 30.0,
    ltcg_rate: float = 12.5,
) -> SimulationResult:
    closes = ohlc["Close"]
    if closes.empty:
        raise ValueError("No price data to simulate over.")

    first_close = float(closes.iloc[0])
    grid = Grid(
        first_close=first_close, step_pct=step, target_pct=target, capital=capital,
        max_deploy_pct=max_deploy, lot_size=lot_size, mode=mode,
        dynamic_weights=dynamic_weights, anchor_mode=anchor_mode,
    )
    ledger = Ledger(symbol=symbol, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate)

    cash = capital
    equity_rows = []
    buy_dates: list[object] = []

    for date, close in closes.items():
        close = float(close)

        cash += cash * idle_yield / 100 / 365

        grid.update_anchor(close)

        for lot in grid.check_buy(close, date):
            cash -= lot.lot_value * (1 + brokerage / 100)
            buy_dates.append(date)

        for sold_lot in grid.check_sells(close):
            proceeds = sold_lot.qty * close * (1 - brokerage / 100)
            cash += proceeds
            ledger.record_sell(
                buy_date=sold_lot.buy_date, sell_date=date,
                buy_price=sold_lot.buy_price, sell_price=close, qty=sold_lot.qty,
            )

        open_value = sum(l.qty * close for l in grid.open_lots.values())
        equity_rows.append({"date": date, "cash": cash, "open_value": open_value, "equity": cash + open_value})

    equity_curve = pd.DataFrame(equity_rows).set_index("date")

    return SimulationResult(
        symbol=symbol, capital=capital, trades=ledger.to_dataframe(),
        equity_curve=equity_curve, exhausted_events=grid.exhausted_events,
        buy_dates=buy_dates, ledger=ledger, grid=grid,
    )
