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
    fills: str = "intraday",
    slippage: float = 0.1,
    fill_buffer: float = 0.0005,
) -> SimulationResult:
    """Simulate the grid strategy day by day.

    `fills="intraday"` (default): buys trigger on low<=level and fill at the level
    price; sells trigger on high>=target and fill at the target price — a lot bought
    today can also sell today. `fills="close"` is the legacy behavior: both trigger
    and fill happen at the day's close. Either way, only that day's own OHLC is used
    — no look-ahead.

    `slippage` (% per side, default 0.1) worsens every fill price. `fill_buffer`
    (fraction, default 0.0005 = 0.05%) requires a level/target to be cleared by more
    than a bare touch before it counts as triggered.
    """
    if ohlc.empty:
        raise ValueError("No price data to simulate over.")
    if fills == "intraday" and not {"High", "Low"}.issubset(ohlc.columns):
        raise ValueError("fills='intraday' requires High and Low columns in ohlc.")

    first_close = float(ohlc["Close"].iloc[0])
    grid = Grid(
        first_close=first_close, step_pct=step, target_pct=target, capital=capital,
        max_deploy_pct=max_deploy, lot_size=lot_size, mode=mode,
        dynamic_weights=dynamic_weights, anchor_mode=anchor_mode, fills=fills,
        slippage_pct=slippage, fill_buffer_pct=fill_buffer,
    )
    ledger = Ledger(symbol=symbol, stcg_rate=stcg_rate, ltcg_rate=ltcg_rate)

    cash = capital
    equity_rows = []
    buy_dates: list[object] = []

    for row in ohlc.itertuples():
        date = row.Index
        close = float(row.Close)

        cash += cash * idle_yield / 100 / 365

        grid.update_anchor(close)

        if fills == "intraday":
            buy_trigger, sell_trigger = float(row.Low), float(row.High)
        else:
            buy_trigger = sell_trigger = close

        for lot in grid.check_buy(buy_trigger, date):
            cash -= lot.lot_value * (1 + brokerage / 100)
            buy_dates.append(date)

        for sold_lot, fill_price in grid.check_sells(sell_trigger):
            proceeds = sold_lot.qty * fill_price * (1 - brokerage / 100)
            cash += proceeds
            ledger.record_sell(
                buy_date=sold_lot.buy_date, sell_date=date,
                buy_price=sold_lot.buy_price, sell_price=fill_price, qty=sold_lot.qty,
            )

        open_value = sum(l.qty * close for l in grid.open_lots.values())
        equity_rows.append({"date": date, "cash": cash, "open_value": open_value, "equity": cash + open_value})

    equity_curve = pd.DataFrame(equity_rows).set_index("date")

    return SimulationResult(
        symbol=symbol, capital=capital, trades=ledger.to_dataframe(),
        equity_curve=equity_curve, exhausted_events=grid.exhausted_events,
        buy_dates=buy_dates, ledger=ledger, grid=grid,
    )
